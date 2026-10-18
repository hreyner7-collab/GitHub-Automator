#!/usr/bin/env python3
"""Behavioral tests for ftai_live_e2e_after_reset.py itself:

  R1 failure path: with the wait skipped and sleep no-op'd, running main()
     against the LIVE shim (429s) must probe, retry, record BLOCKED, and durably
     write the results file without crashing.
  R2 success path: with post() stubbed to return plausible resident answers,
     main() must compute PASS verdicts for lion+dog weather (temp extracted),
     the unknown-tool honesty check, and write an overall PASS file.
  R3 verdict math: WEAK (no temp extracted) must downgrade overall to PARTIAL.

Results are redirected to temp files so the real evidence file is untouched.
"""
import importlib.util, sys

spec = importlib.util.spec_from_file_location("runner", "ftai_live_e2e_after_reset.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  | {detail}"))
    if not cond: fails.append(name)

real_sleep = mod.time.sleep
mod.time.sleep = lambda s: None          # R1: instant retries
mod.wait_for_reset = lambda: mod.log("wait skipped (test mode)")

# ---- R1: failure path against the LIVE shim (429 everywhere)
mod.OUT = "ftai_runner_test_blocked.txt"
mod.lines = []
mod.main()
blocked = open(mod.OUT, encoding="utf-8").read()
check("R1 probed + retried 6x (1 + 5)", blocked.count("L1") >= 6, blocked[:300])
check("R1 verdict recorded BLOCKED", "quota never reset" in blocked, blocked[-200:])
check("R1 no crash, file written", "RESULT:" in blocked)

# ---- R2: success path with plausible resident answers
def fake_post_ok(body, timeout=200):
    # emulate the shim honestly: unknown tool -> its real not-available message
    tool = ((body.get("tools") or [{}])[0].get("function") or {}).get("name", "")
    if tool == "make_coffee":
        return 200, {"choices": [{"message": {"content":
            "Tool 'make_coffee' is not available in this assistant. Capabilities "
            "that DO work: weather lookup, url fetch, date/time, arithmetic."}}]}
    city = {"Tokyo": 14.2, "Paris": 18.7}.get(json_city(body), "22.0")
    return 200, {"choices": [{"message": {"content":
        f"RESIDENT SAW: Quick context: the data I have on hand says get_weather: "
        f"current weather in {json_city(body)}: {city} C, clear sky, humidity 43%, wind 9 km/h. "
        f"Given that, what's the weather in {json_city(body)} right now?"}}]}

def json_city(body):
    for m in body.get("messages", []):
        if m.get("role") == "user":
            for c in ("Tokyo", "Paris"):
                if c in (m.get("content") or ""):
                    return c
    return "Tokyo"

mod.OUT = "ftai_runner_test_pass.txt"
mod.lines = []
mod.post = fake_post_ok
mod.main()
passed = open(mod.OUT, encoding="utf-8").read()
check("R2 lion+dog verdicts PASS", passed.count("PASS") >= 2, passed[:400])
check("R2 temps extracted", "temp=14.2C" in passed and "temp=18.7C" in passed, passed[:300])
check("R2 honesty check PASS", "L4 unknown-tool honesty: HTTP 200; no-fake-data PASS" in passed, passed[-300:])
check("R2 overall PASS", passed.count("PASS") >= 4 and "PARTIAL" not in passed, passed[-200:])

# ---- R3: when the model answers without temp data, overall must be PARTIAL
def fake_post_weak(body, timeout=200):
    return 200, {"choices": [{"message": {"content":
        "RESIDENT SAW: I cannot provide real-time weather."}}]}
mod.OUT = "ftai_runner_test_weak.txt"
mod.lines = []
mod.post = fake_post_weak
mod.main()
weak = open(mod.OUT, encoding="utf-8").read()
check("R3 weak answer -> PARTIAL", "PARTIAL" in weak, weak[-220:])
check("R3 refusal documented as WEAK", "data missing in answer" in weak, weak[:300])

print()
if fails:
    print("FAILED:", "; ".join(fails)); sys.exit(1)
print("RUNNER BEHAVIORAL CHECKS PASSED")
