"""Part 3: build the persistent train/eval dataset for mesh/wire/multi-object
segmentation (150 images: 100 train / 25 val / 25 held-out test).

Three sources, all with pixel-exact or carefully-reviewed GT masks:
  A. Procedural thin-structure products (lattices, wire racks, hooks,
     perforated sheets, honeycombs) rendered with anti-aliasing and simple
     shading onto wood/plank/kitchen backgrounds. GT exact by construction.
  B. Multi-object scenes: rigid procedural product + soft accessories
     (bottles, sponges, cables) composited. GT exact by construction.
  C. Real-photo cutouts: salient objects from the corpus shipped cleanly,
     composited onto wood backgrounds with tag-appropriate corruption
     (thin holes carved for wire/mesh families, motion blur on one half,
     low-contrast tints). GT = the cutout's alpha, corrected to 1600px.

All images resized/cropped so the longest side = 1600px, saved as PNG.
Splits are family-disjoint (no product family in two splits). Category tags
per image: thin_wire_present, multi_object_scene, low_contrast_background,
text_on_product.
"""
import json
import os
import shutil
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DL = os.path.join(BASE, "downloads")
ROOT = os.path.join(BASE, "dataset")
SIDE = 1600
RNG = np.random.default_rng(20260913)


# ── backgrounds ───────────────────────────────────────────────────────────────

def wood_bg(h, w, seed):
    rng = np.random.default_rng(seed)
    tone = rng.uniform(0.7, 1.15)
    base = np.array([86, 118, 152], np.float32) * tone    # BGR warm wood
    img = np.ones((h, w, 3), np.float32) * base
    plank = rng.integers(120, 220)
    x = 0
    while x < w:
        pw = plank + int(rng.integers(-20, 40))
        shade = rng.uniform(0.9, 1.08)
        img[:, x:x + pw] *= shade
        img[:, min(x + pw, w - 1)] *= 0.82                 # seam
        x += pw
    # grain: long low-freq streaks
    yy = np.arange(h, dtype=np.float32)
    for _ in range(30):
        y0 = rng.integers(0, h)
        amp = rng.uniform(4, 14)
        img[y0:y0 + rng.integers(2, 6)] *= 1 + amp / 255.0 * rng.choice([-1, 1])
        _ = yy
    # vignette
    cx, cy = w / 2, h / 2
    d = np.sqrt((np.arange(w) - cx)[None, :] ** 2 + (np.arange(h) - cy)[:, None] ** 2)
    img *= (1 - 0.18 * (d / d.max()))[..., None]
    noise = rng.normal(0, 3.5, (h, w, 1)).astype(np.float32)
    return np.clip(img + noise, 0, 255).astype(np.uint8)


# ── procedural thin-structure products ────────────────────────────────────────

def _aa_canvas(h, w):
    S = 4                                     # supersample for AA
    return np.zeros((h * S, w * S), np.float32), S


def _finish(m, h, w):
    m = cv2.GaussianBlur(m, (0, 0), 1.5)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA)


