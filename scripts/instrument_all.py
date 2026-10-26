"""Part-1 instrumentation: dump all debug artifacts for every corpus image.

Usage:  python scripts/instrument_all.py [subset-glob]
Dumps to  debug/<source_stem>/  per the instrumentation contract:
01_isnet_raw.png 02_u2net_raw.png 03_union.png 04_grabcut.png
04_grabcut_area_ratio.txt 05_color_distance.png 05_salient.png
06_guard_scores.json 07_distance_transform_per_component.json
08_final_composite.png (native resolution)
"""
import glob
import os
import re
import shutil
import sys
import traceback

import cv2

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import editor  # noqa: E402

DL = os.path.join(BASE, "downloads")
DEBUG_ROOT = os.path.join(BASE, "debug")
# The pictures the finisher wrote beside a source photo. Feeding one of these
# back in re-cuts a cut, and a tagged variant (`…_edited_gray`) slipped past the
# endswith(".png") filter this replaces.
_DERIVATIVE = re.compile(r"_(?:edited|mask|repair)(?:_[a-z0-9]+)?$")


def main(pattern: str = "*") -> None:
    srcs = sorted(p for p in glob.glob(os.path.join(DL, f"*{pattern}*"))
                  if not _DERIVATIVE.search(os.path.splitext(p)[0]))
    print(f"{len(srcs)} images to instrument")
    shipped = refused = 0
    for src in srcs:
        stem = os.path.splitext(os.path.basename(src))[0]
        ddir = os.path.join(DEBUG_ROOT, stem)
        os.makedirs(ddir, exist_ok=True)
        try:
            out = editor.edit_image(src, debug_dir=ddir)
            final = cv2.imread(out)
            if final is not None:
                cv2.imwrite(os.path.join(ddir, "08_final_composite.png"), final)
            shipped += 1
            print(f"  SHIPPED {stem}")
        except Exception as e:
            refused += 1
            print(f"  REFUSED {stem}: {str(e)[:80]}")
        # progress + memory hygiene: nets stay loaded (sessions cached)
        sys.stdout.flush()
    print(f"\ndone: {shipped} shipped, {refused} refused -> {DEBUG_ROOT}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "*")
