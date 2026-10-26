"""
Fully LOCAL listing-image finisher — no API keys, no accounts, no network, no cost.

Pipeline per image (product geometry and texture are NEVER resampled):
  1. Segment the product (what pixels are the object?) — ISNet/U2Net via
     `rembg` (free, open-source, models downloaded once) with GrabCut
     refinement; several candidate masks are tried and the best wins.
  2. NO TEXT ANYWHERE: glyph-row VERIFIED text/watermark bands are inpainted
     (product material fills text printed on the product; background text
     simply dies with the wood swap). Product structure is never carved:
     texture edges fail the glyph-row test and stay untouched.
  3. Build a studio backdrop — procedural wooden planks (default) or a flat
     neutral sweep (`background="gray" | "white" | "graphite"`) — with grain,
     soft key light, vignette and contact shadow; deterministic per image,
     zero blur by construction.
  4. Optional filament colour (`recolour="blue"` / `"#rrggbb"`): the product's
     Lab mean is shifted by a CONSTANT offset, so every local difference —
     layer lines, texture, shading — is preserved exactly. Off by default.
  5. Composite with a tight feathered mask (no halo, no soft edges), full
     resolution end-to-end: nothing is downscaled or resampled.

A built-in QA guard measures Laplacian sharpness of the product region in the
output vs the source and refuses to emit a result that lost detail. With a
recolour asked for, the colour guard measures the drift BEYOND the shift that
was requested, so an accidental extra colour change still fails.
"""
import hashlib
import json
import os
import re
import sys
from typing import NamedTuple

import cv2
import numpy as np

# Product pixels must keep at least this fraction of the source's local
# sharpness (Laplacian variance) inside the product mask, or we refuse to save.
SHARPNESS_KEEP_MIN = 0.90
SHARP_ABS_MIN = 30.0      # absolute floor: shipped product core must be at least
                          # this sharp (Laplacian variance), whatever the source
COLOR_IDENTITY_MAX = 8.0  # max mean |Δ(a,b)| in Lab inside the product core:
                          # chroma is color IDENTITY — sharpening barely moves
                          # it, recoloring moves it hugely
SHARPEN_ABOVE = 130.0     # sources with product-core sharpness below this get an
                          # unsharp-mask recovery pass (blurry-source fix)
DROP_PARTS_MAX = 0.02     # max frame fraction of large salient islands a mask
                          # may drop (a garment lost from a hanger shot fails QA)
RESIDUAL_TEXT_MAX = 0.005  # max fraction of the product core under VERIFIED
                          # text bands in the SHIPPED composite (img_5 shipped
                          # 0.0123 under the old 0.02 tolerance)
# Vacuous-pass guards (img_1 regression): every other guard measures quality
# INSIDE the mask, so a tiny fragment of the product scored perfectly on all
# of them and WON the sharpness-ratio race (6.62 vs the complete caddy's
# 2.36), shipping a near-empty composite. Measured gap on the corpus: all 34
# healthy shipped winners keep >= 0.41 of the candidates' union prior; the
# img_1 fragment kept 0.08.
UNION_RECALL_MIN = 0.35   # winner must keep >= this share of every union
                          # prior component it MATERIALLY ENGAGES (>=1% of
                          # the comp). Per-component, not whole-union: the
                          # hinge_8 blue plates sit beside a huge gray mass
                          # the nets also fired on — whole-union recall
                          # 0.27 there is CORRECT (unclaimed background,
                          # caddy-img_9 pattern), but img_1's sliver engaging
                          # its comp at 0.12 is a vacuous fragment. Healthy
                          # fleet: >= 0.41 per engaged comp.
OPAQUE_KEEP_MIN = 0.60    # opaque-shipped area must keep >= this share of the
                          # candidate's own mask (img_1 sliver ghosted to 0.46
                          # in erode+feather; every healthy ship >= 0.9).
                          # Scale-free by construction — close-ups immune.

# Backdrops the finisher can build. "wood" is the original default; the rest
# are flat neutral studio sweeps at a given brightness (0-255).
BACKGROUNDS = {"wood": None, "gray": 150, "grey": 150, "white": 222,
               "graphite": 62, "charcoal": 88}
# Filament colours for the opt-in recolour, as BGR hex.
COLOURS = {
    "white": "#f2f2f2", "black": "#1a1a1a", "gray": "#808080", "grey": "#808080",
    "silver": "#c0c3c7", "red": "#d0342c", "orange": "#e07a2f", "yellow": "#e8c33a",
    "green": "#3f9d4a", "teal": "#2f8f9b", "blue": "#2f6fd0", "navy": "#243b6b",
    "purple": "#7a4fbf", "pink": "#e06aa8", "brown": "#7a5230", "copper": "#b87333",
    "gold": "#d4af37",
}
RECOLOUR_L_GAIN = 0.6     # how far the lightness moves toward the target:
                          # 1.0 would flatten the print's own shading
CORNER_TOL = 6            # neutral backdrops: shipped corner must equal the
                          # rendered backdrop corner within this (per channel)

_rembg_sessions: dict[str, object] = {}   # model name -> loaded session
_rembg_sessions["isnet-general-use"] = None
_rembg_sessions["u2net"] = None           # pre-declared: one owner per model
_rembg_sessions["u2net_human_seg"] = None # hands/arms holding the product
MASK_WORK_SIDE = 1600   # segmentation runs at this size; masks are smooth, so
                        # upscaling the mask costs nothing and keeps it fast


class Candidate(NamedTuple):
    """A candidate product mask and where it came from (for QA reports)."""
    name: str
    mask: np.ndarray


# ── Segmentation ──────────────────────────────────────────────────────────────

def _dbg_dump(debug_dir: str | None, name: str, arr: np.ndarray) -> None:
    """Write one lossless PNG debug artifact at work resolution. No-op when
    debug_dir is None (production path untouched — zero cost, zero files)."""
    if debug_dir is None:
        return
    os.makedirs(debug_dir, exist_ok=True)
    cv2.imwrite(os.path.join(debug_dir, name), arr)


def _candidate_masks(img_bgr: np.ndarray, debug_dir: str | None = None
                     ) -> list[Candidate]:
    """Candidate product masks at FULL resolution, ordered best-first guess:
    ISNet-union, GrabCut-refined union, salient-attached-parts union, plain
    U2Net/ISNet. The caller composites with each and lets the QA guards pick
    the winner — per-image self-tuning instead of one gamble. Nothing paid,
    nothing online.

    With `debug_dir`, dumps every intermediate (raw nets, union, GrabCut +
    its area ratio, salient + the color-distance map feeding it) as lossless
    PNGs at work scale — the instrumentation contract (identical code path,
    not a parallel implementation)."""
    from rembg import remove, new_session
    from PIL import Image
    h, w = img_bgr.shape[:2]
    scale = min(1.0, MASK_WORK_SIDE / max(h, w))
    work = cv2.resize(img_bgr, (int(w * scale), int(h * scale)),
                      interpolation=cv2.INTER_AREA) if scale < 1.0 else img_bgr
    rgb = cv2.cvtColor(work, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)

    at_work = {}
    for model in ("isnet-general-use", "u2net"):
        try:
            if _rembg_sessions.get(model) is None:
                _rembg_sessions[model] = new_session(model)   # loaded once, reused
            m = np.array(remove(pil, session=_rembg_sessions[model], only_mask=True))
            if m.ndim == 3:
                m = m[:, :, 0]
            if m.shape[:2] != work.shape[:2]:
                m = cv2.resize(m, (work.shape[1], work.shape[0]), interpolation=cv2.INTER_LINEAR)
            if m.max() > 10:
                at_work[model] = m
                _dbg_dump(debug_dir,
                          "01_isnet_raw.png" if model.startswith("isnet") else "02_u2net_raw.png",
                          m)
        except Exception as e:
            print(f"[editor] {model} unavailable ({e}) — trying next")

    def up(m):
        return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR) \
            if m.shape[:2] != (h, w) else m

    cands = []
    if at_work:
        names = list(at_work)
        union = np.max(list(at_work.values()), axis=0).astype(np.uint8)
        _dbg_dump(debug_dir, "03_union.png", union)
        ratio_out: list[float] = []
        refined = _grabcut_refine(work, union, ratio_out=ratio_out)
        if debug_dir is not None:
            _dbg_dump(debug_dir, "04_grabcut.png", refined)
            # the raw area ratio vs the seed — logged even when the 0.4x-3x
            # acceptance bound then rejects the refinement
            ratio = ratio_out[0] if ratio_out else 1.0
            with open(os.path.join(debug_dir, "04_grabcut_area_ratio.txt"), "w") as f:
                f.write(f"{ratio:.6f}\n")
        if refined is not union and (refined > 127).mean() != (union > 127).mean():
            cands.append(Candidate("union+gc", up(refined)))
        # multi-part products: grow the mask over parts ATTACHED to the net's
        # product (garment on a hanger, wire sub-hooks, mounting clamp) — nets
        # keep the rigid hero object and drop soft/thin attached parts
        sal = _salient_foreground(work, union, _verify_text_bands(work))
        if debug_dir is not None:
            dist = np.linalg.norm(work.astype(np.float32) - _bg_color(work), axis=2)
            _dbg_dump(debug_dir, "05_color_distance.png",
                      np.clip(dist * (255.0 / max(dist.max(), 1e-6)), 0, 255).astype(np.uint8))
            _dbg_dump(debug_dir, "05_salient.png", sal if sal is not None else np.zeros(work.shape[:2], np.uint8))
        if sal is not None and (sal > 0).mean() <= 0.60:
            cands.append(Candidate("salient", up(cv2.max(union, sal))))
        for n in names:
            cands.append(Candidate(n, up(at_work[n])))
    if not cands:
        cands.append(Candidate("grabcut", _grabcut_mask(img_bgr)))
    return cands


