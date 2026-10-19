#!/usr/bin/env python3
"""Edge behavioral tests for ftai_tool_shim.py — coverage the main suite lacks:

  E1 port-collision: starting a second shim on an occupied port must exit
                     quietly (this is what the Windows autostart cmd does)
  E2 malformed args: a planner-provided tool argument that is not valid JSON
                     must still execute (weather lookup on the raw string)
  E3 concurrency:    parallel tool-loop requests must not cross-contaminate
                     (ThreadingHTTPServer under real concurrent load)
  E4 GET passthrough: /v1/models relays upstream status and body unchanged
"""
import http.client
import json, os, re, subprocess, sys, threading, urllib.request, urllib.error

import ftai_test_support as sup
from ftai_test_support import (LION, planner_tool_call,
                               planner_tool_calls_reply, planner_stop_reply,
                               resident_reply, Checker)

MOCK_PORT, SHIM_PORT = 4299, 4013
spec = sup.load_shim(env_upstream=f"http://127.0.0.1:{MOCK_PORT}")
mod = spec
PLANNER = mod.PLANNER_MODEL

TOOL = {"type": "function", "function": {"name": "get_weather",
        "description": "Get current weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}

seen_cities = []

class EdgePolicy:
    """Scenario logic only; the HTTP envelope lives in ftai_test_support."""
    def serve_get(self):
        return 203, {"object": "list", "data": [{"id": "edge-mock"}]}

    def serve(self, model, body, raw):
        if model == PLANNER:
            msgs = body.get("messages", [])
            if any(m.get("role") == "tool" for m in msgs):
                return 200, planner_stop_reply("planner done")
            # E2: planner sends a malformed (non-JSON) argument string on purpose
            last_user = next((m.get("content") or "" for m in reversed(msgs)
                              if m.get("role") == "user"), "")
            city = last_user.split("in ")[-1].split("'")[0].strip("\"'") or "Berlin"
            seen_cities.append(city)
            return 200, planner_tool_calls_reply(
                [planner_tool_call("c1", "get_weather", "city=" + city)])
        if model == LION:
            content = body.get("messages", [{}])[0].get("content") or ""
            if "999empty" in content.lower():
                return 200, {"object": "chat.completion", "model": model, "choices": []}
            return 200, resident_reply("RESIDENT SAW: " + content, model=model, usage=True)
        return 200, body

sup.start_mock(EdgePolicy(), MOCK_PORT)
sup.start_server(mod.Handler, SHIM_PORT)

check = Checker()

# E1: second shim on the SAME port must exit 0 with the 'already in use' note
#     (uses the LIVE 4010 instance as the occupying process if up, else own)
live_up = False
try:
    c = http.client.HTTPConnection("127.0.0.1", 4010, timeout=5)
    c.request("GET", "/v1/models"); c.getresponse().close(); c.close()
    live_up = True
except Exception:
    live_up = False
victim_port = 4010 if live_up else SHIM_PORT
r = subprocess.run([sys.executable, "ftai_tool_shim.py"], capture_output=True,
                   text=True, timeout=30, env={**os.environ,
                   "FTAI_SHIM_PORT": str(victim_port)})
check("E1 second instance exits quietly (code 0)", r.returncode == 0,
      f"rc={r.returncode} err={r.stderr[:120]}")
check("E1 'already in use' message shown", "already in use" in (r.stderr or ""), r.stderr[:160])

# E2: malformed (non-JSON) tool arguments still execute the real lookup
req = urllib.request.Request(f"http://127.0.0.1:{SHIM_PORT}/v1/chat/completions",
    data=json.dumps({"model": LION, "max_tokens": 300,
        "messages": [{"role": "user", "content": "Weather in 'Cape Town' right now?"}],
        "tools": [TOOL]}).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as resp:
    out = json.loads(resp.read())
txt = (out["choices"][0]["message"] or {}).get("content") or ""
m = re.search(r"current weather in (Cape Town|Berlin)", txt)
check("E2 malformed args did not crash exec", m is not None, txt[:220])
check("E2 handover contains a real record", ("current weather in" in txt) and (" C," in txt), txt[:220])

# E3: 4 concurrent tool-loop requests, distinct cities, no cross-contamination
cities = ["Tokyo", "Oslo", "Lima", "Nairobi"]
results = [None] * 4
def one(i, city):
    data = json.dumps({"model": LION, "max_tokens": 300,
        "messages": [{"role": "user", "content": f"Weather in {city} right now?"}],
        "tools": [TOOL]}).encode()
    r = urllib.request.Request(f"http://127.0.0.1:{SHIM_PORT}/v1/chat/completions",
        data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            d = json.loads(resp.read())
        results[i] = (d["choices"][0]["message"] or {}).get("content") or ""
    except Exception as e:
        results[i] = f"ERROR {e}"

ts = [threading.Thread(target=one, args=(i, c)) for i, c in enumerate(cities)]
for t in ts: t.start()
for t in ts: t.join(120)
ok_all = all(r and "RESIDENT SAW" in r and f"weather in {cities[i].lower()}" in r.lower()
             for i, r in enumerate(results))
check("E3 all 4 concurrent requests completed", ok_all,
      " | ".join((r or "none")[:60] for r in results))
ok_no_mix = all(city in (results[i] or "") for i, city in enumerate(cities))
check("E3 no city cross-contamination", ok_no_mix,
      " | ".join((r or "none")[:80] for r in results))

# E4: GET /v1/models relays upstream status + body unchanged (upstream used 203!)
with urllib.request.urlopen(f"http://127.0.0.1:{SHIM_PORT}/v1/models", timeout=15) as resp:
    body = json.loads(resp.read())
    st = resp.status
check("E4 GET relays status+body",
      st == 203 and body == {"object": "list", "data": [{"id": "edge-mock"}]},
      f"st={st} body={json.dumps(body)[:80]}")

# E5: malformed client fields degrade instead of crashing the handler
garbage_ok = False
try:
    req = urllib.request.Request(f"http://127.0.0.1:{SHIM_PORT}/v1/chat/completions",
        data=json.dumps({"model": LION, "max_tokens": "not-a-number",
            "messages": [{"role": "user", "content": "weather in 'Cape Town' right now?"}],
            "tools": [TOOL]}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        garbage_ok = resp.status == 200
except (urllib.error.HTTPError, urllib.error.URLError, ConnectionError):
    garbage_ok = False
check("E5 bad max_tokens still completes", garbage_ok)

# E6: unreachable upstream -> footed 502 JSON from forward(), never a raise
#     (fresh interpreter: the shim's UPSTREAM seam is env-set at startup, so the
#     closed-port endpoint is plumbed in a subprocess, like E1)
_r = subprocess.run([sys.executable, "-c",
    "import ftai_tool_shim as m\n"
    "st, body, _ = m.forward('/v1/chat/completions', b'{}', 'k')\n"
    "print(st)\nprint(body.decode(), end='')\n"],
    capture_output=True, text=True, timeout=60,
    env={**os.environ, "FTAI_UPSTREAM": "http://127.0.0.1:1"})  # closed port
_lines = _r.stdout.splitlines(keepends=False)
_st = int(_lines[0])
_body = _r.stdout[len(_lines[0]) + 1:]
check("E6 transport failure -> 502 JSON", _st == 502 and "upstream unreachable" in _body,
      f"st={_st} body={_body[:90]}")

# E7: resident 200 with an unusable shape -> footed 502, not a handler crash
req = urllib.request.Request(f"http://127.0.0.1:{SHIM_PORT}/v1/chat/completions",
    data=json.dumps({"model": LION, "max_tokens": 300,
        "messages": [{"role": "user", "content": "999EMPTY weather in Tokyo right now?"}],
        "tools": [TOOL]}).encode(), headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        e7 = (resp.status, resp.read().decode()[:120])
except urllib.error.HTTPError as e:
    e7 = (e.code, e.read().decode()[:120])
check("E7 unusable completion -> 502", e7[0] == 502 and "unusable upstream" in e7[1],
      f"st={e7[0]} {e7[1]}")

print()
if check.fails:
    print("FAILED:", "; ".join(check.fails)); sys.exit(1)
print("ALL EDGE BEHAVIORAL CHECKS PASSED")
