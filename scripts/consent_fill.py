"""Phase 1 of the agreement-box proof on the REAL create-model form.

Takes a licence-clean, not-yet-published model through the form exactly the way
a publish does — through uploader.upload() itself, the same function the
dashboard and the auto run call:

    attach the 3MF -> Next (step 1 -> 2) -> name -> photos (covers + gallery)
    -> Auto-Generated category/tags -> real description -> Model Source
    (Remix Models + source pick) -> licence answers -> step 9's own agreement
    finder.

Set CREALITY_NO_SUBMIT=1 so the run STOPS before tapping Submit: the form is
filled, nothing is published. The result (including UPLOAD_VERIFY's own record
of what the consent finder found and which click target it used) is written to
debug/consent/upload_result.json for the phase-2 probe.

Usage:  .venv/Scripts/python.exe scripts/consent_fill.py
"""
import asyncio
import io
import json
import os
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ["CREALITY_NO_SUBMIT"] = "1"          # stop before the Submit click
sys.path.insert(0, ROOT)

from uploader import upload  # noqa: E402

# The model the last auto run fetched but never published (scale-model-pallet-jack,
# CC BY-NC-SA — remix allowed). Its 3MF and its kept photo are in downloads/.
NAME = "Домкрат для подъема поддонов 1/10-го масштаба"
SRC = "https://www.crealitycloud.com/model-detail/scale-model-pallet-jack"
MODEL = os.path.join(ROOT, "downloads",
                     "Домкрат+для+подъема+поддонов+1_10-го+масштаба.3mf")
PHOTO = os.path.join(ROOT, "downloads",
                     "домкрат_для_подъема_поддонов_1_10-го_мас_img_0.webp")
# The exact capture the auto run made for this model (the licence card read on
# the source page).  classify_license -> CC BY-NC-SA -> answers [SA, No, No].
LICENSE_RAW = ("creativecommons.org/licenses/by-nc-sa/4 License This work is licensed "
               "under the Copyright License 4.0. This license allows reusers to "
               "distribute, remix, adapt, and build upon the material in any medium "
               "or format for noncommercial purposes only, and only so long as "
               "attribution is given to the creator.")
DESC = ("Scale model pallet jack, 1/10 scale — re-uploaded as a remix under the "
        "source licence (CC BY-NC-SA).\n\nOriginal design: " + SRC)

OUT = os.path.join(ROOT, "debug", "consent", "upload_result.json")


async def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    for label, path in (("model", MODEL), ("photo", PHOTO)):
        if not os.path.exists(path):
            print("MISSING %s: %s" % (label, path))
            return 2
    t0 = time.time()
    try:
        res = await upload(NAME, DESC, MODEL, [PHOTO], SRC,
                           dry_run=False, license_raw=LICENSE_RAW)
    except Exception as e:                       # step 8c raises on a bad source
        res = {"success": False, "exception": type(e).__name__ + ": " + str(e)[:600]}
    res["_elapsed_s"] = round(time.time() - t0, 1)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)

    v = res.get("verify") or {}
    print("=== phase 1: form fill (no submit) ===")
    print("elapsed: %ss" % res["_elapsed_s"])
    print("success:", res.get("success"), "| no_submit:", res.get("no_submit"),
          "| dry_run:", res.get("dry_run"))
    if res.get("error"):
        print("error:", res["error"][:300])
    if res.get("exception"):
        print("exception:", res["exception"][:300])
    print("images:", json.dumps(v.get("images") or {}, ensure_ascii=False)[:400])
    print("remix declared:", v.get("remix_source_declared"),
          "| clicked:", (v.get("remix_suggestion_clicked") or "")[:60],
          "| registered:", (v.get("remix_registered_href") or "")[:80])
    if v.get("remix_source_error"):
        print("remix error:", v["remix_source_error"])
    la = v.get("license_answers") or {}
    print("licence: wanted=%s read=%s badge=%r expected=%r ok=%s"
          % (la.get("wanted"), la.get("radio_index_verified"), la.get("badge_license"),
             la.get("badge_expected"), la.get("badge_ok")))
    print("consent:", json.dumps(v.get("consent") or {}, ensure_ascii=False))
    print("copyright_agreed:", v.get("copyright_agreed"))
    print("wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