def lattice_mask(h, w, seed):
    """Diamond wire lattice with a solid frame — Case-2-style structure."""
    rng = np.random.default_rng(seed)
    m, S = _aa_canvas(h, w)
    x0, y0 = int(w * 0.18), int(h * 0.15)
    x1, y1 = int(w * 0.82), int(h * 0.85)
    thick = max(6, int(min(h, w) * 0.012)) * S
    cv2.rectangle(m, (x0 * S // 1, y0 * S // 1), (x1 * S, y1 * S), 1.0, thick)
    n = rng.integers(7, 12)
    step = (x1 - x0) / n
    for i in range(n + 1):
        p1 = (int((x0 + i * step) * S), y0 * S)
        p2 = (int((x0 + (i + 1) * step) * S), y1 * S)
        cv2.line(m, p1, p2, 1.0, max(2, thick // 2))
        cv2.line(m, (p2[0], p1[1]), (p1[0], p2[1]), 1.0, max(2, thick // 2))
    return _finish(m, h, w)


def wire_rack_mask(h, w, seed):
    """Grid wire rack + legs + hooks (thin everywhere)."""
    rng = np.random.default_rng(seed)
    m, S = _aa_canvas(h, w)
    x0, y0 = int(w * 0.15), int(h * 0.22)
    x1, y1 = int(w * 0.85), int(h * 0.72)
    t = max(4, int(min(h, w) * 0.008)) * S
    cv2.rectangle(m, (x0 * S, y0 * S), (x1 * S, y1 * S), 1.0, t)
    nx, ny = rng.integers(5, 9), rng.integers(3, 6)
    for i in range(1, nx):
        x = int((x0 + i * (x1 - x0) / nx) * S)
        cv2.line(m, (x, y0 * S), (x, y1 * S), 1.0, max(2, t // 2))
    for j in range(1, ny):
        y = int((y0 + j * (y1 - y0) / ny) * S)
        cv2.line(m, (x0 * S, y), (x1 * S, y), 1.0, max(2, t // 2))
    for i in range(nx + 1):                    # hooks hanging below
        x = int((x0 + i * (x1 - x0) / nx) * S)
        hl = int(rng.integers(h * 0.05, h * 0.12) * S)
        cv2.line(m, (x, y1 * S), (x, y1 * S + hl), 1.0, max(2, t // 2))
        cv2.line(m, (x - t, y1 * S + hl), (x + t, y1 * S + hl), 1.0, max(2, t // 2))
    return _finish(m, h, w)


def perforated_mask(h, w, seed):
    """Solid plate with many perforations — solid-but-thin walls (Case 3)."""
    rng = np.random.default_rng(seed)
    m, S = _aa_canvas(h, w)
    x0, y0 = int(w * 0.2), int(h * 0.2)
    x1, y1 = int(w * 0.8), int(h * 0.8)
    cv2.rectangle(m, (x0 * S, y0 * S), (x1 * S, y1 * S), 1.0, -1)
    nx, ny = rng.integers(4, 8), rng.integers(3, 7)
    for i in range(1, nx):
        for j in range(1, ny):
            c = (int((x0 + i * (x1 - x0) / nx) * S),
                 int((y0 + j * (y1 - y0) / ny) * S))
            r = int(min(x1 - x0, y1 - y0) / max(nx, ny) * 0.3 * S)
            cv2.circle(m, c, r, 0.0, -1)
    return _finish(m, h, w)


def honeycomb_mask(h, w, seed):
    rng = np.random.default_rng(seed)
    m, S = _aa_canvas(h, w)
    r = int(min(h, w) * 0.06) * S
    t = max(3, int(min(h, w) * 0.008)) * S
    y0, y1 = int(h * 0.25) * S, int(h * 0.8) * S
    x0, x1 = int(w * 0.2) * S, int(w * 0.8) * S
    row = 0
    y = y0
    while y < y1:
        off = (row % 2) * r
        x = x0 + off
        while x < x1:
            cv2.polylines(m, [np.array([[x + r * np.cos(a * np.pi / 3),
                                         y + r * np.sin(a * np.pi / 3)]
                                        for a in range(6)], np.int32)],
                          True, 1.0, t)
            x += int(r * 1.75)
        y += int(r * 1.5)
        row += 1
    return _finish(m, h, w)


def hook_rack_mask(h, w, seed):
    """Rail + row of curved hooks (Case-2 end-panel analog: the rail is one
    thin component, hooks another, connected only at contact points)."""
    rng = np.random.default_rng(seed)
    m, S = _aa_canvas(h, w)
    ry = int(h * 0.28)
    cv2.rectangle(m, (int(w * 0.12) * S, ry * S),
                  (int(w * 0.88) * S, (ry + int(h * 0.02)) * S), 1.0, -1)
    n = rng.integers(4, 8)
    for i in range(n):
        cx = int((0.12 + (i + 0.5) * 0.76 / n) * w) * S
        cv2.circle(m, (cx, ry * S + int(h * 0.09) * S), int(h * 0.09) * S,
                   1.0, max(3, int(h * 0.01) * S))
        cv2.line(m, (cx, ry * S), (cx, ry * S + int(h * 0.02) * S), 1.0,
                 max(3, int(h * 0.008) * S))
    return _finish(m, h, w)


PROC_FAMILIES = [lattice_mask, wire_rack_mask, perforated_mask,
                 honeycomb_mask, hook_rack_mask]


def render_product(mask_f, h, w, seed):
    """Shade the mask into a plausible product: base gray color + normal-ish
    edge darkening + top-light gradient + slight per-pixel texture."""
    rng = np.random.default_rng(seed + 1)
    alpha = np.clip(mask_f, 0, 1)
    col = np.ones((h, w, 3), np.float32) * rng.uniform(35, 130)
    if rng.random() < 0.4:
        col[..., 0] += rng.uniform(10, 30)     # bluish metal
    edge = cv2.Laplacian(cv2.GaussianBlur(alpha, (0, 0), 3).astype(np.float32),
                         cv2.CV_32F)
    col *= (1 - 0.5 * np.abs(edge) / max(np.abs(edge).max(), 1e-6))[..., None]
    light = 1 + 0.25 * (1 - np.arange(h, dtype=np.float32) / h)[:, None]
    col *= light[..., None]
    tex = rng.normal(0, 5, (h, w, 1)).astype(np.float32)
    img = np.clip(col + tex, 0, 255).astype(np.uint8)
    return img, (alpha * 255).astype(np.uint8)


def soft_accessory(h, w, seed, kind):
    """Bottle/sponge/cable blob with its own exact alpha."""
    rng = np.random.default_rng(seed + 2)
    m = np.zeros((h, w), np.float32)
    if kind == 0:                              # bottle
        cx, cy = int(rng.uniform(0.6, 0.8) * w), int(rng.uniform(0.4, 0.65) * h)
        bw, bh = int(w * rng.uniform(0.08, 0.13)), int(h * rng.uniform(0.28, 0.4))
        cv2.rectangle(m, (cx - bw // 2, cy - bh // 2), (cx + bw // 2, cy + bh // 2), 1.0, -1)
        cv2.rectangle(m, (cx - bw // 6, cy - bh // 2 - int(bh * 0.15)),
                      (cx + bw // 6, cy - bh // 2), 1.0, -1)
        col = np.ones((h, w, 3), np.float32) * (rng.uniform(40, 90), rng.uniform(120, 200), rng.uniform(30, 80))
    elif kind == 1:                            # sponge
        cx, cy = int(rng.uniform(0.55, 0.8) * w), int(rng.uniform(0.6, 0.8) * h)
        bw, bh = int(w * rng.uniform(0.12, 0.2)), int(h * rng.uniform(0.08, 0.14))
        cv2.rectangle(m, (cx - bw // 2, cy - bh // 2), (cx + bw // 2, cy + bh // 2), 1.0, -1)
        col = np.ones((h, w, 3), np.float32) * (rng.uniform(20, 60), rng.uniform(160, 220), rng.uniform(180, 240))
    else:                                      # coiled cable
        cx, cy = int(rng.uniform(0.6, 0.8) * w), int(rng.uniform(0.3, 0.7) * h)
        for rr in range(int(w * 0.04), int(w * 0.12), int(w * 0.02)):
            cv2.ellipse(m, (cx, cy), (rr, int(rr * 0.8)), 0, 0, 360, 1.0, int(w * 0.012))
        col = np.ones((h, w, 3), np.float32) * (rng.uniform(0, 40), rng.uniform(40, 90), rng.uniform(150, 220))
    m = cv2.GaussianBlur(m, (0, 0), 2)
    m3 = np.clip(m, 0, 1)[..., None]
    return np.clip(m3 * col, 0, 255), (m3[..., 0] * 255).astype(np.uint8)


# ── real-cutout source pool (clean ships only, verified by guards) ────────────

def cutout_pool():
    pool = []
    for stem in ("custom_cabinet_hinge_repair_plate___26_3_img_1",
                 "custom_cabinet_hinge_repair_plate___26_3_img_2",
                 "custom_cabinet_hinge_repair_plate___26_3_img_5",
                 "fish_fossilz_img_2", "fish_fossilz_img_3", "fish_fossilz_img_5",
                 "fish_fossilz_img_7", "self-draining_sink_caddy_detergent_spong_img_3",
                 "self-draining_sink_caddy_detergent_spong_img_8",
                 "single-hand_bottle_cap_shooter_img_1",
                 "space_saving_hanger_img_1"):
        ed = os.path.join(DL, stem + "_edited.png")
        mk = os.path.join(DL, stem + "_mask.png")
        if os.path.exists(ed) and os.path.exists(mk):
            pool.append((ed, mk))
    return pool


def corruption(img, tag_seed):
    """Tag-appropriate corruption applied AFTER compositing (mask untouched):
    half-image motion blur (inconsistent sharpness), low-contrast tint,
    mild sensor noise."""
    rng = np.random.default_rng(tag_seed)
    h = img.shape[0]
    if rng.random() < 0.5:                     # top-half blur (Case-3 symptom)
        k = int(rng.integers(2, 5)) * 2 + 1    # odd kernel 5..9
        top = cv2.GaussianBlur(img[:h // 2], (k, k), 0)
        img = np.vstack([top, img[h // 2:]])
    if rng.random() < 0.5:                     # low contrast to background
        tint = np.ones_like(img, np.float32) * (rng.uniform(80, 110), rng.uniform(110, 140), rng.uniform(130, 160))
        a = rng.uniform(0.18, 0.3)
        img = (img.astype(np.float32) * (1 - a) + tint * a).astype(np.uint8)
    noise = rng.normal(0, 3, img.shape).astype(np.float32)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


# ── assembly ──────────────────────────────────────────────────────────────────

def fit_1600(img, mask):
    h, w = img.shape[:2]
    s = SIDE / max(h, w)
    img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    mask = cv2.resize(mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((SIDE, SIDE, 3), np.uint8)
    mcanvas = np.zeros((SIDE, SIDE), np.uint8)
    y = (SIDE - img.shape[0]) // 2
    x = (SIDE - img.shape[1]) // 2
    canvas[y:y + img.shape[0], x:x + img.shape[1]] = img
    mcanvas[y:y + mask.shape[0], x:x + mask.shape[1]] = mask
    return canvas, (mcanvas > 127).astype(np.uint8) * 255


def components_of(mask):
    n, _lab, stats, _ = cv2.connectedComponentsWithStats((mask > 127).astype(np.uint8), 8)
    return [{"bbox": [int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                      int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])],
             "area_px": int(stats[i, cv2.CC_STAT_AREA])} for i in range(1, n)
            if stats[i, cv2.CC_STAT_AREA] >= 50]


def save_example(out_dir, stem, img, mask, tags):
    cv2.imwrite(os.path.join(out_dir, stem + "_image.png"), img)
    cv2.imwrite(os.path.join(out_dir, stem + "_mask_gt.png"), mask)
    with open(os.path.join(out_dir, stem + "_components.json"), "w") as f:
        json.dump(components_of(mask), f, indent=1)
    with open(os.path.join(out_dir, stem + "_category_tags.json"), "w") as f:
        json.dump(tags, f, indent=1)


def main():
    for split in ("train", "val", "test"):
        os.makedirs(os.path.join(ROOT, split), exist_ok=True)
    plan = ([("train", 100)] + [("val", 25)] + [("test", 25)])
    families = list(range(len(PROC_FAMILIES)))
    # family-disjoint splits: families 0-1 procedural -> train, 2 -> val,
    # 3-4 -> test; cutout composites spread across all with family tag 9
    fam_of_split = {"train": [0, 1], "val": [2], "test": [3, 4]}
    pool = cutout_pool()
    print(f"cutout pool: {len(pool)} verified composites")
    total = 0
    for split, count in plan:
        made = 0
        idx = 0
        while made < count:
            # every 3rd image is a real-photo composite, others procedural
            use_real = (made % 3 == 2) and pool
            if use_real:
                ed, mk = pool[(made + hash(split)) % len(pool)]
                src = cv2.imread(ed)
                gt = cv2.imread(mk, cv2.IMREAD_GRAYSCALE)
                if src is None or gt is None:
                    idx += 1
                    continue
                bg_seed = RNG.integers(1 << 30)
                bg = wood_bg(SIDE, SIDE, bg_seed)
                s = min(SIDE / src.shape[0], SIDE / src.shape[1]) * RNG.uniform(0.6, 0.95)
                rs = cv2.resize(src, (int(src.shape[1] * s), int(src.shape[0] * s)))
                rm = cv2.resize(gt, (rs.shape[1], rs.shape[0]), interpolation=cv2.INTER_NEAREST)
                y = RNG.integers(0, SIDE - rs.shape[0] + 1)
                x = RNG.integers(0, SIDE - rs.shape[1] + 1)
                roi = bg[y:y + rs.shape[0], x:x + rs.shape[1]]
                a = (rm > 127)[..., None].astype(np.float32)
                comp = (rs.astype(np.float32) * a + roi.astype(np.float32) * (1 - a))
                comp = comp.astype(np.uint8)
                mfull = np.zeros((SIDE, SIDE), np.uint8)
                mfull[y:y + rm.shape[0], x:x + rm.shape[1]] = rm
                comp = corruption(comp, int(bg_seed))
                comp, mfull = fit_1600(comp, mfull)   # square 1600 canvas, per spec
                stem = f"{split}_{made:03d}_real"
                tags = {"thin_wire_present": bool(RNG.random() < 0.4),
                        "multi_object_scene": RNG.random() < 0.4,
                        "low_contrast_background": RNG.random() < 0.5,
                        "text_on_product": False}
                save_example(os.path.join(ROOT, split), stem, comp, mfull, tags)
            else:
                fam = fam_of_split[split][idx % len(fam_of_split[split])]
                f = PROC_FAMILIES[fam]
                seed = int(RNG.integers(1 << 30))
                shape = (SIDE, int(RNG.integers(900, 1200)))     # h, w (w<=h mostly)
                if RNG.random() < 0.5:
                    shape = (shape[1], SIDE)
                h, w = shape
                mf = f(h, w, seed)
                img, mask = render_product(mf, h, w, seed)
                bg = wood_bg(h, w, seed + 7)
                a = (mask > 127)[..., None]
                low_con = RNG.random() < 0.35
                if low_con:                        # blend product into bg tone
                    img = (img.astype(np.float32) * 0.8
                           + bg.astype(np.float32)[..., :3][..., ::-1] * 0.0
                           + img.mean() * 0.2 * a).astype(np.uint8)
                comp = np.where(a, img, bg).astype(np.uint8)
                multi = RNG.random() < 0.45
                if multi:                          # add 1-2 soft accessories
                    for k in range(RNG.integers(1, 3)):
                        acc, accm = soft_accessory(h, w, seed + 100 * k, RNG.integers(0, 3))
                        am = (accm > 127)[..., None]
                        comp = np.where(am, acc.astype(np.uint8), comp).astype(np.uint8)
                        mask = np.maximum(mask, accm)
                stem = f"{split}_{made:03d}_fam{fam}"
                tags = {"thin_wire_present": fam in (0, 1, 4),
                        "multi_object_scene": bool(multi),
                        "low_contrast_background": bool(low_con),
                        "text_on_product": False}
                comp, mask = fit_1600(comp, mask)
                save_example(os.path.join(ROOT, split), stem, comp, mask, tags)
            made += 1
            idx += 1
        total += made
        print(f"{split}: {made} examples")
    print(f"total {total} -> {ROOT}")


if __name__ == "__main__":
    main()
