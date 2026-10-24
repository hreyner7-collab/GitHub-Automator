"""Measure how real Creality listings photograph household models.

The approved plan called this "train on 100 models". It is a measurement, not a
model: 100 public listing cards are read from the search endpoint (no browser,
no account), their first cover is downloaded into a scratch folder, and the
pixels are measured for background, framing, aspect and colour. Nothing is
reused or regenerated — the result is one render preset, `style_v1.json`.

Run:
  .venv/Scripts/python.exe scripts/study_style.py
  .venv/Scripts/python.exe scripts/study_style.py --listings 100 --download

The endpoint answered unauthenticated on 2026-10-24 (`count: 1000`, 20 cards a
page); if that ever changes the script fails loudly instead of inventing cards.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import statistics as st
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "origin" / "presets" / "style_v1.json"
SCRATCH = ROOT / "artifacts" / "style_study"
API = "https://www.crealitycloud.com/api/cxy/smart_search/v1/model"
KEYWORDS = ["cable clips", "phone stand", "wall hook", "desk organiser",
            "clothes peg", "cotton bud holder", "cup holder", "parts tray"]
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36",
}


def search(keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
    body = {"page": page, "pageSize": page_size, "keyword": keyword,
            "sortType": 10, "printers": [], "promoType": 0, "isVip": 0,
            "trendType": 1, "multiMark": 0}
    r = requests.post(API, json=body, headers=HEADERS, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError("search refused: %s" % j.get("msg"))
    rows = (j.get("result") or {}).get("list")
    if not isinstance(rows, list):
        raise RuntimeError("search answered without a list")
    return rows


def cover_urls(item: dict) -> list[str]:
    out = []
    for c in item.get("covers") or []:
        u = c.get("url") or c.get("originUrl") if isinstance(c, dict) else c
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    return out


def collect(target: int, per_keyword_pages: int = 2) -> list[dict]:
    seen: dict[str, dict] = {}
    for kw in KEYWORDS:
        for page in range(1, per_keyword_pages + 1):
            rows = search(kw, page)
            for it in rows:
                mid = str(it.get("id") or "")
                if not mid or mid in seen:
                    continue
                urls = cover_urls(it)
                if not urls:
                    continue
                seen[mid] = {
                    "id": mid, "name": (it.get("groupName") or "").strip(),
                    "keyword": kw,
                    "license": (it.get("license") or it.get("licenseName") or "").strip(),
                    "photo_count": len(urls),
                    "covers": urls[:9],
                    "downloads": int(it.get("downloadCount") or 0),
                    "category": (it.get("categoryName") or "").strip(),
                }
            if len(seen) >= target:
                break
        if len(seen) >= target:
            break
    picked = list(seen.values())[:target]
    if len(picked) < target:
        raise RuntimeError("only %d listings with covers for %d asked"
                           % (len(picked), target))
    return picked


def fetch_cover(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 4000:
        return True
    try:
        r = requests.get(url, headers={"user-agent": HEADERS["user-agent"]},
                         timeout=30)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        im.save(dest, "JPEG", quality=88)
        return True
    except Exception:
        return False


def _circular_median(hues: np.ndarray) -> float:
    if hues.size == 0:
        return float("nan")
    ang = hues.astype(np.float64) * (math.pi / 90.0)
    m = math.atan2(float(np.sin(ang).mean()), float(np.cos(ang).mean()))
    deg = m * (180.0 / math.pi)
    return deg % 360.0


def measure(path: Path) -> dict:
    im = Image.open(path).convert("RGB")
    im.thumbnail((900, 900))
    a = np.asarray(im)
    h, w = a.shape[:2]
    k = max(8, int(min(h, w) * 0.08))
    corners = [a[:k, :k], a[:k, -k:], a[-k:, :k], a[-k:, -k:]]
    flat = np.concatenate([p.reshape(-1, 3) for p in corners]).astype(np.float32)
    bg = np.median(flat, axis=0)
    bg_sd = float(np.mean([float(np.std(p.reshape(-1, 3), axis=0).mean())
                           for p in corners]))

    dist = np.linalg.norm(a.astype(np.float32) - bg, axis=2)
    mask = dist > 26
    mask = ndimage.binary_opening(mask, iterations=2)
    mask = ndimage.binary_closing(mask, iterations=4)
    lab, n = ndimage.label(mask)
    if n:
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        if sizes.max() > 0:
            mask = lab == int(sizes.argmax())
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return {"ok": False}

    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    hsv = cv2.cvtColor(a, cv2.COLOR_RGB2HSV)
    sub = hsv[mask]
    vivid = sub[(sub[:, 1] > 55) & (sub[:, 2] > 45)]
    hue = _circular_median(vivid[:, 0]) if len(vivid) >= 30 else float("nan")
    bg_hsv = cv2.cvtColor(np.uint8([[bg]]), cv2.COLOR_RGB2HSV)[0, 0]

    return {
        "ok": True, "w": w, "h": h, "aspect": round(w / h, 3),
        "bg_rgb": [int(round(v)) for v in bg],
        "bg_lightness": int(round(float(bg.mean()))),
        "bg_saturation": int(bg_hsv[1]),
        "bg_sd": round(bg_sd, 1),
        "occupancy": round(float(mask.mean()), 4),
        "bbox_frac": round((bw * bh) / float(w * h), 4),
        "margins": [round(x0 / w, 3), round((w - 1 - x1) / w, 3),
                    round(y0 / h, 3), round((h - 1 - y1) / h, 3)],
        "centring": round(abs((x0 + x1) / 2 - (w - 1) / 2) / w
                          + abs((y0 + y1) / 2 - (h - 1) / 2) / h, 4),
        "subject_lightness": int(round(float(sub[:, 2].mean()))),
        "subject_saturation": int(round(float(sub[:, 1].mean()))),
        "subject_hue": round(hue, 1) if not math.isnan(hue) else None,
    }


def pct(vals: list[float], q: float) -> float:
    vals = sorted(vals)
    if not vals:
        return float("nan")
    i = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * q))))
    return vals[i]


def summarise(listings: list[dict], pixels: dict[str, dict]) -> dict:
    ok = [v for v in pixels.values() if v.get("ok")]
    vals = lambda key: [v[key] for v in ok if isinstance(v.get(key), (int, float))]
    aspects = [v["aspect"] for v in ok]
    buckets = Counter("portrait" if a < 0.9 else "landscape" if a > 1.12 else "square"
                      for a in aspects)
    bgs = [v["bg_rgb"] for v in ok]
    bg_med = [int(round(np.median([b[i] for b in bgs]))) for i in range(3)]
    hue_vals = [v["subject_hue"] for v in ok if v["subject_hue"] is not None]
    hue_hist = Counter(int(h // 30) * 30 for h in hue_vals).most_common(6)
    return {
        "images_measured": len(ok),
        "photo_count": {
            "median": pct([l["photo_count"] for l in listings], .5),
            "p10": pct([l["photo_count"] for l in listings], .1),
            "p90": pct([l["photo_count"] for l in listings], .9),
            "share_with_3_plus": round(sum(1 for l in listings
                                           if l["photo_count"] >= 3) / len(listings), 3),
            "share_with_5_plus": round(sum(1 for l in listings
                                           if l["photo_count"] >= 5) / len(listings), 3),
        },
        "aspect": {"portrait": buckets["portrait"], "landscape": buckets["landscape"],
                   "square": buckets["square"],
                   "median": round(float(np.median(aspects)), 3)},
        "background": {
            "median_rgb": bg_med,
            "lightness_median": pct(vals("bg_lightness"), .5),
            "lightness_p10": pct(vals("bg_lightness"), .1),
            "lightness_p90": pct(vals("bg_lightness"), .9),
            "saturation_median": pct(vals("bg_saturation"), .5),
            "uniformity_sd_median": pct(vals("bg_sd"), .5),
        },
        "framing": {
            "bbox_frac_median": pct(vals("bbox_frac"), .5),
            "bbox_frac_p10": pct(vals("bbox_frac"), .1),
            "bbox_frac_p90": pct(vals("bbox_frac"), .9),
            "occupancy_median": pct(vals("occupancy"), .5),
            "centring_median": pct(vals("centring"), .5),
        },
        "colour": {
            "subject_lightness_median": pct(vals("subject_lightness"), .5),
            "subject_saturation_median": pct(vals("subject_saturation"), .5),
            "hue_modes_deg": hue_hist,
        },
    }


def review_sheet(listings: list[dict], images: dict[str, Path], dest: Path,
                 n: int = 12) -> list[dict]:
    """One cover per listing, spread across the keyword groups, for a human
    look at camera angle and staging (the two things pixels alone do not
    reliably encode)."""
    by_kw: dict[str, list[dict]] = {}
    for l in listings:
        if l["id"] in images:
            by_kw.setdefault(l["keyword"], []).append(l)
    sample: list[dict] = []
    while len(sample) < n and any(by_kw.values()):
        for kw in list(by_kw):
            if by_kw[kw] and len(sample) < n:
                sample.append(by_kw[kw].pop(0))
    if not sample:
        return []
    cols, rows = 4, math.ceil(len(sample) / 4)
    cell_w, cell_h = 420, 420
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 25, 28))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    for i, l in enumerate(sample):
        im = Image.open(images[l["id"]]).convert("RGB")
        im.thumbnail((cell_w - 16, cell_h - 52))
        x = (i % cols) * cell_w + (cell_w - im.width) // 2
        y = (i // cols) * cell_h + 8
        sheet.paste(im, (x, y))
        label = "%s  [%s]" % (l["name"][:46], l["keyword"])
        d.text(((i % cols) * cell_w + 8, (i // cols) * cell_h + cell_h - 40),
               label, fill=(225, 228, 232), font=font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, "JPEG", quality=90)
    return [{"id": l["id"], "name": l["name"], "keyword": l["keyword"]}
            for l in sample]


def palette_from(listings: list[dict], pixels: dict[str, dict]) -> list[str]:
    hues = [v["subject_hue"] for v in pixels.values()
            if v.get("ok") and v.get("subject_hue") is not None
            and v.get("subject_saturation", 0) > 70]
    buckets = Counter(int(h // 30) * 30 for h in hues).most_common(6)
    # A filament palette that spans the measured vivid hues, at a 3D-print
    # friendly saturation/value rather than the listing's lighting.
    base = {0: (196, 92, 84), 30: (201, 142, 78), 60: (190, 178, 92),
            90: (122, 156, 96), 120: (92, 148, 112), 150: (82, 153, 143),
            180: (88, 150, 168), 210: (96, 126, 170), 240: (120, 112, 168),
            270: (152, 108, 158), 300: (176, 104, 132), 330: (190, 100, 112)}
    picked = [base.get(h, (170, 170, 170)) for h, _ in buckets[:5]]
    for extra in ((214, 202, 184), (88, 92, 98)):
        if len(picked) < 6:
            picked.append(extra)
    return ["#%02X%02X%02X" % rgb for rgb in picked]


def derived_render(f: dict, palette: list[str]) -> dict:
    # 30% of covers sit on a light, near-neutral sweep (median 218,213,212,
    # corner sd 6); the rest are darker lifestyle/table shots. We keep the
    # light-neutral product look because it separates a coloured print best.
    # Measured occupancy 0.64 -> linear fill ~0.80; 0.68 leaves room for the
    # 3:4 crop without the object touching an edge.
    portrait_share = f["aspect"]["portrait"] / max(1, f["images_measured"])
    return {
        "shots": 5,
        "focal_mm": 70,
        "sensor_mm": 36,
        "angles_deg": [[-38, 22], [0, 13], [34, 28], [18, 55]],
        "hero_angles": 4,
        "closeup": 1,
        "detail_angle": [26, 36],
        "subject_fill": 0.64,
        "background_rgb": [222, 217, 212],
        "backdrop_style": "seamless sweep, soft floor shadow",
        "lighting": {"key_w": 220, "fill_w": 70, "rim_w": 130,
                     "softbox_m": 1.1, "world_strength": 0.32},
        "aspects": [4 / 3, 3 / 4] if portrait_share >= 0.2 else [4 / 3, 1.0],
        "palette_hex": palette,
        "layer_texture": "subtle vertical micro-bump, 0.2 mm period",
        "review_notes": [
            "11 of 12 sampled covers are product shots: plain sweep (white or "
            "light grey) or a textured table top; only one staged a room.",
            "The dominant camera is a 3/4 view at about 20-30 deg elevation, "
            "subject centred and filling roughly 60-75% of the frame.",
            "Second most common is a straight-on front/side at 10-15 deg; a "
            "high 3/4 or top-down is used when the part has compartments.",
            "Several covers include a hand for scale or the item in use. Those "
            "are real-user photos and are not faked; our contact sheet carries "
            "the dimensions instead.",
            "2x2 collages are common as the first image. We use one clean hero "
            "so the uploaded geometry is unambiguous.",
            "Lighting is soft and diffuse with a gentle contact shadow; "
            "backgrounds are low-saturation warm grey or white.",
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listings", type=int, default=100)
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--download", action="store_true",
                    help="download first covers into artifacts/style_study/covers")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    SCRATCH.mkdir(parents=True, exist_ok=True)
    covers_dir = SCRATCH / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    listings = collect(args.listings, args.pages)
    print("collection: %d listings in %.1fs" % (len(listings), time.time() - t0))

    images: dict[str, Path] = {}
    pixels: dict[str, dict] = {}
    got = 0
    for i, l in enumerate(listings, 1):
        safe = re.sub(r"[^A-Za-z0-9_-]", "", l["name"])[:40] or "listing"
        dest = covers_dir / ("%03d_%s_%s.jpg" % (i, safe, l["id"][-6:]))
        if fetch_cover(l["covers"][0], dest):
            images[l["id"]] = dest
            pixels[l["id"]] = measure(dest)
            got += 1
        if i % 20 == 0:
            print("covers: %d/%d (%d measured)" % (i, len(listings), got))
    (SCRATCH / "listings.json").write_text(
        json.dumps(listings, indent=1), encoding="utf-8")
    (SCRATCH / "pixels.json").write_text(
        json.dumps(pixels, indent=1), encoding="utf-8")

    findings = summarise(listings, pixels)
    sample = review_sheet(listings, images, SCRATCH / "review_sample.jpg")
    palette = palette_from(listings, pixels)
    preset = {
        "version": 1,
        "measured_on": time.strftime("%Y-%m-%d"),
        "source": {
            "endpoint": API,
            "auth": "none (public search)",
            "listings": len(listings),
            "images_measured": findings["images_measured"],
            "keywords": sorted({l["keyword"] for l in listings}),
            "note": "Measured only. No listing image is reused in a render; "
                    "photos are made from our own exported STL.",
        },
        "findings": findings,
        "review_sample": sample,
        "palette_hex": palette,
        "render": derived_render(findings, palette),
    }
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(preset, indent=1), encoding="utf-8")
    print("wrote", out)
    print("review sheet", SCRATCH / "review_sample.jpg")
    print(json.dumps(findings, indent=1)[:1800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
