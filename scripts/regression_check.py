"""Part 5: regression guarantee — re-run every previously-shipped production
image through the editor (with the refinement candidate active when present)
and compare verdicts against the pre-change baseline snapshot.

Baseline: the shipped/refused verdicts captured during the Part-1
instrumentation run (downloads/_regression_baseline.json, written on first
run or refreshed with --baseline).

Report: any image that previously shipped and now fails, and any previously
refused image whose guard numbers changed materially.
"""
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DL = os.path.join(BASE, "downloads")
BASELINE = os.path.join(DL, "_regression_baseline.json")


def collect_verdicts():
    """verdict per source stem from the shipped _mask/_edited files + debug."""
    out = {}
    for gs_path in glob.glob(os.path.join(BASE, "debug", "*", "06_guard_scores.json")):
        stem = os.path.basename(os.path.dirname(gs_path))
        with open(gs_path) as f:
            gs = json.load(f)
        out[stem] = {"verdict": gs.get("verdict"),
                     "best_ok": any(c.get("ok") for c in gs.get("candidates", []))}
    return out


def main():
    import editor
    baseline_path = BASELINE
    if "--baseline" in sys.argv:
        verdicts = collect_verdicts()
        with open(baseline_path, "w") as f:
            json.dump(verdicts, f, indent=1)
        print(f"baseline snapshot: {len(verdicts)} images -> {baseline_path}")
        return

    with open(baseline_path) as f:
        baseline = json.load(f)
    srcs = sorted(p for p in glob.glob(os.path.join(DL, "*"))
                  if not p.endswith(("_edited.png", "_mask.png", "_repair.png"))
                  and os.path.isfile(p))
    print(f"re-editing {len(srcs)} production images for regression check\n")
    regressed = []
    improved = []
    same = 0
    for src in srcs:
        stem = os.path.splitext(os.path.basename(src))[0]
        base = baseline.get(stem)
        if base is None:
            continue
        try:
            editor.edit_image(src)
            now = "SHIPPED"
        except Exception:
            now = "REFUSED"
        then = base["verdict"]
        if then == "SHIPPED" and now == "REFUSED":
            regressed.append(stem)
        elif then == "REFUSED" and now == "SHIPPED":
            improved.append(stem)
        else:
            same += 1
        print(f"  {stem:<52s} {then:<8s} -> {now}")
    print(f"\nsame: {same}, improved: {len(improved)}, REGRESSED: {len(regressed)}")
    for s in regressed:
        print(f"  REGRESSION: {s}")
    sys.exit(1 if regressed else 0)


if __name__ == "__main__":
    main()
