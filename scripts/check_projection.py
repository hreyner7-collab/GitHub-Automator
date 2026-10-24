"""GATE ORACLE (honest arithmetic): turn the measured baseline and the measured
removed waits into a projected per-model time — and refuse to print a number
the evidence cannot support.

Inputs are all measured, never asserted:
  · the real per-model cost comes from a run log (fetch avg + publish avg);
  · the sleeping removed comes from scheduled_ms() over each module read from
    the working tree against the same file as committed (scripts/check_wait_floor.py).

A stated discount is applied, because a fixed wait sometimes hides real
network latency: removing the sleep does not recover every millisecond of it.
The script prints the discount so a reader can disagree with the arithmetic.

The final assertion is a honesty guard, not a success theatre: a projection of
more than 3x from wait removal alone means something is being measured wrong,
because the rest of the cost is the site uploading and answering.

  .venv/Scripts/python.exe -X utf8 scripts/check_projection.py \
      --job artifacts/auto_run_20261024-131810.json
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from check_wait_floor import scheduled_ms          # noqa: E402

DISCOUNT = 0.75      # share of a removed sleep that really becomes time saved
MODULES = ("downloader.py", "uploader.py")


def git_show(ref, path):
    out = subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
    if out.returncode:
        raise SystemExit("cannot read %s:%s" % (ref, path))
    return out.stdout.decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--against", default="HEAD")
    ap.add_argument("--models", type=int, default=20)
    a = ap.parse_args()

    d = json.load(open(os.path.join(ROOT, a.job), encoding="utf-8"))
    ph = {p["phase"]: p for p in (d.get("timings") or {}).get("phases") or []}
    if "fetch" not in ph or "publish" not in ph:
        print("FAIL: %s has no measured fetch/publish phase to project from" % a.job)
        return 1
    fetch, publish = ph["fetch"]["avg_s"], ph["publish"]["avg_s"]
    per_model = fetch + publish
    print("measured baseline (%s): fetch %.1f s + publish %.1f s = %.1f s per model"
          % (os.path.basename(a.job), fetch, publish, per_model))

    removed_ms = 0
    for m in MODULES:
        work = open(os.path.join(ROOT, m), encoding="utf-8").read()
        base = scheduled_ms(git_show(a.against, m))
        now = scheduled_ms(work)
        removed_ms += max(0, base - now)
        print("  %-14s %6d ms scheduled -> %6d ms (%6d ms less)" % (m, base, now, base - now))
    if removed_ms <= 0:
        print("FAIL: no scheduled waiting was actually removed")
        return 1

    saved = removed_ms / 1000.0 * DISCOUNT
    projected = per_model - saved
    if projected <= 0:
        print("FAIL: projection says a model costs %.1f s, which is impossible" % projected)
        return 1
    speedup = per_model / projected
    print("sleeping removed %.1f s x %.2f discount = %.1f s saved -> %.1f s per model"
          % (removed_ms / 1000.0, DISCOUNT, saved, projected))
    print("projected speedup %.2fx; %d models %.0f s -> %.0f s (excluding the %.0f s "
          "of discovery and the per-model work the site itself spends)"
          % (speedup, a.models, a.models * per_model, a.models * projected,
             (ph.get("discovery") or {}).get("total_s", 0)))
    if speedup > 3.0:
        print("FAIL: a %.2fx claim from removing sleeps alone is not credible; "
              "state what else changed or lower the claim" % speedup)
        return 1
    if speedup < 1.2:
        print("FAIL: only %.2fx — the waits this change targeted are still there" % speedup)
        return 1
    print("projection verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
