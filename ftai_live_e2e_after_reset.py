#!/usr/bin/env python3
"""Wait for the FreeTheAI daily quota reset (00:00 UTC) then run the live
end-to-end check of the shim's auto-tool loop and write results to a file.

Checks:
  L1 lion plain chat         (quota gate reached)
  L2 lion weather via shim   (planner -> real open-meteo -> resident answer)
  L3 dog  weather via shim   (same on the dog route)
  L4 unknown-tool honesty    (no faked data)
Writes plain-text + JSON results so the run's evidence survives the session.
Run:  python ftai_live_e2e_after_reset.py
"""
import json, re, sys, time, urllib.request, urllib.error, os, winreg
from ftai_executors import UNKNOWN_TOOL_MARKER
from datetime import datetime, timedelta, timezone

SHIM = "http://127.0.0.1:4010/v1/chat/completions"
LION, DOG = "fta/lion/claude-opus-5-5", "fta/dog/gpt-5.5"
TOOL = {"type": "function", "function": {"name": "get_weather",
    "description": "Get current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}
COFFEE = {"type": "function", "function": {"name": "make_coffee",
    "description": "Make coffee",
    "parameters": {"type": "object", "properties": {"size": {"type": "string"}}}}}
OUT = "ftai_live_e2e_results.txt"
lines = []

def log(s):
    line = datetime.now(timezone.utc).strftime("%H:%M:%S") + "  " + s
    lines.append(line)
    print(line, flush=True)

def post(body, timeout=200):
    req = urllib.request.Request(SHIM, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}

def wait_for_reset():
    now = datetime.now(timezone.utc)
    target = now.replace(hour=0, minute=0, second=5, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    secs = (target - now).total_seconds()
    log(f"waiting {secs/3600:.2f}h until {target.isoformat()} for quota reset")
    time.sleep(secs)

def main():
    if not (os.environ.get("FTAI_API_KEY") or os.environ.get("FTAI_CHAT_KEY")):
        log("note: FTAI_API_KEY not in this env; shim reads registry fallback itself")
    wait_for_reset()

    st, r = post({"model": LION, "max_tokens": 80,
        "messages": [{"role": "user", "content": "Write one short friendly sentence about autumn."}]})
    quota_ok = st == 200
    log(f"L1 lion plain chat: HTTP {st}" + ("" if quota_ok else f" {json.dumps(r)[:120]}"))

    if not quota_ok:  # reset might lag a few minutes; probe 5x, 2 min apart
        for i in range(5):
            time.sleep(120)
            st, r = post({"model": LION, "max_tokens": 80,
                "messages": [{"role": "user", "content": "Write one short friendly sentence about autumn."}]})
            log(f"L1 retry {i+1}: HTTP {st}")
            if st == 200:
                quota_ok = True
                break

    verdicts = {}
    if not quota_ok:
        log("RESULT: quota never reset — live E2E not run")
        verdicts["overall"] = "BLOCKED"
    else:
        for tag, model, city in [("L2 lion", LION, "Tokyo"), ("L3 dog", DOG, "Paris")]:
            st, r = post({"model": model, "max_tokens": 400,
                "messages": [{"role": "user", "content": f"What's the weather in {city} right now?"}],
                "tools": [TOOL]})
            if st != 200:
                log(f"{tag} weather: HTTP {st} {json.dumps(r)[:120]}")
                verdicts[tag] = f"FAIL {st}"
                continue
            txt = (r["choices"][0]["message"] or {}).get("content") or ""
            m = re.search(r"current weather in " + city + r".{0,24}?(-?[\d.]+) C", txt)
            # `p = None if m else ...` is correct here, not the conditional bug it
            # resembles: when m matched, the verdict goes PASS via the exact record
            # echo, so the loose temp search is deliberately skipped; it is only
            # needed for the paraphrased-reading branch of a non-echo answer.
            p = None if m else re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*C\b", txt)
            w = re.search(r"humidity|km/h|cloudy|rain|drizzle|snow|fog|wind", txt, re.I)
            if m:
                log(f"{tag} weather: PASS (record echo) — real data: ...{txt[:150]}")
                verdicts[tag] = f"PASS temp={m.group(1)}C"
            elif p and w and -45.0 <= float(p.group(1)) <= 55.0:
                log(f"{tag} weather: PASS (paraphrased live reading, temp={p.group(1)}) — ...{txt[:150]}")
                verdicts[tag] = f"PASS temp={p.group(1)}C"
            else:
                log(f"{tag} weather: no live reading in answer: REPR={txt[:170]!r}")
                verdicts[tag] = "WEAK (no live weather data in answer)"
        st, r = post({"model": LION, "max_tokens": 300,
            "messages": [{"role": "user", "content": "Make me a coffee please."}],
            "tools": [COFFEE]})
        txt = (r.get("choices", [{}])[0].get("message") or {}).get("content") or "" if st == 200 else ""
        ok = st == 200 and UNKNOWN_TOOL_MARKER in txt
        log(f"L4 unknown-tool honesty: HTTP {st}; no-fake-data {'PASS' if ok else 'FAIL'} {txt[:120]}")
        verdicts["L4"] = "PASS" if ok else "FAIL"
        verdicts["overall"] = "PASS" if all("PASS" in v for v in verdicts.values()) else "PARTIAL"
        log(f"RESULT: {verdicts}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("results written to", OUT, flush=True)

if __name__ == "__main__":
    main()
