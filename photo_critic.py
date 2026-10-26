"""The panel that looks at a rebuilt picture and says whether it is right.

`editor` produces a picture whose product is copied pixel-for-pixel from the
source listing's own photograph and whose backdrop it painted itself. That is
the intent; this module measures whether the intent survived. Eight critics look
at the shipped file beside its source and none of them trusts the other:

  · model       — a fresh segmentation of the SOURCE photograph against the mask
                  the composite used. This is the complaint the whole route
                  answers: the thing that went onto the new background must be
                  the model the photograph shows, not half of it and not the
                  bench it was sitting on;
  · shape       — a fresh segmentation of the OUTPUT against the same mask. If
                  the shipped picture no longer reads as that one object, the
                  cut is wrong however the pipeline feels about it;
  · background  — the pixels around the model must have changed (otherwise the
                  source's photograph went out untouched);
  · backdrop    — and the new one must be flat, because a half-erased workbench
                  behind the product looks wrong;
  · colour      — the model's own pixels must be the ones from the source, so a
                  mean colour shift means something recoloured the plastic;
  · coverage    — a model shrunk into one corner is not a listing picture;
  · words       — text the detector verifies outside the model means a caption
                 or watermark survived the cut;
  · leftovers   — no patch of the photograph's own backdrop came through with the
                 model, away from the contact shadow the editor paints there.

A critic that fails names the remedy it wants, and `improve()` carries it out.
Every remedy it can carry out is the same rebuild on a DIFFERENT backdrop,
because the finisher is deterministic: two passes on one backdrop return the
identical file (measured 2026-10-26), so a retry that did not move the backdrop
could never change a verdict. A picture that still fails is DROPPED — never
shipped as the source's original file. Every attempt is reported, so the run log
reads like the argument that actually happened:

    looks wrong (45% of the model's silhouette missing) → rebuilt on graphite
    → looks right (shape 0.98, backdrop moved 38, plastic unchanged 2.1)

All thresholds are measured on real photographs, not guessed; see the numbers
beside each constant and GATES-MAKERWORLD.md G14.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

WORK_SIDE = 640          # segmentation size; masks are smooth, so this is enough

# ── thresholds, measured 2026-10-26 on the nine listing photographs in
# downloads/ that the finisher would ship (five more were refused before this
# panel ever saw them). The two shape numbers came from segmenting both the
# source and the output of every pair against the mask the composite used:
#   output vs mask — 0.11 0.36 0.74 broken | 0.89 0.90 0.91 0.91 0.93 0.93 0.98 healthy
#   source vs mask — 0.67 0.74 0.90 0.94 0.94 0.94 0.95 0.98 (0.00 = the source
#                   itself would not segment, so it is not a verdict)
# The healthy floor of 0.89 is the segmenter's own noise between two runs on
# near-identical pictures, which is why 0.90 sat on it and refused good cuts;
# 0.85 still separates them by a clear margin from the 0.74 that failed.
SHAPE_MIN = 0.85           # IoU(fresh segmentation of the OUTPUT, composite mask)
MODEL_MIN = 0.55           # IoU(fresh segmentation of the SOURCE, composite mask)
MODEL_JUDGABLE = 0.02      # a source segmentation below this found nothing to compare
BACKGROUND_MOVE_MIN = 18.0  # mean ΔE between the two backdrops (same colour = 0)
# Measured 2026-10-26 on the composition masks of real shipped outputs and on two
# controls built from them. Shipped pictures — flat sweep plus the contact shadow
# the editor paints near the object — spread 4.1–12.3 over 20 pairs and 4.5–15.1
# over the 18 files of the last full panel run. The failure this names, the
# photograph's own workbench still standing behind the product (the far field
# pasted back from the source), measures 21.9–30.8. The limit therefore sits in
# the gap: 18.0 refuses what the old 26.0 let through, and every picture this
# machine has actually shipped still passes it. (A *small* leftover patch is a
# different statistic and belongs to "no leftovers", below.)
BACKGROUND_FLAT_MAX = 18.0
COLOUR_KEEP_MAX = 9.0    # mean ΔE of the model's own pixels, before vs after
COVERAGE_MIN = 0.06      # share of the frame the model fills
COVERAGE_MAX = 0.95
WORDS_MAX = 0.0015       # share of the frame covered by verified text
LEFTOVER_MAX = 0.001     # share of the frame still showing the photo's own backdrop

# Backdrops the editor can build and this loop will try. "keep" is not one of
# them: leaving the source's own background in the frame is exactly the
# "you didn't change anything" picture this whole route exists to avoid.
CUT_BACKDROPS = ("white", "gray", "graphite", "charcoal", "wood")

# editor.BACKGROUNDS holds two spellings for the same 150-level sweep. A retry
# that came back to the other one would rebuild a byte-identical picture under a
# different filename and read the identical verdict, so the loop works on one
# spelling only.
BACKDROP_ALIAS = {"grey": "gray"}


def _lab(img: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0
    lab[..., 1] -= 128.0
    lab[..., 2] -= 128.0
    return lab


def _delta_e(a: np.ndarray, b: np.ndarray) -> float:
    """Mean CIE76 colour difference between two Lab means."""
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def _fresh_mask(img: np.ndarray) -> np.ndarray | None:
    """The product as an independent segmenter sees it in the shipped file."""
    try:
        from rembg import remove
        import editor
        sess = editor._rembg_sessions.get("u2net")
        if sess is None:
            sess = editor.new_session_safe("u2net")
            editor._rembg_sessions["u2net"] = sess
        h, w = img.shape[:2]
        s = min(1.0, WORK_SIDE / max(h, w))
        small = cv2.resize(img, (int(w * s), int(h * s)),
                           interpolation=cv2.INTER_AREA) if s < 1.0 else img
        rgba = cv2.cvtColor(small, cv2.COLOR_BGR2RGBA)
        mask = np.array(remove(rgba, session=sess, only_mask=True))
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
        return (mask >= 128).astype(np.uint8)
    except Exception:                                    # noqa: BLE001
        return None


def _composition_mask(source_path: str, out_path: str) -> np.ndarray | None:
    """The opaque product region `editor` wrote beside the picture it shipped.

    `edit_image` writes `{stem}_edited{tag}.png` and its mask as
    `{stem}_mask{tag}.png`, so the sidecar is the LAST `_edited` in the name —
    a source file already called `…_edited_img_1` would otherwise send the
    lookup to a path that never existed.
    """
    name = os.path.basename(out_path)
    head, sep, tail = name.rpartition("_edited")
    if not sep:
        return None
    path = os.path.join(os.path.dirname(out_path), head + "_mask" + tail)
    if not os.path.exists(path):
        return None
    m = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    return None if m is None else (m > 127).astype(np.uint8)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = float(np.logical_or(a, b).sum())
    return float(np.logical_and(a, b).sum()) / union if union else 0.0


def _verdict(name: str, ok: bool, measured: str, limit: str, fix: str) -> dict:
    return {"critic": name, "ok": bool(ok), "measured": measured,
            "limit": limit, "fix": "" if ok else fix}


def critique(source_path: str, out_path: str) -> dict:
    """Look at one produced picture beside its source.

    {ok, verdicts:[…], fixes:[…], measurements:{…}}. `fixes` is the distinct set
    of remedies the failing critics asked for, in the order they were found.
    """
    src = cv2.imread(source_path, cv2.IMREAD_COLOR)
    out = cv2.imread(out_path, cv2.IMREAD_COLOR)
    if src is None or out is None:
        return {"ok": False, "verdicts": [],
                "fixes": ["drop"], "measurements": {},
                "why": "the picture or its source could not be read back"}
    if src.shape[:2] != out.shape[:2]:
        out = cv2.resize(out, (src.shape[1], src.shape[0]),
                         interpolation=cv2.INTER_AREA)

    mask = _composition_mask(source_path, out_path)
    if mask is None:
        return {"ok": False, "verdicts": [], "fixes": ["drop"], "measurements": {},
                "why": "the composite's own product mask was not written, so the "
                       "shipped picture cannot be vouched for"}
    if mask.shape[:2] != out.shape[:2]:
        mask = cv2.resize(mask, (out.shape[1], out.shape[0]),
                          interpolation=cv2.INTER_NEAREST)

    verdicts = []
    lab_src, lab_out = _lab(src), _lab(out)
    inside, outside = mask > 0, mask == 0
    # A mask that fills or empties the frame has no other side to compare with,
    # and numpy answers that with NaN — which compares False everywhere, so the
    # critics did fail closed but reported `nan` as the measurement. Say which
    # side is missing instead.
    if not outside.any():
        unmeasurable = "the mask covers the whole frame"
    elif not inside.any():
        unmeasurable = "the mask covers nothing"
    else:
        unmeasurable = ""

    # 1 · the region that was pasted is the model the photograph shows. This is
    # the user's own complaint — "that doesn't look like the original model" —
    # and only the SOURCE can answer it: a fresh segmenter that never saw the
    # composite is pointed at the photograph, and what it finds there is compared
    # with what the finisher actually cut out. Where the photograph will not
    # segment at all there is nothing to compare, and this critic says so instead
    # of inventing a failure.
    fresh_src = _fresh_mask(src)
    src_cover = float(fresh_src.mean()) if fresh_src is not None else 0.0
    if src_cover < MODEL_JUDGABLE:
        model_iou = -1.0
        verdicts.append(_verdict(
            "model in the photo", True, "the photograph would not segment",
            f"≥ {MODEL_MIN:.2f} when judgeable", ""))
    else:
        model_iou = _iou(fresh_src, mask)
        verdicts.append(_verdict(
            "model in the photo", model_iou >= MODEL_MIN, f"{model_iou:.2f}",
            f"≥ {MODEL_MIN:.2f}", "remask"))

    # 2 · the shipped picture still reads as that one object: the same
    # segmenter, now pointed at what is going to be posted.
    fresh = _fresh_mask(out)
    if fresh is None:
        shape_iou = 0.0
        verdicts.append(_verdict("shape", False, "could not be measured",
                                 f"≥ {SHAPE_MIN:.2f}", "drop"))
    else:
        shape_iou = _iou(fresh, mask)
        verdicts.append(_verdict(
            "shape", shape_iou >= SHAPE_MIN, f"{shape_iou:.2f}", f"≥ {SHAPE_MIN:.2f}",
            "remask"))

    # 3 · the backdrop really moved, and it is a sweep rather than leftovers.
    if unmeasurable:
        move = flat = -1.0
        verdicts.append(_verdict("background", False, unmeasurable,
                                 f"≥ {BACKGROUND_MOVE_MIN}", "remask"))
        verdicts.append(_verdict("backdrop is clean", False, unmeasurable,
                                 f"≤ {BACKGROUND_FLAT_MAX}", "remask"))
    else:
        move = _delta_e(lab_src[outside].mean(axis=0), lab_out[outside].mean(axis=0))
        flat = float(lab_out[..., 0][outside].std())
        verdicts.append(_verdict(
            "background", move >= BACKGROUND_MOVE_MIN, f"{move:.1f}",
            f"≥ {BACKGROUND_MOVE_MIN}", "background"))
        verdicts.append(_verdict(
            "backdrop is clean", flat <= BACKGROUND_FLAT_MAX, f"{flat:.1f}",
            f"≤ {BACKGROUND_FLAT_MAX}", "background"))

    # 4 · the plastic is the source's own: same pixels, same colour.
    if unmeasurable:
        colour = -1.0
        verdicts.append(_verdict("colour kept", False, unmeasurable,
                                 f"≤ {COLOUR_KEEP_MAX}", "remask"))
    else:
        colour = _delta_e(lab_src[inside].mean(axis=0), lab_out[inside].mean(axis=0))
        verdicts.append(_verdict(
            "colour kept", colour <= COLOUR_KEEP_MAX, f"{colour:.1f}",
            f"≤ {COLOUR_KEEP_MAX}", "drop"))

    # 5 · a listing picture is mostly the model.
    cover = float(inside.sum()) / float(inside.size)
    verdicts.append(_verdict(
        "coverage", COVERAGE_MIN <= cover <= COVERAGE_MAX, f"{cover * 100:.0f}%",
        f"{COVERAGE_MIN * 100:.0f}–{COVERAGE_MAX * 100:.0f}%", "remask"))

    # 6 · no caption or watermark survived the cut — measured OUTSIDE the model,
    # because letters printed on the object are part of the model the user asked
    # to keep. Text across the product is the finisher's own repair stage
    # (`editor._verify_text_bands` is the only detector it lets alter the
    # product), so a nameplate, a keychain or a stamped lid is not a rejection.
    try:
        import editor
        words = editor._verify_text_bands(out)
    except Exception as e:                              # noqa: BLE001
        # The same detector the finisher itself relies on. If it cannot be run,
        # nothing has vouched that a caption is absent, and "untested" is not
        # the same as "clean" — so this fails instead of defaulting to 0.0.
        word_share = -1.0
        measured = f"could not be measured ({type(e).__name__})"
    else:
        word_share = float((np.asarray(words) > 0)[outside].sum()) / float(inside.size)
        measured = f"{word_share * 100:.2f}%"
    verdicts.append(_verdict(
        "no words", word_share >= 0 and word_share <= WORDS_MAX, measured,
        f"≤ {WORDS_MAX * 100:.2f}%", "drop"))

    # 7 · no piece of the photograph's own backdrop came through with the model.
    # Only judged when the backdrop really moved: on a new sweep of the same tone
    # the comparison has nothing to stand on, and "background" has already failed.
    # The band hugging the model is left out of it on purpose — the editor paints
    # a contact shadow there, and a shadow that touches the object is a photograph
    # looking more real, not a leftover.
    src_bg = np.median(lab_src[outside], axis=0) if outside.any() else np.zeros(3)
    out_bg = np.median(lab_out[outside], axis=0) if outside.any() else np.zeros(3)
    leftover = 0.0
    if _delta_e(src_bg, out_bg) >= BACKGROUND_MOVE_MIN:
        band = max(9, int(round(0.02 * min(inside.shape))))
        near_model = cv2.dilate(mask, np.ones((band, band), np.uint8)) > 0
        near_old = ((~inside) & (~near_model)
                    & (np.linalg.norm(lab_out - src_bg, axis=2) < 10.0))
        n, _, stats, _ = cv2.connectedComponentsWithStats(
            near_old.astype(np.uint8) * 255, 8)
        big = [stats[i, cv2.CC_STAT_AREA] for i in range(1, n)
               if stats[i, cv2.CC_STAT_AREA] >= max(300, 0.0002 * inside.size)]
        leftover = float(sum(big)) / float(inside.size)
    verdicts.append(_verdict(
        "no leftovers", leftover <= LEFTOVER_MAX, f"{leftover * 100:.2f}%",
        f"≤ {LEFTOVER_MAX * 100:.2f}%", "remask"))

    fixes = []
    for v in verdicts:
        if v["fix"] and v["fix"] not in fixes:
            fixes.append(v["fix"])
    return {"ok": all(v["ok"] for v in verdicts), "verdicts": verdicts,
            "fixes": fixes,
            "measurements": {"shape": round(shape_iou, 3),
                             "model_in_photo": round(model_iou, 3),
                             "background_move": round(move, 1),
                             "backdrop_flat": round(flat, 1),
                             "colour_kept": round(colour, 1),
                             "coverage": round(cover, 3),
                             "leftovers": round(leftover, 4),
                             "words": round(word_share, 4)}}


def _wants_contrast(background: str, source_path: str,
                    already_tried=()) -> str:
    """A backdrop that cannot be mistaken for the model's own colour.

    Measured on the source's own pixels, not on a guess: the sweeps are sorted
    by lightness and the one furthest from the plastic wins, with a penalty for
    coming back in the tone the photograph already had — "the background is
    still the same colour" is one of the complaints this is answering. Anything
    already tried on this picture is left out as well: `editor` is deterministic,
    so a backdrop that has been used once would return the same file and the same
    verdict. "" means there is no backdrop left that has not been tried.
    """
    from editor import BACKGROUNDS

    img = cv2.imread(source_path, cv2.IMREAD_COLOR)
    if img is None:
        return ""
    lab = _lab(img)
    m = _fresh_mask(img)
    if m is not None and m.any():
        product_l = float(lab[..., 0][m > 0].mean())
        bg_l = float(lab[..., 0][m == 0].mean()) if (~m.astype(bool)).any() else product_l
    else:
        # No segmentation: the middle of the frame is the model, its edges are
        # the backdrop. Wrong sometimes, but only used to rank five sweeps.
        h, w = lab.shape[:2]
        product_l = float(lab[h // 4:3 * h // 4, w // 4:3 * w // 4, 0].mean())
        bg_l = float(np.concatenate([
            lab[:max(1, h // 8), :, 0].ravel(),
            lab[-max(1, h // 8):, :, 0].ravel()]).mean())
    best, best_gap = "", None
    for name in CUT_BACKDROPS:
        if name == background or name in already_tried:
            continue
        level = BACKGROUNDS.get(name)
        level = 200.0 if level is None else float(level) * 100.0 / 255.0
        gap = abs(level - product_l) - 0.5 * abs(level - bg_l)
        # The best of what is left, whatever its sign: a sweep can score below
        # zero and still be the furthest from the plastic. Starting the search
        # at -1.0 threw candidates away — measured on 2026-10-26, the loop
        # stopped after three of five sweeps and said it had spent them all.
        if best_gap is None or gap > best_gap:
            best, best_gap = name, gap
    return best


def improve(source_path: str, background: str = "", attempts: int = 3) -> dict:
    """Produce one picture, look at it, and fix what the critics name.

    Returns {ok, path, attempts:[{background, verdicts, notes}], why}. `path` is
    "" when every attempt was refused — the caller must treat that as a missing
    picture, never fall back to the source file.
    """
    import editor

    tried, notes = [], []
    used = set()
    backdrop = BACKDROP_ALIAS.get(background, background) or CUT_BACKDROPS[0]
    for i in range(max(1, int(attempts))):
        style = backdrop
        used.add(style)
        try:
            out = editor.edit_image(source_path, background=style, recolour="")
        except Exception as e:              # noqa: BLE001 — the finisher refused it
            notes.append(f"attempt {i + 1} ({style}) refused by the finisher: "
                         f"{type(e).__name__}: {str(e)[:160]}")
            tried.append({"background": style, "verdicts": [], "ok": False})
            # A refusal is about the photograph's masks failing every QA score,
            # not about this backdrop: measured on 14 listing photographs, the
            # same five were refused whichever backdrop each was given, and a
            # second one only spends minutes to fail the same way.
            break
        look = critique(source_path, out)
        tried.append({"background": style, "ok": look["ok"],
                      "verdicts": look["verdicts"], "file": os.path.basename(out)})
        if look["ok"]:
            notes.append(f"{os.path.basename(source_path)} → looks right on {style} "
                         f"(shape {look['measurements']['shape']:.2f}, backdrop moved "
                         f"{look['measurements']['background_move']:.0f}, plastic "
                         f"unchanged {look['measurements']['colour_kept']:.1f})")
            return {"ok": True, "path": out, "attempts": tried, "notes": notes,
                    "measurements": look["measurements"]}
        wrong = "; ".join(f"{v['critic']} {v['measured']} (wants {v['limit']})"
                          for v in look["verdicts"] if not v["ok"])
        notes.append(f"attempt {i + 1} on {style} does not look right — {wrong}")
        if not ({"background", "remask"} & set(look["fixes"])):
            # Only a rebuild can answer those two; a critic that names "drop"
            # (the plastic was recoloured, words across the frame) is a fact
            # about this photograph and another backdrop cannot argue with it.
            break
        if i + 1 >= max(1, int(attempts)):
            notes.append("the attempts allowed on one picture have run out, so it is "
                         "dropped rather than posted")
            break
        nxt = _wants_contrast(style, source_path, used)
        if not nxt:
            notes.append("every backdrop this machine can build has now been tried "
                         "on this picture, so it is dropped rather than posted")
            break
        backdrop = nxt
        notes.append(f"rebuilding the same cut on {backdrop}, which is further from "
                     "both the plastic and the photo's own backdrop than " + style)
        continue

    return {"ok": False, "path": "", "attempts": tried, "notes": notes,
            "why": notes[-1] if notes else "no picture was produced"}