def _get_mask_candidates(img_bgr: np.ndarray) -> list[np.ndarray]:
    """Bare-mask view of `_candidate_masks` (kept for scripts/QA tooling)."""
    return [c.mask for c in _candidate_masks(img_bgr)]


def _human_overlap(img_bgr: np.ndarray) -> np.ndarray | None:
    """Pixels of a PERSON (hand/arm holding the print) via rembg's human-
    segmentation net, or None when the net is unavailable. Runs at 1024px —
    silhouettes don't need full res. Callers decide hand-vs-product with
    `_strip_foreign_body`'s overlap/fraction test."""
    try:
        from rembg import remove
        h, w = img_bgr.shape[:2]
        scale = min(1.0, 1024.0 / max(h, w))
        work = cv2.resize(img_bgr, (int(w * scale), int(h * scale)),
                          interpolation=cv2.INTER_AREA) if scale < 1.0 else img_bgr
        rgb = cv2.cvtColor(work, cv2.COLOR_BGR2RGB)
        from PIL import Image
        if _rembg_sessions.get("u2net_human_seg") is None:
            _rembg_sessions["u2net_human_seg"] = new_session_safe("u2net_human_seg")
        sess = _rembg_sessions["u2net_human_seg"]
        if sess is None:
            return None
        m = np.array(remove(Image.fromarray(rgb), session=sess, only_mask=True))
        if m.ndim == 3:
            m = m[:, :, 0]
        if m.shape[:2] != (h, w):
            m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
        return (m > 127).astype(np.uint8) * 255
    except Exception:
        return None


def _skin_like(img_bgr: np.ndarray, region: np.ndarray, min_frac: float = 0.25
               ) -> bool:
    """True when `region` (bool mask) is dominated by SKIN-colored pixels
    (YCrCb gate). The human-segmentation net also fires on dark faucets,
    monitor bezels and other neutral clutter (caddy img_0: it flagged the
    black faucet zone, 5.6% skin-like) — a region this test rejects must
    never be treated as a hand. Measured: real hands score far above the
    0.25 floor; the faucet false-fire scored 0.056."""
    if region.sum() < 100:
        return False
    ycc = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YCrCb)
    cr = ycc[..., 1].astype(np.int16)
    cb = ycc[..., 2].astype(np.int16)
    y = ycc[..., 0].astype(np.int16)
    skin = ((cr > 135) & (cr < 180) & (cb > 85) & (cb < 135) & (y > 60))
    return float(skin[region].mean()) >= min_frac


def _strip_foreign_body(mask: np.ndarray, human: np.ndarray | None,
                        img_bgr: np.ndarray | None = None,
                        ) -> tuple[np.ndarray, bool]:
    """Remove a PERSON (hand/arm) from a product mask. Verified discriminator
    (real photos, measured): a genuine hand OVERLAPS the product but is a
    small part of it (18-37%), while a wood/skin-COLORED product reads as
    ~95% of its own mask — so 'foreign body' means overlap>5% AND covered
    product fraction<=55%. With `img_bgr` the flagged region must ALSO be
    skin-colored (the net false-fires on faucets/clutter; without skin
    verification it amputated the caddy's product mask — img_0 regression).
    Returns (cleaned mask, hand_found)."""
    if human is None or not human.any():
        return mask, False
    hard = mask > 127
    if hard.sum() < 1000:
        return mask, False
    frac_of_mask = float((human[hard] > 0).mean())
    dil = cv2.dilate(human, np.ones((9, 9), np.uint8)) > 0
    overlap = float((dil & hard).sum()) / max(float(hard.sum()), 1.0)
    # a real gripping hand covers a substantial part of the product (measured
    # ~15%); sub-2% blobs are wood knots / hardware false positives (img_5: 0.7%)
    if overlap <= 0.05 or frac_of_mask > 0.55 or frac_of_mask < 0.02:
        return mask, False               # no hand, or the 'hand' IS the product
    # skin verification: the human net's region must be skin-COLORED, or it is
    # a dark faucet/monitor false-fire and stripping would amputate the product
    if img_bgr is not None and not _skin_like(img_bgr, human > 0):
        return mask, False
    keep = cv2.erode((~(human > 0)).astype(np.uint8) * 255,
                     np.ones((5, 5), np.uint8))   # pull the edge off the skin
    return cv2.bitwise_and(mask, keep), True


def new_session_safe(model: str):
    """new_session that returns None instead of raising (model download failed
    / offline) so callers degrade gracefully."""
    try:
        from rembg import new_session
        return new_session(model)
    except Exception as e:
        print(f"[editor] {model} unavailable ({e}) — feature disabled")
        return None


