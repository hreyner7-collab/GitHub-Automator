"""Measure how quick nvidia/nemotron-3-ultra-550b-a55b really is on the
FULL 'Make a file' design path (not just the OK-probe):
  1. probe  = kimi_status()                      (the 0.9s-ping path)
  2. design = design_spec(...)                   (real design call, ~8000 tok)
  3. build  = build_stl(spec, meta, out_dir)     (forge + verify + previews)
No fallback, no retries: pure measurement of one clean attempt.

Usage:  .venv/Scripts/python.exe scripts/time_nemotron.py [prompt]
"""
import sys
import time
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from generator import generate as gen  # noqa: E402

# Point the existing primary-brain plumbing at Nemotron 3 Ultra.
NEMO = "nvidia/nemotron-3-ultra-550b-a55b"
gen.BRAIN_MODEL = NEMO

PROMPT = (sys.argv[1] if len(sys.argv) > 1
          else "A desk cable dock with 5 charging slots for USB cables")
OUT = BASE / "artifacts" / "nemotime"
OUT.mkdir(parents=True, exist_ok=True)

all_t0 = time.time()

print("model   : " + NEMO)
print("prompt  : " + repr(PROMPT))
print("-" * 60)


def _retry_503(fn, tries=6, wait_s=8, label=""):
    """NIM randomly 503s 'Service temporarily overloaded'; retry past it
    (each retry is reported honestly so timing stays interpretable)."""
    for i in range(1, tries + 1):
        t0 = time.time()
        try:
            out = fn()
            return out, time.time() - t0, i
        except Exception as e:
            dt = time.time() - t0
            transient = "503" in str(e) or "overloaded" in str(e).lower()
            print("%s attempt %d: %.1fs  %s: %s"
                  % (label, i, dt, type(e).__name__, str(e)[:160]))
            if not transient or i == tries:
                raise
            time.sleep(wait_s)
    raise RuntimeError("unreachable")


# 1) probe (the path that read 0.9 s before) — retried past random 503s
def _probe():
    s = gen.kimi_status()
    if s["state"] != "ok":
        raise RuntimeError("probe: " + str(s.get("detail", ""))[:200])
    return s


status, probe_s, probe_tries = _retry_503(_probe, label="probe")
print("probe   : %6.1fs  state=%s  (clean attempts: %d)"
      % (probe_s, status["state"], probe_tries))
print("          detail=" + str(status.get("detail", ""))[:120])
if status["state"] != "ok":
    print("\nRESULT: probe failed -> full design call not attemptable")
    print("total  : %.1fs" % (time.time() - all_t0))
    sys.exit(2)

# 2) full design call (the unknown being measured) — retried past 503s
spec, design_s, design_tries = _retry_503(
    lambda: gen.design_spec(PROMPT), label="design")
meta = spec[1]
spec = spec[0]
print("design  : %6.1fs  (elapsed_s in meta: %ss; clean attempts: %d)"
      % (design_s, meta.get("elapsed_s"), design_tries))

print("          name=%r" % spec.get("name", "?"))
parts = spec.get("parts", []) if isinstance(spec, dict) else []
print("          parts=%d" % len(parts))
for p in parts[:12]:
    sh = p.get("shape", {})
    print("            - %s: op=%s type=%s"
          % (p.get("id"), p.get("op"), sh.get("type")))
    for c in p.get("also_cut", [])[:10]:
        cs = c.get("shape", {})
        print("              cut %s %s size/r=%s pos=%s"
              % (cs.get("type"), cs.get("axis", ""),
                 cs.get("size", cs.get("radius")), cs.get("pos")))
(base / "spec.json") if False else None
try:
    (OUT / "spec.json").write_text(
        json.dumps(spec, indent=2), encoding="utf-8")
    print("          spec saved -> artifacts/nemotime/spec.json")
except Exception:
    pass

# 3) forge + verify + previews
t0 = time.time()
meta["source"] = "timing-script"
try:
    stl_path, report = gen.build_stl(spec, meta, OUT, allow_repair=False)
    build_s = time.time() - t0
    print("build   : %6.1fs  (forge+verify+previews, no repair)" % build_s)
    print("          stl=%s" % stl_path.name)
    print("          verify: ok=%s watertight=%s vol=%smm3 tris=%s"
          % (report.get("ok"), report.get("watertight"),
             report.get("volume_mm3"), report.get("tris")))
    print("          warnings=%s" % (report.get("warnings"),))
except Exception as e:
    build_s = time.time() - t0
    print("build   : %6.1fs  FAILED %s: %s"
          % (build_s, type(e).__name__, str(e)[:300]))
    print("\nRESULT: spec designed in %.1fs but did not verify" % design_s)
    print("total  : %.1fs" % (time.time() - all_t0))
    sys.exit(4)

total = time.time() - all_t0
print("-" * 60)
print("NEMOTRON TIMING  probe=%.1fs  design=%.1fs  build=%.1fs  e2e=%.1fs"
      % (probe_s, design_s, build_s, total))
print("previews:")
for p in report.get("png_paths", []):
    print("  " + str(p))
