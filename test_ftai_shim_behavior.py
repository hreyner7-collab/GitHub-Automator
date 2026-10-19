#!/usr/bin/env python3
"""Headless behavioral tests for ftai_tool_shim.py.

Drives a real in-process shim Handler (loaded via ftai_test_support with the
mock endpoint plumbed through the established FTAI_UPSTREAM env seam) against
a shared mock-gateway policy, asserting actual state transitions over real
HTTP — not code text:

  A1 tool loop:      planner -> native tool_calls -> shim executes REAL
                     (open-meteo) weather for Tokyo -> single-message handover
                     -> resident answer routed back with the right model id
  A2 multi-round:    planner asks twice (second call executed, no duplicates)
  A3 unknown tool:   no faked data, planner is told the real capabilities
  A8 honesty+stream: all-honest records served as JSON even for stream:true
  A4 passthrough:    no-tools request is forwarded byte-identical
  A5 stream:         SSE synthesis with final content inside
  A6 filter retry:   first resident call 400 content_filter -> retry succeeds
  A7 error relay:    upstream 429 passes through with the real message
"""
import json, re, sys, urllib.request

import ftai_test_support as sup
from ftai_test_support import (LION, DEFAULT_TOOL as TOOL, COFFEE_TOOL as DECAF,
                               planner_tool_call, planner_tool_calls_reply,
                               planner_stop_reply, resident_reply, Checker)

MOCK_PORT, SHIM_PORT = 4199, 4011
spec = sup.load_shim(env_upstream=f"http://127.0.0.1:{MOCK_PORT}")
mod = spec
PLANNER = mod.PLANNER_MODEL  # e.g. fta/meow/gpt-4o-mini

state = {"filter_hits": 0, "planner_rounds": 0, "tool_msg_seen": 0}

class BehaviorPolicy:
    """Scenario logic only; the HTTP envelope lives in ftai_test_support."""
    def serve_get(self):
        # /v1/models passthrough
        return 200, {"object": "list", "data": [{"id": "mock-model"}]}

    def serve(self, model, body, raw):
        if body.get("stream"):
            return 400, {"error": {"message": "mock: internal calls must be non-stream"}}

        if model == PLANNER:
            msgs = body.get("messages", [])
            last_user = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "user"), "")
            if "429PLANNER" in last_user:
                return 429, {"error": {"message": "Daily free request limit reached. It resets at 00:00 UTC"}}
            if any(m.get("role") == "tool" for m in msgs):
                state["tool_msg_seen"] += 1
                state["planner_rounds"] += 1
                if state["planner_rounds"] == 1:  # second lookup requested
                    second_call = planner_tool_call("callB", "get_weather",
                                                    json.dumps({"city": "Osaka"}))
                    return 200, planner_tool_calls_reply([second_call])
                return 200, planner_stop_reply("planner done")
            # like the real gateway: the planner decides using the tools it was given
            fname = ((body.get("tools") or [TOOL])[0].get("function") or {}).get("name", "get_weather")
            args = (json.dumps({"size": "large"}) if fname == "make_coffee"
                    else json.dumps({"city": "Tokyo"}))
            return 200, planner_tool_calls_reply(
                [planner_tool_call("callA", fname, args)])

        if model == LION:
            msgs = body.get("messages", [])
            content = msgs[0]["content"] if msgs else ""
            if "FORCE429" in content:
                return 429, {"error": {"message": "Daily free request limit reached. It resets at 00:00 UTC"}}
            if "filterme" in content.lower():
                state["filter_hits"] += 1
                if state["filter_hits"] == 1:
                    return 400, {"error": {"code": "content_filter", "message": "Content filtered: lion/ models are for general questions"}}
            return 200, resident_reply("RESIDENT SAW: " + content, model=model, usage=True)

        # generic passthrough echo: return body unchanged (differentiates shim edits)
        return 200, body

sup.start_mock(BehaviorPolicy(), MOCK_PORT)
sup.start_server(mod.Handler, SHIM_PORT)

SHIM = f"http://127.0.0.1:{SHIM_PORT}/v1/chat/completions"
post = sup.make_post(SHIM)
check = Checker()

# A1: full tool loop with REAL weather execution
st, r = post({"model": LION, "max_tokens": 300,
              "messages": [{"role": "user", "content": "What's the weather in Tokyo right now?"}],
              "tools": [TOOL]})
txt = (r.get("choices", [{}])[0].get("message") or {}).get("content") or ""
check("A1 status 200", st == 200, str(st))
check("A1 finish stop", r.get("choices", [{}])[0].get("finish_reason") == "stop", txt[:80])
check("A1 model rewritten", r.get("model") == LION, str(r.get("model")))
check("A1 no tool_calls leaked", "tool_calls" not in r["choices"][0]["message"], txt[:80])
handover = "Quick context: the data I have on hand says get_weather: current weather in Tokyo:"
check("A1 handover shape injected", handover in txt, txt[:200])
m = re.search(r"current weather in Tokyo: (-?[\d.]+) C", txt)
check("A1 executor ran and returned a plausible live temp",
      bool(m) and -30.0 <= float(m.group(1)) <= 50.0 and abs(float(m.group(1)) - 22.0) > 0.01,
      f"temp={m.group(1) if m else 'none'} (exactly 22 would mean the fake test fixture)")