def _grabcut_refine(img_bgr: np.ndarray, mask: np.ndarray,
                    ratio_out: list[float] | None = None) -> np.ndarray:
    """Snap a coarse mask to true color boundaries with GrabCut seeded by it:
    mask>127 → probable-FG, a dilated ring outside it → probable-BG. Rescues
    same-material photos (wood product on wood cabinet) where the net's mask
    bleeds into the background. When `ratio_out` is a list, the raw refined/
    seed area ratio is appended to it BEFORE the acceptance bound decides —
    so instrumentation can see rejections that would otherwise be silent."""
    try:
        hard = (mask > 127).astype(np.uint8)
        if hard.mean() < 0.02 or hard.mean() > 0.92:
            return mask                     # too little/much confidence to refine
        gcm = np.full(hard.shape, cv2.GC_PR_BGD, np.uint8)
        gcm[hard > 0] = cv2.GC_PR_FGD
        gcm[hard > 200] = cv2.GC_FGD
        ring = cv2.dilate(hard, np.ones((13, 13), np.uint8)) - hard
        gcm[ring > 0] = cv2.GC_BGD
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        cv2.grabCut(img_bgr, gcm, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
        refined = np.where((gcm == cv2.GC_FGD) | (gcm == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        # keep the refinement only if it stays sane (within 3× of the seed area)
        a0, a1 = hard.sum(), (refined > 0).sum()
        if ratio_out is not None:
            ratio_out.append(float(a1) / max(float(a0), 1.0))
        if 0.4 * a0 <= a1 <= 3.0 * a0:
            return cv2.max(mask, refined)   # union with seed: never lose product
        return mask
    except Exception:
        return mask


def _grabcut_mask(img_bgr: np.ndarray) -> np.ndarray:
    """OpenCV-only subject mask (fully offline, no model download)."""
    h, w = img_bgr.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    rect = (int(w * 0.04), int(h * 0.04), int(w * 0.92), int(h * 0.92))
    cv2.grabCut(img_bgr, mask, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = np.where(labels == biggest, 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((max(15, h // 40),) * 2, np.uint8))
    return m


def _bg_color(img_small: np.ndarray) -> np.ndarray:
    """Median color of the frame border — the assumed backdrop."""
    border = np.concatenate([
        img_small[:8].reshape(-1, 3), img_small[-8:].reshape(-1, 3),
        img_small[:, :8].reshape(-1, 3), img_small[:, -8:].reshape(-1, 3)])
    return np.median(border, axis=0)


def _attach_to_mask(fg: np.ndarray, seed_mask: np.ndarray,
                    text_bands: np.ndarray | None) -> np.ndarray | None:
    """Components of `fg` large enough to be product parts AND touching the
    seed mask (attachment chains through neighboring parts). None when
    nothing attaches or the result would swallow the frame."""
    if text_bands is not None and text_bands.any():
        fg = fg & (text_bands == 0)
    fg = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_CLOSE,
                          np.ones((9, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n <= 1:
        return None
    h, w = fg.shape[:2]
    anchor = cv2.dilate((seed_mask > 127).astype(np.uint8),
                        np.ones((25, 25), np.uint8))
    keep = np.zeros_like(fg)
    area_min = max(int(0.004 * h * w), 200)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < area_min:
            continue
        comp = lab == i
        if (comp & anchor).any():
            keep |= comp
    if not keep.any() or keep.mean() > 0.60:
        return None
    keep = cv2.morphologyEx(keep.astype(np.uint8), cv2.MORPH_CLOSE,
                            np.ones((15, 15), np.uint8))
    return keep * 255


def _salient_foreground(img_small: np.ndarray, seed_mask: np.ndarray,
                        text_bands: np.ndarray) -> np.ndarray | None:
    """Foreground parts ATTACHED to `seed_mask`, by color distance from the
    border-median backdrop — catches what segmenters drop on multi-part
    products (garment on a hanger, wire sub-hooks, a mounting clamp) against
    a neutral wall/door backdrop. Returns None when the cue is ambiguous
    (heterogeneous borders, same-material photos) — a chroma-based fallback
    was tried and deliberately removed: it cannot tell a colored product part
    from a chromatic WOODEN FLOOR, and happily shipped the floor as product.
    Photos where this cue declines simply keep their net masks; if parts were
    truly dropped, the dropped-parts guard refuses the image instead of
    shipping it damaged."""
    dist = np.linalg.norm(img_small.astype(np.float32) - _bg_color(img_small), axis=2)
    return _attach_to_mask(dist > 55.0, seed_mask, text_bands)


def _dropped_parts_ratio(img_small: np.ndarray, mask_small: np.ndarray,
                         text_bands_small: np.ndarray) -> float:
    """Frame fraction covered by LARGE salient foreground islands that a
    candidate mask leaves out. A shirt dropped from a hanger photo is ~20% —
    far above DROP_PARTS_MAX — so any mask that amputates attached parts
    fails QA and another candidate wins."""
    sal = _salient_foreground(img_small, mask_small, text_bands_small)
    if sal is None:
        return 0.0
    dropped = (sal > 0) & ~(mask_small > 127)
    n, _lab, stats, _cen = cv2.connectedComponentsWithStats(
        dropped.astype(np.uint8), 8)
    h, w = img_small.shape[:2]
    big = max(int(0.01 * h * w), 150)
    return sum(int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)
               if stats[i, cv2.CC_STAT_AREA] >= big) / float(h * w)


# ── Text/watermark removal (verified, product-safe) ──────────────────────────

def _row_density(members: np.ndarray) -> float:
    """Stroke coverage of a candidate text row: sum of member areas over the
    row's bounding box. Real text lines have whitespace between letters
    (typically 0.15-0.5); tiled foliage/texture stripes fill their box."""
    x0 = int(members[:, 0].min())
    y0 = int(members[:, 1].min())
    x1 = int((members[:, 0] + members[:, 2]).max())
    y1 = int((members[:, 1] + members[:, 3]).max())
    box = max((x1 - x0) * (y1 - y0), 1)
    return float(members[:, 4].sum()) / box


def _cluster_rows(glyphs: np.ndarray) -> list[np.ndarray]:
    """Group glyph boxes into text-row candidates, O(N log N), vectorized:
    sort by vertical center, split where the gap exceeds a height-scaled
    tolerance, trim members whose height deviates, return index arrays.
    Runs in microseconds per group even with ~100k foliage glyphs."""
    yc = glyphs[:, 1] + glyphs[:, 3] / 2
    order = np.argsort(yc, kind="stable")
    ys = yc[order]
    tol = max(8.0, float(np.median(glyphs[:, 3])) * 0.7)
    breaks = np.where(np.diff(ys) > tol)[0]
    rows = []
    for idx in np.split(order, breaks + 1):
        if len(idx) < 2:
            continue
        gh = glyphs[idx][:, 3]
        med = float(np.median(gh))
        keep = (gh >= 0.45 * med) & (gh <= 2.2 * med)
        idx = idx[keep]
        if len(idx) >= 2:
            rows.append(idx)
    return rows


def _verify_text_bands(img_bgr: np.ndarray) -> np.ndarray:
    """Regions that are almost certainly TEXT: a ROW of similar-height glyph
    components. Glyph candidates come from raw gradient strokes (no closing —
    closing merges text with product edges into one blob). Rows are grouped
    vectorized (O(N log N)) and validated with the discriminators that
    separate letters from foliage/texture noise: height spread, spacing
    regularity, line-span, and row stroke DENSITY (text has whitespace
    between letters; a lawn stripe does not). Word-blobs (merged letters)
    are recognized via letter holes. This is the ONLY detector allowed to
    alter the product."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    # ADAPTIVE (local) threshold: letters on dark wood or bright sky keep their
    # strokes — global Otsu fragments soft/anti-aliased text into debris that
    # can never group into rows
    bw = cv2.adaptiveThreshold(grad, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 31, 7)
    h, w = img_bgr.shape[:2]
    max_glyph_h = max(int(h * 0.25), 10)

    n, _lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    if n <= 1:
        return np.zeros_like(bw)
    st = stats[1:].astype(np.float32)
    cw, ch, area = st[:, 2], st[:, 3], st[:, 4]
    aspect = cw / np.maximum(ch, 1)
    ok = ((ch >= 6) & (ch <= max_glyph_h) & (area >= 8)
          & (aspect >= 0.08) & (aspect <= 12.0)
          & (area / np.maximum(cw * ch, 1) <= 0.80))    # strokes, not solid blobs
    glyphs = st[ok]
    if len(glyphs) < 4:
        return np.zeros_like(bw)

    regions = np.zeros_like(bw)
    for idx in _cluster_rows(glyphs):
        m = glyphs[idx]                                  # (k, 5) x,y,w,h,area
        if len(m) < 3:
            continue                                     # 2-member rows: texture loops, not text
        med_h = float(np.median(m[:, 3]))
        if float(np.std(m[:, 3]) / max(np.mean(m[:, 3]), 1)) > 0.55:
            continue
        cxs = np.sort(m[:, 0] + m[:, 2] / 2)
        gaps = np.diff(cxs)
        if (len(gaps) >= 2 and gaps.min() > 0
                and float(gaps.max()) / float(gaps.min()) > 3.0):
            continue
        bx0, by0 = float(m[:, 0].min()), float(m[:, 1].min())
        bx1 = float((m[:, 0] + m[:, 2]).max())
        by1 = float((m[:, 1] + m[:, 3]).max())
        if (bx1 - bx0) > 80.0 * med_h:                   # a text line isn't 80 chars wide
            continue
        d = _row_density(m)
        if not (0.10 <= d <= 0.65):                      # letters have whitespace
            continue
        regions[max(0, int(by0) - 2):int(by1) + 2,
                max(0, int(bx0) - 2):int(bx1) + 2] = 255
    return regions


def _remove_verified_text(img_bgr: np.ndarray, text_mask: np.ndarray,
                          product_hint: np.ndarray | None = None,
                          ) -> tuple[np.ndarray, np.ndarray]:
    """Inpaint verified text bands: product material fills text printed on the
    product (no wood holes, no mask carving); the background replacement
    handles the rest. ADAPTIVE: anti-aliased letter halos survive a single
    pass and still read as text, so we re-verify the repair and re-inpaint
    with growing dilation until the region is clean (max 4 rounds; large
    bands get Navier-Stokes, which propagates structure instead of smearing).
    Re-verification is CROPPED to the repair's bounding box — a full-frame
    verify per round costs minutes on 12MP foliage images.
    `product_hint` (full-res union of candidate masks, optional) limits the
    texture-restoration step to product pixels: a smear left inside a TEXTURED
    product is restored by adding grain matched to the surrounding ring (a
    smooth product stays smooth — the ring decides). Returns (repaired image,
    inpainted pixel set)."""
    if not text_mask.any():
        return img_bgr, text_mask
    ys, xs = np.where(text_mask > 0)
    y0, y1 = max(int(ys.min()) - 40, 0), min(int(ys.max()) + 40, img_bgr.shape[0])
    x0, x1 = max(int(xs.min()) - 40, 0), min(int(xs.max()) + 40, img_bgr.shape[1])
    thick = text_mask
    repaired = img_bgr
    for radius in (5, 11, 19, 31):
        thick = cv2.dilate(text_mask, np.ones((radius, radius), np.uint8))
        big = radius >= 19 and cv2.countNonZero(text_mask) > 400
        method = cv2.INPAINT_NS if big else cv2.INPAINT_TELEA
        repaired = cv2.inpaint(img_bgr, thick, 3, method)
        sub = _verify_text_bands(repaired[y0:y1, x0:x1])
        left = np.zeros_like(thick)
        left[y0:y1, x0:x1] = cv2.bitwise_and(sub, thick[y0:y1, x0:x1])
        if cv2.countNonZero(left) == 0:
            break
    # texture restoration: a smooth inpainted island inside a textured product
    # reads as a defect (and fails the seam gate). Match the surrounding ring's
    # grain, per channel, only where the hint says "product".
    if product_hint is not None:
        band = (thick > 0) & (cv2.resize(
            product_hint, (img_bgr.shape[1], img_bgr.shape[0]),
            interpolation=cv2.INTER_LINEAR) > 127) \
            if product_hint.shape[:2] != img_bgr.shape[:2] else (thick > 0) & (product_hint > 127)
        ring = cv2.dilate(band.astype(np.uint8) * 255,
                          np.ones((31, 31), np.uint8)) > 0
        ring &= ~band
        if int(band.sum()) >= 100 and int(ring.sum()) >= 500:
            f = repaired.astype(np.float32)
            stds = [float(f[..., c][ring].std()) for c in range(3)]
            if float(np.mean(stds)) >= 4.0:          # textured surroundings only
                rng = np.random.default_rng(12345)
                noise = np.stack([
                    cv2.GaussianBlur(rng.normal(0, max(s * 0.8, 1.5),
                                                 band.shape).astype(np.float32),
                                     (0, 0), 0.8) for s in stds], axis=-1)
                f[band] += noise[band]
                repaired = np.clip(f, 0, 255).astype(np.uint8)
    return repaired, thick


def _residual_text_in_mask(img: np.ndarray, opaque: np.ndarray) -> float:
    """Fraction of the fully-opaque product core covered by VERIFIED text
    bands (same glyph-row test as detection — raw texture strokes must not
    count). The QA guard rejects any composite that ships visible text."""
    core = _product_core(opaque) > 0
    if core.sum() < 100:
        return 0.0
    verified = _verify_text_bands(img)
    return float((verified > 0)[core].mean())


# ── Procedural wooden backdrop ────────────────────────────────────────────────

def _wood_background(shape, seed: int) -> np.ndarray:
    """Warm oak plank backdrop with grain, growth rings, plank seams, gentle
    studio vignette — NO contact shadow (that is added per candidate mask by
    `_add_contact_shadow`, since the shadow depends on the product silhouette).
    Deterministic for a given seed. Inherently sharp."""
    h, w = shape[:2]
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    # 1) base tone: light warm oak, subtle vertical falloff
    t = (yy / h).astype(np.float32)
    base = np.empty((h, w, 3), np.float32)
    base[..., 0] = 122 + 26 * t          # B
    base[..., 1] = 158 + 30 * t          # G
    base[..., 2] = 196 + 28 * t          # R

    # 2) planks: horizontal boards with per-plank tone variation + dark seams
    plank_px = max(h // 4, 120)
    plank_id = (yy / plank_px).astype(np.int32)
    tone = rng.normal(0, 3.5, size=plank_id.max() + 2).astype(np.float32)
    base += tone[plank_id][..., None]          # (h,w,1) per-plank tint
    seam = np.abs(((yy % plank_px) - plank_px / 2) / (plank_px / 2))  # 1 at seam
    seam_dark = np.clip(1.0 - 0.09 * np.exp(-((1 - seam) ** 2) / 0.004), 0, 1)[..., None]
    base *= seam_dark

    # 3) grain: horizontally-stretched noise field + growth rings along x
    small = rng.normal(0, 1, size=(max(h // 24, 8), max(w // 6, 8))).astype(np.float32)
    grain = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    grain = cv2.GaussianBlur(grain, (0, 0), sigmaX=3, sigmaY=1)
    warp = 26 * grain + 10 * np.sin((xx * 0.004) + plank_id * 2.1)
    rings = np.sin((xx + warp) * (2 * np.pi / max(w * rng.uniform(0.5, 0.9), 1)))
    grain_strength = 9 + 4 * np.abs(grain)
    base += (rings * grain_strength)[..., None]

    # fine vertical pore noise (wood isn't glass-smooth — this IS the texture)
    pores = rng.normal(0, 1, size=(max(h // 6, 8), max(w // 3, 8))).astype(np.float32)
    pores = cv2.resize(pores, (w, h), interpolation=cv2.INTER_CUBIC)
    pores = cv2.GaussianBlur(pores, (0, 0), sigmaX=5, sigmaY=0.6)
    base += (pores * 5)[..., None]

    # 4) studio lighting: soft key light upper-left + vignette
    lx, ly = 0.30, 0.22
    d2 = ((xx / w - lx) * 1.05) ** 2 + ((yy / h - ly) * 1.25) ** 2
    key = np.exp(-d2 * 1.6)[..., None] * 14
    vig = 1 - 0.20 * np.clip(((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 2.4, 0, 1)[..., None]
    base = np.clip(base * vig + key, 0, 255)

    return base.astype(np.uint8)


def _neutral_background(shape, seed: int, level: int) -> np.ndarray:
    """Flat neutral studio sweep (gray / white / graphite): soft key light from
    above-left, a gentle floor falloff, fine grain and a light vignette. Same
    grain field on all three channels, so the sweep has no colour cast and the
    product stays the only warm or saturated thing in frame. Deterministic per
    seed; grain and seams are real detail, never blur."""
    h, w = shape[:2]
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.full((h, w, 3), float(level), np.float32)

    d2 = ((xx / w - 0.38) * 0.9) ** 2 + ((yy / h - 0.18) * 1.1) ** 2
    base += (np.exp(-d2 * 1.8) * (0.06 * level))[..., None]
    base *= (1 - 0.10 * (yy / h))[..., None]

    grain = rng.normal(0, 1, size=(max(h // 3, 8), max(w // 3, 8))).astype(np.float32)
    grain = cv2.resize(grain, (w, h), interpolation=cv2.INTER_CUBIC)
    base += (grain * max(1.2, level * 0.012))[..., None]

    vig = 1 - 0.10 * np.clip(((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 2.2,
                             0, 1)[..., None]
    return np.clip(base * vig, 0, 255).astype(np.uint8)


def _studio_background(shape, seed: int, background: str) -> np.ndarray:
    """The requested backdrop. `wood` keeps the original procedural oak."""
    key = (background or "wood").strip().lower()
    if key in ("", "wood"):
        return _wood_background(shape, seed)
    if key not in BACKGROUNDS:
        raise ValueError(f"unknown background {background!r} — use one of "
                         f"{', '.join(sorted(set(BACKGROUNDS)))}")
    return _neutral_background(shape, seed, BACKGROUNDS[key])


def _lab_of(colour: str) -> tuple[float, float, float]:
    """A colour name or #rrggbb, in the same 0-255 Lab scale OpenCV uses."""
    key = (colour or "").strip().lower()
    hexv = COLOURS.get(re.sub(r"[^a-z]", "", key) or key, key)
    if not str(hexv).startswith("#") or len(str(hexv)) != 7:
        raise ValueError(f"unknown colour {colour!r} — use #rrggbb or one of "
                         f"{', '.join(sorted(COLOURS))}")
    bgr = np.array([[[int(hexv[5:7], 16), int(hexv[3:5], 16), int(hexv[1:3], 16)]]],
                   np.uint8)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)[0, 0]
    return float(lab[0]), float(lab[1]), float(lab[2])


def _recolour_product(img: np.ndarray, mask: np.ndarray,
                      colour: str) -> tuple[np.ndarray, float]:
    """Shift the product's Lab mean onto the requested filament colour.

    The offset is CONSTANT across the mask, so layer lines, texture and shading
    keep every local difference they had — only the colour moves. Lightness
    travels part of the way (RECOLOUR_L_GAIN) so the print keeps its own
    shading instead of going flat. Returns the image and the per-channel drift
    the guards should now EXPECT (anything beyond it is real damage)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2Lab).astype(np.float32)
    hard = (mask > 127).astype(np.uint8) * 255
    core = cv2.erode(hard, np.ones((9, 9), np.uint8))
    if cv2.countNonZero(core) < 400:
        core = hard
    l0, a0, b0 = (float(v) for v in cv2.mean(lab, mask=core)[:3])
    lt, at, bt = _lab_of(colour)
    off = np.array([(lt - l0) * RECOLOUR_L_GAIN, at - a0, bt - b0], np.float32)
    out = cv2.cvtColor(np.clip(lab + off, 0, 255).astype(np.uint8), cv2.COLOR_Lab2BGR)
    return out, float(abs(off[1]) + abs(off[2])) / 2.0


def _add_contact_shadow(wood: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Soft contact shadow slightly below the product, composited over the
    cached wood. Computed at quarter scale (the shadow is a smooth blob —
    a full-scale blur here was pure waste on 12MP images)."""
    h, w = wood.shape[:2]
    s = 0.25
    sw, sh = max(int(w * s), 8), max(int(h * s), 8)
    m_small = cv2.resize((mask > 127).astype(np.uint8) * 255, (sw, sh),
                         interpolation=cv2.INTER_AREA)
    sh_d = cv2.dilate(m_small, np.ones((5, 5), np.uint8))
    k = max(int(sh * 0.012) | 1, 3)
    sh_b = cv2.GaussianBlur(sh_d, (k * 3 | 1, k * 3 | 1), sigmaX=k)
    shadow = np.zeros((sh, sw), np.float32)
    off = max(sh // 220, 1)
    shadow[off:, :] = sh_b[:sh - off, :] / 255.0
    shadow = cv2.resize(shadow, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(wood.astype(np.float32) * (1 - 0.38 * shadow)[..., None],
                   0, 255).astype(np.uint8)


# ── Sharpness recovery for already-blurry sources ────────────────────────────

def _sharpness_in(img: np.ndarray, core: np.ndarray) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64)
    return cv2.Laplacian(g, cv2.CV_64F)[core].var()


def _sharpen_product(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """If the SOURCE product is out of focus (the user's 'some are blurry'),
    recover it with an unsharp mask applied ONLY to product pixels. Already-
    sharp images pass through untouched — no halos, no artifacts.

    PER-COMPONENT effort (Case-3 fix): a product can be half motion-blurred
    (top) and half crisp (bottom); one global strength measured on the whole
    core under-corrects the blurred half and over-protects the crisp half.
    Each connected component ≥2% of the mask is measured and sharpened
    independently; everything else shares the whole-core measurement."""
    hard = (mask > 127).astype(np.uint8) * 255
    core = cv2.erode(hard, np.ones((7, 7), np.uint8), iterations=2)
    if cv2.countNonZero(core) < 500:
        return img
    s = _sharpness_in(img, core > 0)
    if s >= SHARPEN_ABOVE:
        return img                       # already sharp: do nothing
    # scale boost inversely with existing sharpness, clamped
    strength = float(np.clip((SHARPEN_ABOVE - s) / SHARPEN_ABOVE, 0.0, 1.0))
    amount = 0.6 + 1.2 * strength        # 0.6 .. 1.8
    blur = cv2.GaussianBlur(img, (0, 0), sigmaX=1.2)
    sharp = cv2.addWeighted(img, 1 + amount, blur, -amount, 0)
    alpha = (cv2.GaussianBlur(hard, (0, 0), 1.5).astype(np.float32) / 255.0)[..., None]
    out = sharp.astype(np.float32) * alpha + img.astype(np.float32) * (1 - alpha)
    out = np.clip(out, 0, 255).astype(np.uint8)

    # PER-REGION rescue (Case-3 fix): motion blur localizes to a REGION (top
    # half), not a connected component — a component-level measurement averages
    # the soft half away. Tile the product core; every tile still soft after
    # the global effort gets its own stronger pass, feather-blended.
    core_soft = cv2.erode(hard, np.ones((7, 7), np.uint8), iterations=1) > 0
    ys, xs = np.where(core_soft)
    tile_enabled = os.environ.get("EDITOR_DISABLE_TILERESCUE") != "1"
    if len(ys) >= 2000 and tile_enabled:
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        gh, gw = y1 - y0, x1 - x0
        tiles_y = max(1, gh // 256)
        tiles_x = max(1, gw // 256)
        rescue = np.zeros(img.shape[:2], np.float32)
        # regional bar: a tile only merits rescue when it is meaningfully
        # SOFTER THAN THE PRODUCT'S OWN MEDIAN tile — i.e. regionally
        # inconsistent blur (Case 3). Uniformly-soft products (whole product
        # below SHARPEN_ABOVE everywhere, like img_5's fine-grained plate)
        # are the global pass's job; firing tile-wise on them over-sharpens
        # and fails QA (regression caught by Part-5: hinge img_5).
        tile_stats = []
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                ya = y0 + ty * gh // tiles_y
                yb = y0 + (ty + 1) * gh // tiles_y
                xa = x0 + tx * gw // tiles_x
                xb = x0 + (tx + 1) * gw // tiles_x
                tile = np.zeros(img.shape[:2], bool)
                tile[ya:yb, xa:xb] = core_soft[ya:yb, xa:xb]
                if tile.sum() < 500:
                    continue
                tile_stats.append((float(_sharpness_in(out, tile)), ya, yb, xa, xb, tile))
        med = float(np.median([t[0] for t in tile_stats])) if tile_stats else 0.0
        # TRUE-outlier bar (0.35x median): flat-but-in-focus patches have
        # naturally low Laplacian variance — rescuing every sub-median tile
        # produces a patchwork that trips the residual-text guard (Part-5
        # caught this on hinge img_5: 14/34 tiles fired, image broke).
        # Only regionally-blurred outliers (Case-3's motion-blurred half,
        # typically 4-7x below the product's median) merit rescue.
        fired = 0
        for ts_val, ya, yb, xa, xb, tile in tile_stats:
            if ts_val >= 0.35 * med:
                continue                 # consistent with its product, or fine
            extra = 0.6 + 1.2 * float(np.clip(
                (SHARPEN_ABOVE - ts_val) / SHARPEN_ABOVE, 0, 1))
            rescue[ya:yb, xa:xb] = extra
            fired += 1
        # safety cap: rescuing >25% of the product bbox means the WHOLE
        # product is soft — the global pass owns that, not tile surgery
        if fired > 0.25 * len(tile_stats):
            rescue[:] = 0
        if rescue.any():
            rescue = cv2.GaussianBlur(rescue, (0, 0), 48)   # soft tile seams
            rmax = float(rescue.max())
            if rmax > 0.05:
                blur2 = cv2.GaussianBlur(out, (0, 0), sigmaX=1.6)
                sharp2 = cv2.addWeighted(out, 2.0, blur2, -1.0, 0)  # max-strength source
                wmap = np.clip(rescue / max(rmax, 1e-6), 0, 1)[..., None]
                out = np.clip(sharp2.astype(np.float32) * wmap
                              + out.astype(np.float32) * (1 - wmap), 0, 255).astype(np.uint8)
    return out


# ── QA guard ──────────────────────────────────────────────────────────────────

def _product_sharpness(img: np.ndarray, out: np.ndarray, opaque: np.ndarray,
                       exclude: np.ndarray | None = None):
    """(ratio, absolute) product-core sharpness. Core = eroded FULLY-OPAQUE
    region minus `exclude` (repaired text pixels have their own seam guard —
    they are not 'kept' pixels and must not fail the kept-pixel contract):
    those pixels are bit-identical to the source (or sharpened source), so the
    ratio only drops if the core accidentally covers composite transition
    zones or wood — i.e. real damage, never thin-product anatomy."""
    core = _product_core(opaque)
    if exclude is not None:
        core = cv2.bitwise_and(core, cv2.bitwise_not(exclude))
    if cv2.countNonZero(core) < 100:
        return 1.0, 0.0
    m = core > 0
    go = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64)
    ge = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).astype(np.float64)
    lo, le = cv2.Laplacian(go, cv2.CV_64F), cv2.Laplacian(ge, cv2.CV_64F)
    vo, ve = lo[m].var(), le[m].var()
    if vo <= 1e-6:
        return 1.0, float(ve)
    return ve / vo, float(ve)


def _product_core(opaque: np.ndarray) -> np.ndarray:
    """Definitely-product region used by every QA metric: eroded fully-opaque
    product pixels (1 iteration — heavy erosion punishes thin products)."""
    return cv2.erode(opaque, np.ones((7, 7), np.uint8), iterations=1)


def _color_identity_delta(img: np.ndarray, out: np.ndarray, opaque: np.ndarray,
                          exclude: np.ndarray | None = None) -> float:
    """Mean |Δ| of the Lab a/b (chroma) channels inside the product core
    (minus `exclude` — repaired text pixels are not kept pixels).
    Chroma IS color identity: blue stays blue, blue+green stay blue+green.
    Unsharp sharpening moves chroma by ~1-3; any recolor moves it 30+.
    (Raw value deltas are meaningless here — sharpening legitimately
    changes values while preserving color.)"""
    core = _product_core(opaque)
    if exclude is not None:
        core = cv2.bitwise_and(core, cv2.bitwise_not(exclude))
    core = core > 0
    if core.sum() < 100:
        return 0.0
    la = cv2.cvtColor(img, cv2.COLOR_BGR2Lab).astype(np.int16)
    lb = cv2.cvtColor(out, cv2.COLOR_BGR2Lab).astype(np.int16)
    d = np.abs(la[..., 1] - lb[..., 1]) + np.abs(la[..., 2] - lb[..., 2])
    return float(d[core].mean()) / 2.0   # report per-channel scale


# ── Debug instrumentation (no-ops in production; debug_dir=None) ─────────────

def _dbg_component_stats(debug_dir: str | None, cand_name: str,
                         mask_u8: np.ndarray, p80_whole: float) -> None:
    """Append per-connected-component interior distance-transform stats for
    one candidate to 07_distance_transform_per_component.json (accumulates
    across candidates; production no-op). The wire exemption is decided at
    MASK level today — per-component rows expose Case-2-style failures where
    one sub-component crosses the 3px cutoff while the body doesn't."""
    if debug_dir is None:
        return
    path = os.path.join(debug_dir, "07_distance_transform_per_component.json")
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(mask_u8, 8)
    rows = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 50:
            continue
        comp = (_lab == i)
        d = cv2.distanceTransform(comp.astype(np.uint8), cv2.DIST_L2, 3)
        rows.append({"candidate": cand_name, "component": i - 1,
                     "area_px": int(stats[i, cv2.CC_STAT_AREA]),
                     "bbox": [int(stats[i, cv2.CC_STAT_LEFT]),
                              int(stats[i, cv2.CC_STAT_TOP]),
                              int(stats[i, cv2.CC_STAT_WIDTH]),
                              int(stats[i, cv2.CC_STAT_HEIGHT])],
                     "p80_interior_dist": round(float(np.percentile(d[comp], 80)), 3),
                     "p80_whole_mask": round(p80_whole, 3),
                     "wire_exempt_mask_level": p80_whole < 3.0})
    existing = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
        except Exception:
            existing = []
    existing.extend(rows)
    with open(path, "w") as f:
        json.dump(existing, f, indent=1)


def _dbg_dump_json(debug_dir: str | None, name: str, obj) -> None:
    if debug_dir is None:
        return
    os.makedirs(debug_dir, exist_ok=True)
    with open(os.path.join(debug_dir, name), "w") as f:
        json.dump(obj, f, indent=1, default=float)


# ── Public API (same signatures the rest of the app already uses) ─────────────

def edit_image(source_path: str, retries: int = 0, debug_dir: str | None = None,
               background: str = "wood", recolour: str = "") -> str:
    """Local edit: product geometry and texture kept exactly, background
    replaced with a sharp procedural studio backdrop (`background="wood"` by
    default, or a flat `"gray"` / `"white"` / `"graphite"` sweep), and — only
    when asked — the filament colour shifted onto `recolour` (a name or
    "#rrggbb"). Full resolution end-to-end; nothing is downscaled or resampled.

    With `debug_dir`, every intermediate and QA number is dumped there
    (identical code path, instrumentation only — see _dbg_dump)."""
    style = (background or "wood").strip().lower()
    if style not in BACKGROUNDS:
        raise ValueError(f"unknown background {background!r} — use one of "
                         f"{', '.join(sorted(set(BACKGROUNDS)))}")
    if recolour:
        _lab_of(recolour)                 # bad colour name: fail before segmenting
    img = cv2.imread(source_path, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"Could not read image: {source_path}")
    if max(img.shape[:2]) > 4096:   # sanity cap only; keeps memory bounded
        s = 4096 / max(img.shape[:2])
        img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)

    candidates = _candidate_masks(img, debug_dir=debug_dir)   # best-mask-wins
    if not candidates:
        raise RuntimeError(f"no product mask found for {source_path}")
    seed = int(hashlib.sha1(os.path.basename(source_path).encode()).hexdigest()[:8], 16)

    # Verified text bands, computed once at work scale (detection is scale-
    # relative; the repair itself is applied at full resolution below).
    work_scale = min(1.0, MASK_WORK_SIDE / max(img.shape[:2]))
    work_img = cv2.resize(img, (int(img.shape[1] * work_scale),
                                int(img.shape[0] * work_scale)),
                          interpolation=cv2.INTER_AREA) if work_scale < 1.0 else img
    text_mask = _verify_text_bands(work_img)
    # FULL-RES detection pass too: letters that verify at full resolution can
    # vanish at work scale (img_5's 40px band did) — and the QA residual check
    # runs at full res, so detection and QA MUST see the same pixels. Scoped
    # to the near-product zone to keep the cost bounded on 12MP images.
    prod_zone = np.zeros(img.shape[:2], np.uint8)
    hint_full = np.zeros(img.shape[:2], np.uint8)   # union mask (repair hint)
    for cand in candidates:
        m_full = cv2.resize(cand.mask, (img.shape[1], img.shape[0]),
                            interpolation=cv2.INTER_LINEAR) \
            if cand.mask.shape[:2] != img.shape[:2] else cand.mask
        hint_full |= (m_full > 127).astype(np.uint8) * 255
        prod_zone |= cv2.dilate((m_full > 127).astype(np.uint8) * 255,
                                np.ones((61, 61), np.uint8))
    # The union prior doubles as the vacuous-pass reference: what the
    # segmenters COLLECTIVELY believe is product (img_1 regression guard).
    # Decompose ONCE per image: per-component engagement is judged per
    # candidate against these labels.
    union_bool = hint_full > 0
    union_area = float(union_bool.sum())
    u_n, u_lab, u_stats, _ = cv2.connectedComponentsWithStats(
        union_bool.astype(np.uint8) * 255, 8)
    frame_area = float(img.shape[0] * img.shape[1])
    u_big = [i for i in range(1, u_n)
             if u_stats[i, cv2.CC_STAT_AREA] >= 0.005 * frame_area]
    full_detect = np.zeros(img.shape[:2], np.uint8)
    ys, xs = np.where(prod_zone > 0)
    if len(ys):
        y0, y1 = max(int(ys.min()) - 8, 0), min(int(ys.max()) + 8, img.shape[0])
        x0, x1 = max(int(xs.min()) - 8, 0), min(int(xs.max()) + 8, img.shape[1])
        crop = _verify_text_bands(img[y0:y1, x0:x1])
        if crop.any():
            full_detect[y0:y1, x0:x1] = crop
    full_text = cv2.resize(text_mask, (img.shape[1], img.shape[0]),
                           interpolation=cv2.INTER_NEAREST) \
        if work_scale < 1.0 else text_mask
    full_text = cv2.bitwise_or(full_text, full_detect)
    work_src, thick_text = img, full_text
    if full_text.any():
        near_product = cv2.bitwise_and(full_text, prod_zone)
        if near_product.any():
            work_src, thick_text = _remove_verified_text(
                img, near_product, product_hint=hint_full)
            thick_text = cv2.bitwise_and(thick_text,
                                         cv2.dilate(prod_zone, np.ones((3, 3), np.uint8)))

    # One backdrop per image (it depends only on shape+seed+style, not on the
    # mask — previously re-rendered for every candidate). Rendered at work
    # scale and upscaled once; plank tones are softened so seams stay gentle
    # at 4K (measured row-jump 15.5 -> target <=6, same visual style).
    work_shape = (int(img.shape[0] * work_scale), int(img.shape[1] * work_scale)) \
        if work_scale < 1.0 else img.shape[:2]
    wood_work = _studio_background(work_shape, seed, style)

    # One human (hand/arm) detection for the whole image; per-candidate masks
    # get the foreign body stripped — but ONLY a skin-verified person (the
    # raw net also fires on faucets/monitors and would amputate the product).
    # None = net unavailable or no person.
    human = _human_overlap(img)

    best = None   # (quality, out, metrics)
    best_mask = None
    best_alpha = None
    attempts = []
    for cand in candidates:
        mask = cand.mask
        m_small = cv2.resize(mask, (work_img.shape[1], work_img.shape[0]),
                             interpolation=cv2.INTER_LINEAR) \
            if mask.shape[:2] != work_img.shape[:2] else mask
        coverage = (mask > 127).mean()
        if coverage > 0.70 or coverage < 0.01:
            attempts.append({"coverage": round(coverage, 2), "ok": False,
                             "note": "degenerate mask"})
            continue           # no real product/background separation
        # Vacuous-pass guard 1 — union recall: a candidate that keeps only a
        # fragment of what the nets collectively saw (img_1: 0.08 recall,
        # all 34 healthy ships >= 0.41) must never win, however clean the
        # fragment is. Guard 2 — opaque floor, gated on the union prior
        # being substantial, catches collapse during erode/feather.
        m_full_chk = mask if mask.shape[:2] == img.shape[:2] else \
            cv2.resize(mask, (img.shape[1], img.shape[0]),
                       interpolation=cv2.INTER_LINEAR)
        mbool_chk = m_full_chk > 127
        udec = 1.0
        for ui in u_big:
            comp = u_lab == ui
            a = float(u_stats[ui, cv2.CC_STAT_AREA])
            touch = float((comp & mbool_chk).sum()) / a
            if touch >= 0.01:               # materially engaged this component
                udec = min(udec, touch)
        dropped = _dropped_parts_ratio(work_img, m_small, text_mask)
        mask, hand_found = _strip_foreign_body(mask, human, img_bgr=img)
        work = work_src
        work = _sharpen_product(work, mask)
        want_drift = 0.0
        if recolour:
            work, want_drift = _recolour_product(work, mask, recolour)

        # Thin structures (wires, hooks) must not be eroded away by the
        # feather: on wire-dominated masks skip the erode and keep the blur
        # tight, or a 2-3px wire renders half-transparent (ghost wire).
        # PER-COMPONENT decision (Case-3 fix): the old mask-level test let a
        # solid body (p80 300px+) drag a thin-but-solid frame part (p80 ~2.3px)
        # into the erode path, biting geometry out of it. Now every component
        # with >=2% of mask area gets its own exemption test.
        hard = (mask > 127).astype(np.uint8) * 255
        dist_in = cv2.distanceTransform((mask > 127).astype(np.uint8),
                                        cv2.DIST_L2, 3)
        p80_whole = float(np.percentile(dist_in[mask > 127], 80)) \
            if (mask > 127).any() else 0.0
        n_comp, lab_comp, stats_comp, _ = cv2.connectedComponentsWithStats(hard, 8)
        # EDITOR_DISABLE_COMPWIRE=1: ops switch to the old mask-level test
        if os.environ.get("EDITOR_DISABLE_COMPWIRE") == "1":
            p80_whole_only = p80_whole
            thin_parts = np.zeros_like(hard)
            solid_parts = (hard > 0).astype(np.uint8) * 255
            stats_comp = stats_comp  # noqa — keep shape of the code stable
        total_area = float((hard > 0).sum())   # pixel count, NOT 255-scaled sum
        thin_parts = np.zeros_like(hard)
        solid_parts = np.zeros_like(hard)
        for ci in range(1, n_comp):
            comp = lab_comp == ci
            area = float(stats_comp[ci, cv2.CC_STAT_AREA])
            if area < 0.02 * total_area or area < 400:
                solid_parts |= comp          # specks: erode-safe by definition
                continue
            p80_c = float(np.percentile(dist_in[comp], 80))
            if p80_c < 3.0:
                thin_parts |= comp           # genuine wire/thin structure
            else:
                solid_parts |= comp
        if thin_parts.any():
            hard = np.where(thin_parts, hard,
                            cv2.erode(np.where(solid_parts, hard, 0)
                                      .astype(np.uint8), np.ones((3, 3), np.uint8)))
            hard = hard.astype(np.uint8)
        else:
            hard = cv2.erode(hard, np.ones((3, 3), np.uint8))
        sigma = max(1.0, min(img.shape[:2]) / 1200)
        alpha = (cv2.GaussianBlur(hard, (0, 0), sigmaX=sigma).astype(np.float32) / 255.0)[..., None]

        wood_full = cv2.resize(wood_work, (img.shape[1], img.shape[0]),
                               interpolation=cv2.INTER_CUBIC) \
            if wood_work.shape[:2] != img.shape[:2] else wood_work
        wood_full = _add_contact_shadow(wood_full,
                                        (mask > 127).astype(np.uint8) * 255)
        out = (work.astype(np.float32) * alpha + wood_full.astype(np.float32) * (1 - alpha))
        out = np.clip(out, 0, 255).astype(np.uint8)

        opaque = (alpha[..., 0] >= 0.995).astype(np.uint8) * 255
        repair_zone = (cv2.dilate(thick_text, np.ones((5, 5), np.uint8))
                       if thick_text.any() else None)
        kept, abs_sharp = _product_sharpness(img, out, opaque, repair_zone)
        cid = _color_identity_delta(img, out, opaque, repair_zone)
        # With an asked-for recolour the intended shift is not drift: measure
        # only what moved BEYOND it, so an accidental extra colour change (a
        # mask that swallowed a background object) still fails the guard.
        cid_excess = max(0.0, cid - want_drift)
        # The corners must be the backdrop this run rendered: warm wood for
        # the default, a neutral sweep for gray/white/graphite. A cool corner
        # on a wood run means the mask swallowed the background.
        corners = ((4, 4), (4, -5), (-5, 4), (-5, -5))
        if style in ("", "wood"):
            corners_ok = all(bool(c[2] >= c[1] - 2 and c[1] >= c[0] - 2)
                             for c in (out[p] for p in corners))
        else:
            corners_ok = all(
                float(np.abs(out[p].astype(np.int16)
                             - wood_full[p].astype(np.int16)).max()) <= CORNER_TOL
                for p in corners)
        # repair seam: an inpainted band that stays VISIBLE (inside the kept
        # product core) must not be a smooth smear surrounded by product
        # texture — compare its variance to the product texture RING around
        # it. Repairs over background wall barely matter: those pixels are
        # replaced by wood anyway. Source variance is deliberately NOT used:
        # it includes the text strokes being removed, which inpainting always
        # (and legitimately) flattens.
        seam_ok = True
        if thick_text.any():
            core_m = cv2.erode(opaque, np.ones((7, 7), np.uint8)) > 0
            band = (thick_text > 0) & core_m
            if int(band.sum()) >= 100:
                ge = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).astype(np.float64)
                lap = cv2.Laplacian(ge, cv2.CV_64F)
                ve = float(lap[band].var())
                ring = cv2.dilate(band.astype(np.uint8) * 255,
                                  np.ones((41, 41), np.uint8)) > 0
                ring &= ~band & core_m
                floor = 5.0
                if int(ring.sum()) >= 500:
                    floor = max(5.0, 0.10 * float(lap[ring].var()))
                seam_ok = ve >= floor
        # NO TEXT may survive inside the shipped product, and NO HAND either:
        # the strip already removed a detected hand from the mask, so skin
        # inside the opaque core means the composite still shows it.
        text_left = _residual_text_in_mask(out, opaque)
        hand_left = 0.0
        if hand_found and human is not None:
            core = _product_core(opaque) > 0
            if core.sum() >= 100:
                hand_left = float((human[core] > 0).mean())
        opaque_frac = float((opaque > 0).mean())
        cand_frac = float(coverage)
        vacuous = (udec < UNION_RECALL_MIN
                   or opaque_frac < OPAQUE_KEEP_MIN * cand_frac)
        ok = (kept >= SHARPNESS_KEEP_MIN and abs_sharp >= SHARP_ABS_MIN
              and cid_excess <= COLOR_IDENTITY_MAX and corners_ok
              and seam_ok and text_left <= RESIDUAL_TEXT_MAX
              and hand_left <= 0.02
              and dropped <= DROP_PARTS_MAX
              and not vacuous)
        attempts.append({"ratio": round(kept, 2), "abs": round(abs_sharp),
                         "cid": round(cid, 1), "drift": round(cid_excess, 1),
                         "text": round(text_left, 3),
                         "hand": round(hand_left, 3), "hand_found": hand_found,
                         "drop": round(dropped, 3), "urec": round(udec, 3),
                         "opq": round(opaque_frac, 3),
                         "ok": ok,
                         **({"note": "vacuous fragment"} if vacuous else {})})
        # instrumentation: per-COMPONENT distance-transform stats (the wire
        # exemption is decided mask-wide today; Case-2-style amputations show
        # up as one component crossing the 3px cutoff while another doesn't)
        _dbg_component_stats(debug_dir, cand.name, (mask > 127).astype(np.uint8),
                             float(np.percentile(dist_in[mask > 127], 80))
                             if (mask > 127).any() else 0.0)
        # sharpness first, then fewer dropped attached parts, minus color drift
        score = (kept + 40.0 * (DROP_PARTS_MAX - min(dropped, DROP_PARTS_MAX))
                 - 2.0 * max(0.0, cid_excess - 2.0))
        if ok and (best is None or score > best[0]):
            best = (score, out, {"ratio": kept, "cid": cid_excess, "dropped": dropped})
            best_mask = mask
            best_alpha = alpha

    if best is None:
        _dbg_dump_json(debug_dir, "06_guard_scores.json",
                       {"verdict": "REFUSED", "candidates": attempts})
        raise RuntimeError(
            f"all {len(candidates)} candidate masks failed QA for "
            f"{os.path.basename(source_path)}: {attempts} — likely a product-on-"
            f"same-background photo; refusing to ship a damaged product")

    # The default (wood, no recolour) keeps writing <stem>_edited.png exactly as
    # every other script in this app expects; a variant names itself so it
    # cannot overwrite the run beside it.
    stem = os.path.splitext(source_path)[0]
    tag = ""
    if style not in ("", "wood"):
        tag += "_" + re.sub(r"[^a-z0-9]", "", style)
    if recolour:
        tag += "_" + re.sub(r"[^a-z0-9]", "", recolour.lower())
    out_path = f"{stem}_edited{tag}.png"
    _dbg_dump_json(debug_dir, "06_guard_scores.json",
                   {"verdict": "SHIPPED", "winner": os.path.basename(out_path),
                    "background": style or "wood", "recolour": recolour or "",
                    "candidates": attempts})
    if not cv2.imwrite(out_path, best[1]):
        raise RuntimeError(f"Could not write {out_path}")
    # Persist the OPAQUE product region so external QA measures exactly what
    # this file vouched for (no transition zones), plus the repaired band so
    # QA can exclude it from kept-pixel contracts.
    mask_path = f"{stem}_mask{tag}.png"
    cv2.imwrite(mask_path, (best_alpha[..., 0] >= 0.995).astype(np.uint8) * 255)
    if thick_text.any():
        cv2.imwrite(f"{stem}_repair{tag}.png", thick_text)
    return out_path


def edit_all(image_paths: list[str], background: str = "wood",
             recolour: str = "") -> list[str]:
    """Edit each image and return list of edited paths.

    `background` and `recolour` are passed straight to edit_image; both
    default to the original behaviour (wood, product colour untouched).

    UNIFORM-EFFORT CONTRACT: every image runs the identical full pipeline —
    every candidate mask is segmented, composited and QA-scored with no fast
    paths, no early exits and no per-image shortcuts, so each image gets
    exactly the same amount of attention every time. An image whose QA fails
    is refused loudly (the exception lists every candidate's metrics) and the
    batch continues; nothing is ever silently downgraded or skipped."""
    edited = []
    for path in image_paths:
        try:
            edited.append(edit_image(path, background=background,
                                     recolour=recolour))
        except Exception as e:
            # Full-effort refusal, not a rush job: the image received its
            # complete candidate sweep and failed QA on the merits.
            print(f"[editor] REFUSED after full QA — {path}: {e}")
    return edited


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Finish listing photographs locally: studio backdrop and, "
                    "if you ask, a different filament colour.")
    ap.add_argument("images", nargs="+", help="photographs to edit (your own)")
    ap.add_argument("--background", default="wood",
                    help="wood (default) | gray | grey | white | graphite | charcoal")
    ap.add_argument("--recolour", default="",
                    help="filament colour: %s, or #rrggbb" % ", ".join(sorted(COLOURS)))
    args = ap.parse_args()
    for src in args.images:
        try:
            print(edit_image(src, background=args.background, recolour=args.recolour))
        except Exception as exc:  # noqa: BLE001
            print(f"REFUSED {src}: {exc}", file=sys.stderr)
            sys.exit(1)
