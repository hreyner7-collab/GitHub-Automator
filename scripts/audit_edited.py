"""Audit every *_edited.png in downloads/ for shipped defects:
- residual VERIFIED text bands inside the opaque product core
- skin-tone (hand) coverage inside the opaque product core
- a rendered contact sheet of the suspects for eyeballing
Read-only; prints one line per image + totals.
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DL = os.path.join(BASE, "downloads")
sys.path.insert(0, BASE)
import editor  # noqa: E402


def skin_fraction(img: np.ndarray, opaque: np.ndarray) -> float:
    core = opaque > 0
    if core.sum() < 100:
        return 0.0
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    skin = cv2.inRange(ycrcb, (40, 135, 85), (255, 180, 135)) > 0
    return float(skin[core].mean())


def main():
    rows = []
    for edited in sorted(glob.glob(os.path.join(DL, "*_edited.png"))):
        src_path = edited.replace("_edited.png", "")
        out = cv2.imread(edited, cv2.IMREAD_COLOR)
        mask = cv2.imread(edited.replace("_edited.png", "_mask.png"),
                          cv2.IMREAD_GRAYSCALE)
        if out is None:
            rows.append((edited, None, None, "unreadable"))
            continue
        opaque = mask if mask is not None else (np.full(out.shape[:2], 255, np.uint8))
        text = editor._residual_text_in_mask(out, opaque)
        skin = skin_fraction(out, opaque)
        rows.append((os.path.basename(edited), text, skin, ""))
    for name, t, s, note in rows:
        flag = ""
        if isinstance(t, float) and isinstance(s, float):
            flag = " <-- SUSPECT" if (t > 0.005 or s > 0.30) else ""
            print(f"{name:60s} text={t:.4f} skin={s:.3f}{flag}")
        else:
            print(f"{name:60s} {note}")
    n_flag = sum(1 for _, t, s, _ in rows
                 if isinstance(t, float) and (t > 0.005 or (isinstance(s, float) and s > 0.30)))
    print(f"\n{len(rows)} edited images, {n_flag} suspects")


if __name__ == "__main__":
    main()