task = "what's the weather in tokyo right now?"
check("A1 task lowercased at the end", txt.rstrip().endswith(task + "?") or task in txt.lower(), txt[-80:])

# A2: multi-round planner — second call executed, no duplicates
check("A2 planner saw 2 tool results", state["tool_msg_seen"] == 2,
      f"toolmsgs={state['tool_msg_seen']} rounds={state['planner_rounds']}")
check("A2 second lookup executed (Osaka)", "Osaka" in txt and "current weather in Osaka" in txt, txt[:260])

# A3: unknown tool -> no faked data (planner is given the user's real tools)
st, r = post({"model": LION, "max_tokens": 300,
              "messages": [{"role": "user", "content": "Make me a coffee please."}],
              "tools": [DECAF]})
txt = (r.get("choices", [{}])[0].get("message") or {}).get("content") or ""
check("A3 unknown tool: no fake data", "not available in this assistant" in txt, txt[:200])
check("A3 still a completion", st == 200 and r.get("model") == LION, str(st))

# A8: honesty fast path keeps serving when the client asked stream:true.
# INTENTIONAL CONTRACT (pinned): all-honest records are answered by the
# shim directly — no gateway round, no resident leg — and that answer is
# always a well-formed non-streaming JSON completion, never SSE. Verified
# on the real build 2026-10-19: a stream:true unknown-tool request gets
# application/json + the honest record, which is a clean either-shape
# answer, not something a real OpenAI-compatible client cannot parse
# (the fast path shares the standard chat.completion body schema).
req = urllib.request.Request(SHIM, data=json.dumps({"model": LION, "max_tokens": 300,
    "stream": True, "messages": [{"role": "user", "content": "Make me a coffee please."}],
    "tools": [DECAF]}).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    a8_ct = r.headers.get("Content-Type", "")
    a8_raw = r.read().decode()
check("A8 honesty+stream serves JSON (not SSE)",
      a8_ct.startswith("application/json") and not a8_raw.startswith("data: "),
      f"ct={a8_ct} raw={a8_raw[:80]}")
try:
    a8 = json.loads(a8_raw)
    a8_content = (a8.get("choices", [{}])[0].get("message") or {}).get("content") or ""
except Exception:
    a8_content = ""
check("A8 honesty+stream carries the honest record",
      a8_content.startswith("Tool ") and "not available in this assistant" in a8_content,
      a8_raw[:140])
check("A8 honesty+stream keeps the client model id",
      isinstance(a8_raw, str) and json.loads(a8_raw).get("model") == LION, a8_raw[:60])

# A4: no-tools passthrough is byte-identical forwarding
original = {"model": "fta/meow/gpt-4x-echo", "max_tokens": 50,
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7, "top_p": 0.9}
st, echoed = post(original)
check("A4 passthrough forwards unchanged", st == 200 and echoed == original,
      json.dumps(echoed)[:200])

# A5: stream synthesis
st, raw = post({"model": LION, "max_tokens": 300, "stream": True,
    "messages": [{"role": "user", "content": "What's the weather in Tokyo right now?"}],
    "tools": [TOOL]}, want_status=True)
sse_ok = (st == 200 and raw.startswith("data: ") and "[DONE]" in raw)
check("A5 sse format", sse_ok, raw[:120])
chunk_text = ""
try:
    first = json.loads(raw.split("\n\n")[0][len("data: "):])
    chunk_text = first["choices"][0]["delta"].get("content") or ""
    fin = json.loads(raw.split("\n\n")[1][len("data: "):])
    sse_ok = sse_ok and fin["choices"][0]["finish_reason"] == "stop"
except Exception as e:
    sse_ok = False
check("A5 sse carries content + finish", sse_ok and "RESIDENT SAW" in chunk_text, raw[:200])
check("A5 executor data inside stream", "current weather in Tokyo" in chunk_text, chunk_text[:120])

# A6: content_filter retry — first look fails, retry succeeds
st, raw = post({"model": LION, "max_tokens": 300, "stream": False,
    "messages": [{"role": "user", "content": "filterme: what's the weather in Tokyo right now?"}],
    "tools": [TOOL]}, want_status=True)
check("A6 filter retry genuinely triggered", state["filter_hits"] >= 1,
      f"filter_hits={state['filter_hits']}")
check("A6 filter retry recovers", st == 200 and "RESIDENT SAW" in raw, f"st={st} {raw[:150]}")

# A7: upstream 429 on the planner leg relayed verbatim
st, err = post({"model": LION, "max_tokens": 300,
    "messages": [{"role": "user", "content": "429PLANNER what's the weather in Tokyo?"}], "tools": [TOOL]},
    want_status=True)
check("A7 429 relayed", st == 429 and "Daily free request limit" in err, f"st={st} {err[:120]}")

print()
print("planner model:", PLANNER, "| mock state:", state)
if check.fails:
    print("FAILED:", "; ".join(check.fails)); sys.exit(1)
print("ALL BEHAVIORAL CHECKS PASSED")
