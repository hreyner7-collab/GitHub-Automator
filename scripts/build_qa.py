"""Build a self-contained QA report page (base64-embedded images) for the editor.

Run:  python scripts/build_qa.py
Writes: downloads/_qa_report.json (metrics for every edited image)
        _qa/index.html in the preview workspace (self-contained, embeds images)
"""
import base64
import json
import os
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DL = os.path.join(BASE, "downloads")
sys.path.insert(0, BASE)

from editor import edit_image, _product_core, _candidate_masks  # noqa: E402

PREVIEW_WS = os.path.join(os.environ.get("QA_WS", r"C:\Users\henry\Documents\free\app\Cruality Pipeline"), "_qa")


def eroded(m, it=1):
    # Same erosion the editor uses when vouching for a file (1 iter) — a heavier
    # one would collapse thin products to nothing and fall back to full-frame.
    return cv2.erode((m > 127).astype(np.uint8) * 255, np.ones((7, 7), np.uint8), iterations=it) > 0


def measure_mask(o, src_path):
    """The editor's OWN saved mask when available (QA must measure exactly what
    the editor vouched for); fall back to the candidate union."""
    mask_path = os.path.splitext(src_path)[0] + "_mask.png"
    if os.path.exists(mask_path):
        m = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        if m is not None:
            return m
    cands = _candidate_masks(o)
    if not cands:
        return None
    return np.max([c.mask for c in cands], axis=0).astype(np.uint8)


def b64(path, max_side=None, q=80):
    img = cv2.imread(path)
    if img is None:
        return ""
    if max_side and max(img.shape[:2]) > max_side:
        s = max_side / max(img.shape[:2])
        img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


