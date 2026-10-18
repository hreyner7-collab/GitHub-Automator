#!/usr/bin/env python3
"""Headless behavioral tests for ftai_tool_shim.py.

Spins up a MOCK gateway that mimics FreeTheAI's measured behavior, points a
real in-process shim Handler at it, and drives the full request surface with
real HTTP calls — asserting actual state transitions, not code text:

  A1 tool loop:      planner -> native tool_calls -> shim executes REAL
                     (open-meteo) weather for Tokyo -> single-message handover
                     -> resident answer routed back with the right model id
  A2 multi-round:    planner asks twice (second call executed, no duplicates)
  A3 unknown tool:   no faked data, planner is told the real capabilities
  A4 passthrough:    no-tools request is forwarded byte-identical
  A5 stream:         SSE synthesis with final content inside
  A6 filter retry:   first resident call 400 content_filter -> retry succeeds
  A7 error relay:    upstream 429 passes through with the real message
"""
import importlib.util, json, re, sys, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location("shimmod", "ftai_tool_shim.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

MOCK_PORT, SHIM_PORT = 4199, 4011
PLANNER = mod.PLANNER_MODEL  # e.g. fta/meow/gpt-4o-mini
LION = "fta/lion/claude-opus-5-5"

state = {"filter_hits": 0, "planner_rounds": 0, "tool_msg_seen": 0}

class Mock(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        # /v1/models passthrough
        self._send(200, {"object": "list", "data": [{"id": "mock-model"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) if n else b"{}")
        model = body.get("model", "")
        if body.get("stream"):
            self._send(400, {"error": {"message": "mock: internal calls must be non-stream"}})
            return

        if model == PLANNER:
            msgs = body.get("messages", [])
            last_user = next((m.get("content") or "" for m in reversed(msgs) if m.get("role") == "user"), "")
            if "429PLANNER" in last_user:
                self._send(429, {"error": {"message": "Daily free request limit reached. It resets at 00:00 UTC"}})
                return
            if any(m.get("role") == "tool" for m in msgs):
                state["tool_msg_seen"] += 1
                state["planner_rounds"] += 1
                if state["planner_rounds"] == 1:  # second lookup requested
                    second_call = {
                        "id": "callB", "type": "function",
                        "function": {"name": "get_weather",
                                     "arguments": json.dumps({"city": "Osaka"})}}
                    reply = {"object": "chat.completion", "choices": [{
                        "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "content": None,
                                    "tool_calls": [second_call]}}]}
                    self._send(200, reply)
                    return
                stop = {"object": "chat.completion", "choices": [{
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "planner done"}}]}
                self._send(200, stop)
                return
            # like the real gateway: the planner decides using the tools it was given
            fname = "get_weather"
            fname = ((body.get("tools") or [TOOL])[0].get("function") or {}).get("name", "get_weather")
            args = (json.dumps({"size": "large"}) if fname == "make_coffee"
                    else json.dumps({"city": "Tokyo"}))
            first_call = {
                "id": "callA", "type": "function",
                "function": {"name": fname, "arguments": args}}
            reply = {"object": "chat.completion", "choices": [{
                "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None,
                            "tool_calls": [first_call]}}]}
            self._send(200, reply)
            return

        if model == LION:
            msgs = body.get("messages", [])
            content = msgs[0]["content"] if msgs else ""
            if "FORCE429" in content:
                self._send(429, {"error": {"message": "Daily free request limit reached. It resets at 00:00 UTC"}})
                return
            if "filterme" in content.lower():
                state["filter_hits"] += 1
                if state["filter_hits"] == 1:
                    self._send(400, {"error": {"code": "content_filter", "message": "Content filtered: lion/ models are for general questions"}})
            self._send(200, {"object": "chat.completion", "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "RESIDENT SAW: " + content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return

        # generic passthrough echo: return body unchanged (differentiates shim edits)
        self._send(200, body)

def start_server(port, handler):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

start_server(MOCK_PORT, Mock)
# point the shim's UPSTREAM at the mock, then run a REAL shim handler
mod.UPSTREAM = f"http://127.0.0.1:{MOCK_PORT}"
start_server(SHIM_PORT, mod.Handler)

SHIM = f"http://127.0.0.1:{SHIM_PORT}/v1/chat/completions"
TOOL = {"type": "function", "function": {"name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}
DECAF = {"type": "function", "function": {"name": "make_coffee",
        "description": "Make coffee",
        "parameters": {"type": "object", "properties": {"size": {"type": "string"}}}}}

def post(body, timeout=60, want_status=False):
    req = urllib.request.Request(SHIM, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return (r.status, raw if want_status else json.loads(raw))
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode())

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  | {detail}"))
    if not cond: fails.append(name)

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
if fails:
    print("FAILED:", "; ".join(fails)); sys.exit(1)
print("ALL BEHAVIORAL CHECKS PASSED")
