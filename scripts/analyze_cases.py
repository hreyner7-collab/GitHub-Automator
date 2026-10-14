"""Part-2 root-cause evidence for the three reference cases.

For each case: raw-net agreement (IoU isnet vs u2net), fragment count of the
union at work scale, GrabCut ratio, per-component p80 interior distances,
sharpness asymmetry (top vs bottom half of the shipped product core), and the
guard rows. Everything printed as numbers straight from the debug dumps plus
fresh measurement of the sources.
"""
import json
import os
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import editor  # noqa: E402

DL = os.path.join(BASE, "downloads")
DEBUG = os.path.join(BASE, "debug")

CASES = {
    "CASE1_collapse": "self-draining_sink_caddy_detergent_spong_img_9",
    "CASE2_amputation": "self-draining_sink_caddy_detergent_spong_img_14",
    "CASE3_biteout": "single-hand_bottle_cap_shooter_img_4",
}


def find_source(stem):
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        p = os.path.join(DL, stem + ext)
        if os.path.exists(p):
            return p
    return None


def iou(a, b):
    a, b = a > 127, b > 127
    inter = (a & b).sum()
    union = (a | b).sum()
    return inter / union if union else 0.0


def main():
    for tag, stem in CASES.items():
        d = os.path.join(DEBUG, stem)
        print(f"\n=== {tag}: {stem} ===")
        src = find_source(stem)
        img = cv2.imread(src, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        scale = min(1.0, editor.MASK_WORK_SIDE / max(h, w))
        work = cv2.resize(img, (int(w * scale), int(h * scale)),
                          interpolation=cv2.INTER_AREA) if scale < 1.0 else img

        # raw nets vs union fragmentation
        m1 = cv2.imread(os.path.join(d, "01_isnet_raw.png"), cv2.IMREAD_GRAYSCALE)
        m2 = cv2.imread(os.path.join(d, "02_u2net_raw.png"), cv2.IMREAD_GRAYSCALE)
        un = cv2.imread(os.path.join(d, "03_union.png"), cv2.IMREAD_GRAYSCALE)
        sal = cv2.imread(os.path.join(d, "05_salient.png"), cv2.IMREAD_GRAYSCALE)
        if m1 is not None and m2 is not None:
            print(f"  raw-net IoU(isnet,u2net): {iou(m1, m2):.3f}")
        hard = un > 127
        n, _lab, stats, _ = cv2.connectedComponentsWithStats(
            hard.astype(np.uint8), 8)
        big = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)
               if stats[i, cv2.CC_STAT_AREA] > 0.002 * hard.size]
        print(f"  union coverage {hard.mean():.3f}, islands>0.2% frame: {len(big)}, areas {sorted(big, reverse=True)[:6]}")
        if sal is not None:
            sa = sal > 0
            print(f"  salient mask: coverage {sa.mean():.3f}, "
                  f"added-over-union {float((sa & ~hard).sum()) / max(float(hard.sum()), 1):.3f}")
        r_path = os.path.join(d, "04_grabcut_area_ratio.txt")
        if os.path.exists(r_path):
            print(f"  grabcut area ratio: {open(r_path).read().strip()}")

        # per-component wire stats of the WINNING candidate (from dump)
        comp_path = os.path.join(d, "07_distance_transform_per_component.json")
        if os.path.exists(comp_path):
            rows = json.load(open(comp_path))
            from collections import defaultdict
            by_cand = defaultdict(list)
            for r in rows:
                by_cand[r["candidate"]].append(r)
            for cand, rs in by_cand.items():
                exempts = sum(1 for r in rs if r["p80_interior_dist"] < 3.0)
                print(f"  candidate '{cand}': {len(rs)} components, "
                      f"{exempts} below 3px wire cutoff, "
                      f"p80 range {min(r['p80_interior_dist'] for r in rs):.1f}-"
                      f"{max(r['p80_interior_dist'] for r in rs):.1f}")

        # guard rows
        gs = json.load(open(os.path.join(d, "06_guard_scores.json")))
        print(f"  verdict: {gs['verdict']}")
        for i, c in enumerate(gs["candidates"]):
            print(f"    cand{i}: {c}")

        # sharpness asymmetry: shipped product core, top vs bottom half
        mask_p = os.path.join(DL, stem + "_mask.png")
        edited_p = os.path.join(DL, stem + "_edited.png")
        if os.path.exists(mask_p) and os.path.exists(edited_p):
            mask = cv2.imread(mask_p, cv2.IMREAD_GRAYSCALE)
            edited = cv2.imread(edited_p, cv2.IMREAD_GRAYSCALE)
            ys, xs = np.where(mask > 127)
            if len(ys):
                y0, y1 = int(ys.min()), int(ys.max())
                ymid = (y0 + y1) // 2
                top = mask.copy(); top[ymid:] = 0
                bot = mask.copy(); bot[:ymid] = 0
                def shp(m):
                    if m.sum() < 100:
                        return float("nan")
                    return float(cv2.Laplacian(edited.astype(np.float64), cv2.CV_64F)[m > 127].var())
                print(f"  shipped core sharpness: top half {shp(top):.0f}, bottom half {shp(bot):.0f}, "
                      f"ratio {shp(top) / max(shp(bot), 1e-6):.2f}")


if __name__ == "__main__":
    main()