def main():
    os.makedirs(PREVIEW_WS, exist_ok=True)
    paths = sorted(set(glob_images()))
    paths = [p for p in paths if "_edited" not in p]

    report = []
    for src in paths:
        base = src.rsplit(".", 1)[0]
        out = base + "_edited.png"
        rec = {"file": os.path.basename(src), "ok": False, "error": ""}
        try:
            if not os.path.exists(out):
                edit_image(src)          # only edit when missing — QA measures files as-is
            o = cv2.imread(src)
            e = cv2.imread(out)
            if o is None or e is None:
                raise RuntimeError("unreadable image")
            mask = measure_mask(o, src)
            if mask is None:
                raise RuntimeError("no product mask")
            core = eroded(mask)
            if core.sum() < 500:
                core = mask > 127      # thin product: measure the opaque region as-is
            # exclude the repaired text band (kept-pixel contract only — the
            # repair has its own seam check), exactly like the editor vouches
            repair_path = os.path.join(DL, base + "_repair.png")
            if os.path.exists(repair_path):
                rep = cv2.imread(repair_path, cv2.IMREAD_GRAYSCALE)
                if rep is not None and rep.shape == core.shape:
                    excl = cv2.dilate(rep, np.ones((5, 5), np.uint8)) > 0
                    trimmed = core & ~excl
                    if trimmed.sum() >= 100:
                        core = trimmed
            og = cv2.cvtColor(o, cv2.COLOR_BGR2GRAY).astype(np.float64)
            eg = cv2.cvtColor(e, cv2.COLOR_BGR2GRAY).astype(np.float64)
            lo = cv2.Laplacian(og, cv2.CV_64F)
            le = cv2.Laplacian(eg, cv2.CV_64F)
            vo, ve = lo[core].var(), le[core].var()
            rec["sharp_src"] = round(float(vo))
            rec["sharp_out"] = round(float(ve))
            rec["ratio"] = round(float(ve) / max(float(vo), 1e-6), 2)
            # color identity = Lab chroma delta (sharpening barely moves chroma;
            # any recolor moves it hugely) — same promise the editor enforces
            la = cv2.cvtColor(o, cv2.COLOR_BGR2Lab).astype(np.int16)
            lb = cv2.cvtColor(e, cv2.COLOR_BGR2Lab).astype(np.int16)
            dc = (np.abs(la[..., 1] - lb[..., 1]) + np.abs(la[..., 2] - lb[..., 2]))[core]
            rec["color_delta"] = round(float(dc.mean()) / 2.0, 1)
            c = e[4, 4]
            rec["wood"] = bool(c[2] >= c[1] >= c[0])
            rec["ok"] = rec["ratio"] >= 0.9 and rec["color_delta"] <= 8 and rec["wood"]
        except Exception as ex:
            rec["error"] = str(ex)[:120]
        report.append(rec)

    with open(os.path.join(DL, "_qa_report.json"), "w") as f:
        json.dump(report, f, indent=1)

    n_ok = sum(1 for r in report if r["ok"])
    fails = [r for r in report if not r["ok"]]

    # ---- flagged rows ----
    rows = "".join(
        f"<tr><td>{r['file'][:40]}</td><td>{r.get('sharp_src','-')}</td><td>{r.get('sharp_out','-')}</td>"
        f"<td>{r.get('ratio','-')}</td><td>{r.get('color_delta','-')}</td><td>{r.get('wood','-')}</td>"
        f"<td class=bad>FLAGGED</td></tr>"
        for r in fails
    ) or "<tr><td colspan=7 class=pass>none — all images passed every check</td></tr>"

    # ---- contact sheet ----
    TH = 420
    names = [r["file"] for r in report]
    tiles = []
    for n in names:
        base = n.rsplit(".", 1)[0]
        for tag, p in (("EDITED", os.path.join(DL, base + "_edited.png")),
                       ("ORIG", os.path.join(DL, n))):
            img = cv2.imread(p)
            if img is None:
                img = np.full((TH, TH, 3), 40, np.uint8)
                cv2.putText(img, "missing", (30, TH // 2), 0, 1, (200,), 2)
            else:
                s = TH / max(img.shape[:2])
                img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)))
            canvas = np.full((TH, TH, 3), 24, np.uint8)
            y, x = (TH - img.shape[0]) // 2, (TH - img.shape[1]) // 2
            canvas[y:y + img.shape[0], x:x + img.shape[1]] = img
            cv2.putText(canvas, f"{tag} {n[-11:-4]}", (8, 24), 0, 0.55,
                        (0, 255, 255) if tag == "EDITED" else (255, 255, 255), 1)
            tiles.append(canvas)
    rows2 = [np.hstack(tiles[i * 2:(i + 1) * 2]) for i in range(len(tiles) // 2)]
    sheet = os.path.join(DL, "_qa_sheet.png")
    cv2.imwrite(sheet, np.vstack(rows2))
    sheet_uri = b64(sheet, max_side=1100, q=80)

    # ---- 100% center crops of flagged images ----
    crop_html = []
    for i, r in enumerate(fails):
        n = r["file"]
        base = n.rsplit(".", 1)[0]
        o = cv2.imread(os.path.join(DL, n))
        e = cv2.imread(os.path.join(DL, base + "_edited.png"))
        if o is None or e is None:
            continue
        h, w = o.shape[:2]
        side = min(h, w) // 2
        y0, x0 = h // 2 - side // 2, w // 2 - side // 2
        tiles = []
        for tag, im in (("ORIGINAL", o), ("EDITED", e)):
            crop = im[y0:y0 + side, x0:x0 + side]
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
            uri = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
            tiles.append(f'<figure><img src="{uri}"><figcaption>{tag} — {n[:40]}</figcaption></figure>')
        crop_html.append(
            f"<h2>Flagged #{i+1}: {n[:44]} (ratio {r.get('ratio')}, colr {r.get('color_delta')})</h2>"
            f"<div class=row>{''.join(tiles)}</div>")

    html = f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>Editor QA</title><style>
body{{background:#101010;color:#ddd;font-family:Segoe UI,sans-serif;padding:24px;max-width:1200px}}
h1{{color:#fff;font-size:1.3rem}}h2{{color:#fff;font-size:.95rem;margin-top:26px}}
table{{border-collapse:collapse;margin-top:10px}}td,th{{border:1px solid #333;padding:5px 9px;font-size:.8rem;text-align:left}}
th{{color:#888;text-transform:uppercase;font-size:.65rem;letter-spacing:.06em}}
.pass{{color:#7bd67b;font-weight:600}}.bad{{color:#ff8a80;font-weight:600}}
.row{{display:flex;gap:18px;flex-wrap:wrap}}figure{{margin:0}}
img{{width:420px;border:1px solid #333}}figcaption{{color:#888;font-size:.72rem;margin-top:4px}}
.sheet{{max-width:1050px;width:auto}}
</style></head><body>
<h1>Editor QA — {len(report)} real edited images: {n_ok}/{len(report)} PASS, {len(fails)} flagged</h1>
<h2>Flagged-image metrics</h2>
<table><tr><th>file</th><th>sharp src</th><th>sharp out</th><th>ratio</th><th>color &Delta;</th><th>wood</th><th>verdict</th></tr>{rows}</table>
<h2>Full-frame contact sheet</h2>
<p style="color:#888;font-size:.75rem">EDITED tiles = pipeline output; ORIG = sources.</p>
<img class=sheet src="{sheet_uri}">
<h2>100% center crops of flagged images (blur judged here)</h2>
{''.join(crop_html) or '<p class=pass>No flagged images.</p>'}
</body></html>"""

    out_html = os.path.join(PREVIEW_WS, "index.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"QA page: {n_ok}/{len(report)} PASS, {len(fails)} flagged -> {out_html}")
    for r in report:
        print(("PASS" if r["ok"] else "BAD "), f"{r['file'][:36]:38s}",
              f"ratio={r.get('ratio','-'):>5} colr={r.get('color_delta','-'):>5} wood={r.get('wood','-')}")


def glob_images():
    import glob
    return set(glob.glob(os.path.join(DL, "*_img_[0-9].*")) +
               glob.glob(os.path.join(DL, "*_img_1[0-9].*")))


if __name__ == "__main__":
    main()
