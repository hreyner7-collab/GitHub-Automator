"""Summarize every debug/ folder into case-identification signatures.

Prints per image: shipped verdict, winning coverage, per-component p80
interior distances (min across candidates), GrabCut area ratio, and the
guard-score rows. This is the Part-2 evidence base.
"""
import glob
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBUG = os.path.join(BASE, "debug")


def main():
    rows = []
    for d in sorted(glob.glob(os.path.join(DEBUG, "*"))):
        stem = os.path.basename(d)
        gs_path = os.path.join(d, "06_guard_scores.json")
        if not os.path.exists(gs_path):
            continue
        with open(gs_path) as f:
            gs = json.load(f)
        verdict = gs.get("verdict")
        cands = gs.get("candidates", [])
        best_ok = [c for c in cands if c.get("ok")]
        ratio = "-"
        r_path = os.path.join(d, "04_grabcut_area_ratio.txt")
        if os.path.exists(r_path):
            with open(r_path) as f:
                ratio = f.read().strip()
        comp_path = os.path.join(d, "07_distance_transform_per_component.json")
        comps = []
        if os.path.exists(comp_path):
            with open(comp_path) as f:
                all_rows = json.load(f)
            p80s = [r["p80_interior_dist"] for r in all_rows]
            comps = (f"comps={len(p80s)} min_p80={min(p80s):.2f} max_p80={max(p80s):.2f}"
                     if p80s else "comps=0")
        score = next((c for c in cands if c.get("ok")), cands[0] if cands else {})
        rows.append((verdict, stem, score.get("coverage", "-"),
                     score.get("drop", "-"), ratio, comps))
    print(f"{'verdict':<8s} {'image':<52s} {'cov':>5s} {'drop':>6s} {'gc_ratio':>9s}  components")
    for verdict, stem, cov, drop, ratio, comps in rows:
        print(f"{verdict:<8s} {stem:<52s} {str(cov):>5s} {str(drop):>6s} {ratio:>9s}  {comps}")


if __name__ == "__main__":
    main()
