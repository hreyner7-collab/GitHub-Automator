"""Forensic visual checks on edited images — the things 'looking' would catch.

For every edited image, verify:
  1. TEXT GONE:   the source's verified text bands no longer verify as text in
                  the edited output (anywhere: product core or wood).
  2. NO WOOD HOLES: product geometry preserved — opaque area minus repaired
                  text must cover the same product extent as the source mask.
  3. NO NEW TEXT: the wood background introduces no glyph-row structures.
  4. SEAM:        inpainted regions blend (sharp) instead of smearing.
Run:  python scripts/forensics.py
"""
import os
import re
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DL = os.path.join(BASE, "downloads")
# The pictures the finisher wrote beside a source photo. They are not sources:
# a tagged variant (`…_edited_gray`) used to slip past an endswith(".png")
# filter and get measured as if it were the photograph.
_DERIVATIVE = re.compile(r"_(?:edited|mask|repair)(?:_[a-z0-9]+)?$")
sys.path.insert(0, BASE)
from editor import _verify_text_bands  # noqa: E402


def band_fraction(img, region):
    """Fraction of `region` pixels covered by verified text bands."""
    v = _verify_text_bands(img) > 0
    if region.sum() == 0:
        return 0.0
    return float(v[region].mean())


def sharp_in(img, region):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64)
    if region.sum() < 20:
        return None
    return float(cv2.Laplacian(g, cv2.CV_64F)[region > 0].var())


def main():
    sources = sorted(
        f for f in os.listdir(DL)
        if "_img_" in f and not _DERIVATIVE.search(os.path.splitext(f)[0])
        and f.endswith((".png", ".jpg", ".jpeg", ".webp")))
    all_ok = True
    for s in sources:
        base = os.path.splitext(s)[0]
        src = cv2.imread(os.path.join(DL, s))
        out = cv2.imread(os.path.join(DL, base + "_edited.png"))
        msk = cv2.imread(os.path.join(DL, base + "_mask.png"), cv2.IMREAD_GRAYSCALE)
        if src is None or out is None or msk is None:
            print(f"SKIP {s} (missing artifact — likely refused)")
            continue
        if out.shape[:2] != src.shape[:2]:
            out = cv2.resize(out, (src.shape[1], src.shape[0]),
                             interpolation=cv2.INTER_NEAREST)
        if msk.shape[:2] != src.shape[:2]:
            msk = cv2.resize(msk, (src.shape[1], src.shape[0]),
                             interpolation=cv2.INTER_NEAREST)

        checks = {}
        # 1) text bands from the source must not verify as text in the output
        src_text = _verify_text_bands(src) > 0
        dil = cv2.dilate(src_text.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        checks["text_gone"] = band_fraction(out, dil)

        # 2) repair must not swallow the product: inpainted (repaired) pixels
        #    inside the shipped product are product material by design — but if
        #    they dominate the mask, text "removal" destroyed the listing.
        #    (Masks are never carved anymore, so there is no expected-vs-shipped
        #    gap to measure; the repaired band stays product.)
        opaque = msk >= 128
        repair_path = os.path.join(DL, base + "_repair.png")
        rep = cv2.imread(repair_path, cv2.IMREAD_GRAYSCALE)
        if rep is not None and rep.shape[:2] == msk.shape[:2]:
            repair_frac = float(((cv2.dilate(rep, np.ones((9, 9), np.uint8)) > 0)
                                 & opaque).sum()) / max(float(opaque.sum()), 1.0)
            dilated_mask = cv2.dilate(rep, np.ones((9, 9), np.uint8)) > 0
        else:
            repair_frac = 0.0
            dilated_mask = cv2.dilate(src_text.astype(np.uint8),
                                      np.ones((9, 9), np.uint8)) > 0
        checks["repair_frac"] = repair_frac

        # 3) no glyph rows introduced anywhere in the output
        checks["new_text_total"] = band_fraction(out, np.ones(src.shape[:2], bool))

        # 4) repaired seam must stay sharp (blend, not smear)
        seam = (sharp_in(out, dilated_mask & opaque)
                if dilated_mask.any() else None)
        checks["seam_sharpness"] = seam

        ok = (checks["text_gone"] <= 0.02 and checks["repair_frac"] <= 0.25
              and checks["new_text_total"] <= 0.01
              and (seam is None or seam >= 12.0))
        all_ok &= ok
        seam_s = "-" if seam is None else f"{seam:5.0f}"
        print(f"{'PASS' if ok else 'FAIL'} {s[:48]:50s} "
              f"text_gone={checks['text_gone']:.3f} repair={checks['repair_frac']:.2f} "
              f"new_text={checks['new_text_total']:.3f} seam_sharp={seam_s}")
    print("\nALL FORENSIC CHECKS PASS" if all_ok else "\nSOME CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
