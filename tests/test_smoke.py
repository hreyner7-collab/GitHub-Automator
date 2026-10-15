"""Offline smoke tests — no network, no API keys, no browser logins required.

Run with:  python -m pytest tests/ -q
"""
import asyncio
import os
import zipfile

import pytest

# Make sure the repo root is importable regardless of pytest's launch dir
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Imports ───────────────────────────────────────────────────────────────────

def test_modules_import():
    import main  # noqa: F401
    import discover  # noqa: F401
    import downloader  # noqa: F401
    import editor  # noqa: F401
    import uploader  # noqa: F401


# ── FastAPI surface ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


def test_index_serves_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Creality" in r.text
    assert "<html" in r.text.lower()


def test_file_route_404_for_missing(client):
    r = client.get("/file/definitely-not-here-12345.stl")
    assert r.status_code == 404


def test_file_route_blocks_traversal(client):
    # Even if an encoded traversal sneaks through the router, it must not
    # serve files from outside downloads/.
    r = client.get("/file/..%2F..%2Fmain.py")
    assert r.status_code in (400, 404)
    assert "import" not in r.text


def test_discover_rejects_unknown_site(client):
    r = client.post("/discover", json={"site": "not-a-site", "count": 5})
    assert r.status_code == 400


def test_fetch_rejects_bad_url_gracefully(client):
    # A generic URL that can't be downloaded should produce a 400, not a crash.
    # (Uses a reserved TEST-NET address so it fails fast even with network.)
    r = client.post("/fetch", json={"url": "http://192.0.2.1/nonexistent.stl"})
    assert r.status_code in (400, 422)


# ── downloader helpers ────────────────────────────────────────────────────────

def test_slug():
    from downloader import _slug
    assert _slug("Hello World! 3D-Print") == "hello_world__3d-print"
    assert len(_slug("x" * 200)) == 40


def test_clean_title():
    from downloader import _clean_title
    assert _clean_title("Cool Model by someuser | Printables") == "Cool Model"
    assert _clean_title("Thing - Thingiverse") == "Thing"


def test_fetch_dispatches_unknown_scheme():
    from downloader import fetch
    # Non-matching hosts fall through to _fetch_generic, which will try (and
    # fail) to download — the result must still be a well-formed dict.
    out = fetch("http://192.0.2.1/model.stl")
    assert isinstance(out, dict)
    assert "name" in out and "image_paths" in out


# ── uploader helpers ──────────────────────────────────────────────────────────

def test_extract_models_from_zip(tmp_path):
    from uploader import _extract_models
    zpath = tmp_path / "model.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("part1.stl", "solid x\nendsolid x" + " " * 2048)
        z.writestr("notes.txt", "not a model")
        z.writestr("folder/part2.3mf", "x" * 2048)
    out = _extract_models(str(zpath))
    names = sorted(os.path.basename(p) for p in out)
    assert "part1.stl" in names and "part2.3mf" in names
    assert not any(n.endswith(".txt") for n in names)


def test_extract_models_rejects_non_model(tmp_path):
    from uploader import _extract_models
    p = tmp_path / "readme.txt"
    p.write_text("hello")
    assert _extract_models(str(p)) == []


def test_model_exts_cover_creality_formats():
    from uploader import MODEL_EXTS
    for ext in (".stl", ".3mf", ".step", ".obj"):
        assert ext in MODEL_EXTS


# ── editor ────────────────────────────────────────────────────────────────────

def test_editor_preserves_product_colors_and_sharpness(monkeypatch, tmp_path):
    """Product pixels keep their color and detail; background becomes wood."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import cv2
    import numpy as np
    import editor

    # synthetic: textured blue + green product on a white background
    img = np.full((240, 240, 3), 240, np.uint8)
    cv2.circle(img, (120, 120), 70, (150, 120, 40), -1)      # blue-ish base
    cv2.circle(img, (95, 100), 25, (60, 180, 60), -1)        # green sub-shape
    # texture detail so sharpness is measurable
    noise = np.random.default_rng(7).normal(0, 14, (240, 240)).astype(np.float32)
    img = np.clip(img.astype(np.float32) + noise[..., None], 0, 255).astype(np.uint8)
    src = tmp_path / "test_product.png"
    cv2.imwrite(str(src), img)

    out_path = editor.edit_image(str(src))
    out = cv2.imread(out_path)
    assert out is not None

    # product keeps BOTH its colors (sample inside each region)
    blue_px = out[120, 120]
    green_px = out[95, 100]
    assert blue_px[0] > blue_px[2] and blue_px[0] > blue_px[1], f"blue region recolored: {blue_px}"
    assert green_px[1] > green_px[2] and green_px[1] > green_px[0], f"green region recolored: {green_px}"

    # wood background: corners warm (R ≥ G ≥ B), wood-toned, not white/flat
    corner = out[4, 4]
    assert corner[2] >= corner[1] >= corner[0], f"background not warm wood: {corner}"
    assert 90 <= int(corner[2]) <= 245, f"background not wood-toned: {corner}"

    # product-core sharpness preserved (no blur): the contract is about the
    # PRODUCT, not the frame — replacing a noisy background with clean wood
    # legitimately lowers whole-frame variance.
    prod_o = img[70:170, 70:170].astype(np.float64)
    prod_e = out[70:170, 70:170].astype(np.float64)
    s_o = cv2.Laplacian(cv2.cvtColor(prod_o.astype(np.uint8), cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    s_e = cv2.Laplacian(cv2.cvtColor(prod_e.astype(np.uint8), cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    assert s_e >= 0.9 * s_o, f"product lost sharpness: {s_e:.0f} vs {s_o:.0f}"


def test_editor_removes_watermark_text(monkeypatch, tmp_path):
    """NO TEXT ANYWHERE: baked-in watermark text — even printed across the
    product — must be gone in the edited output (the BISTAKE regression)."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import cv2
    import numpy as np
    import editor

    img = np.full((400, 400, 3), 60, np.uint8)           # dark studio background
    cv2.circle(img, (200, 210), 95, (150, 120, 40), -1)  # blue product
    # FDM-style layer texture (smoothed noise — raw static would smear under
    # any inpainting and prove nothing)
    tex = np.random.default_rng(11).normal(0, 18, (400, 400)).astype(np.float32)
    tex = cv2.GaussianBlur(tex, (0, 0), 1.1)
    img = np.clip(img.astype(np.float32) + tex[..., None], 0, 255).astype(np.uint8)
    cv2.putText(img, "BISTAKE", (95, 228), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                (255, 255, 255), 2)                      # watermark ACROSS the product
    cv2.putText(img, "SAMPLE", (110, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                (255, 255, 255), 2)                      # watermark on the background
    src = tmp_path / "watermarked.png"
    cv2.imwrite(str(src), img)

    # precondition: the detector really sees this text
    assert editor._verify_text_bands(img).any(), "synthetic watermark not detected"

    out_path = editor.edit_image(str(src))
    out = cv2.imread(out_path)
    assert out is not None

    # no verified text bands survive anywhere in the output
    assert not editor._verify_text_bands(out).any(), "text survived the edit"

    # the letter strokes are gone: no near-white cluster remains where
    # BISTAKE was printed across the product
    prod = out[160:260, 90:320]
    white = int(((prod > 235).all(axis=2)).sum())
    assert white < 50, f"letter pixels survived on the product: {white}"

    # product is still blue where the text wasn't
    px = out[285, 200]
    assert px[0] > px[2] and px[0] > px[1], f"product recolored: {px}"


def test_editor_keeps_attached_product_parts(monkeypatch, tmp_path):
    """MULTI-PART PRODUCTS (the space-saving-hanger regression): a garment
    hanging on the product, a mounting rail, thin wire hooks — parts the
    segmenters drop — must ship WITH the product, while wall text next to
    them is still removed."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import cv2
    import numpy as np
    import editor

    img = np.full((420, 420, 3), 232, np.uint8)          # neutral wall
    cv2.rectangle(img, (0, 120), (420, 132), (245, 245, 245), -1)   # white rail
    cv2.circle(img, (210, 150), 38, (150, 120, 40), -1)  # blue rigid hero
    # soft navy garment hanging off the hero (the part nets drop)
    garment = np.zeros((420, 420), np.float32)
    cv2.rectangle(garment, (178, 185), (242, 330), 1.0, -1)
    garment = cv2.GaussianBlur(garment, (0, 0), 3)
    gcol = np.zeros((420, 420, 3), np.float32)
    gcol[..., 0] = 95
    gcol[..., 1] = 60
    gcol[..., 2] = 45                                    # navy-ish
    tex = np.random.default_rng(3).normal(0, 10, (420, 420)).astype(np.float32)
    img = (img.astype(np.float32) * (1 - garment[..., None])
           + (gcol + tex[..., None]) * garment[..., None]).clip(0, 255).astype(np.uint8)
    cv2.putText(img, "SPACE SAVING", (18, 60), cv2.FONT_HERSHEY_SIMPLEX,
                0.9, (30, 30, 30), 2)                    # wall text top-left
    src = tmp_path / "multipart.png"
    cv2.imwrite(str(src), img)

    out_path = editor.edit_image(str(src))
    out = cv2.imread(out_path)
    assert out is not None

    # the garment kept its color identity (navy: B > R) at the same spot
    px = out[260, 210]
    assert px[0] > px[2] + 15, f"attached garment dropped/reclored: {px}"
    # the hero kept blue
    px2 = out[150, 210]
    assert px2[0] > px2[2], f"hero recolored: {px2}"
    # wall text is gone and background is warm wood
    assert not editor._verify_text_bands(out).any(), "wall text survived"
    corner = out[4, 4]
    assert corner[2] >= corner[1] >= corner[0], f"background not wood: {corner}"


def test_editor_strips_hand_but_keeps_skin_product(monkeypatch, tmp_path):
    """A genuine hand holding the print is REMOVED from the mask, while a
    skin/wood-COLORED product is kept whole (measured discriminator: a hand
    overlaps the product but covers 2-55% of it; a colored product reads as
    ~95% of its own mask)."""
    import cv2
    import numpy as np
    import editor

    # product-colored blob + separate 'hand' blob overlapping its edge
    mask = np.zeros((300, 300), np.uint8)
    cv2.circle(mask, (150, 150), 80, 255, -1)          # product
    hand = np.zeros((300, 300), np.uint8)
    cv2.ellipse(hand, (210, 190), (38, 60), 30, 0, 360, 255, -1)  # overlaps edge
    cleaned, found = editor._strip_foreign_body(mask, hand)
    assert found, "real hand not detected"
    # hand area no longer in the mask, product body intact
    assert int(cv2.countNonZero(cv2.bitwise_and(cleaned, hand))) == 0
    assert cv2.countNonZero(cleaned) > 0.5 * cv2.countNonZero(mask)

    # a 'human' region that IS the product (wood-colored print case) survives
    whole = np.zeros((300, 300), np.uint8)
    cv2.circle(whole, (150, 150), 80, 255, -1)
    kept, found2 = editor._strip_foreign_body(whole, whole.copy())
    assert not found2 and cv2.countNonZero(kept) == cv2.countNonZero(whole)

    # tiny blob (wood knot, 0.7%) does not trigger stripping (img_5 regression)
    knot = np.zeros((300, 300), np.uint8)
    cv2.circle(knot, (150, 150), 12, 255, -1)
    same, found3 = editor._strip_foreign_body(whole, knot)
    assert not found3 and np.array_equal(same, whole)


def test_editor_does_not_strip_non_skin_false_fires(monkeypatch, tmp_path):
    """THE CADDY REGRESSION: the human-segmentation net also fires on dark
    faucets/monitors (neutral colors, 5.6% skin-like on the real photo) and
    the stripper used to amputate the product mask with it — shipping a
    nearly-empty composite. A flagged region must be SKIN-COLORED to count
    as a hand; a dark faucet blob overlapping the product is kept."""
    import cv2
    import numpy as np
    import editor

    # blue-ish product + dark faucet-like blob overlapping its edge
    mask = np.zeros((300, 300), np.uint8)
    cv2.circle(mask, (150, 150), 80, 255, -1)
    faucet = np.zeros((300, 300), np.uint8)
    cv2.ellipse(faucet, (200, 170), (30, 55), 20, 0, 360, 255, -1)
    img = np.full((300, 300, 3), 200, np.uint8)          # light background
    img[mask > 0] = (150, 120, 40)                       # blue product
    img[faucet > 0] = (35, 35, 38)                       # dark metal
    cleaned, found = editor._strip_foreign_body(mask, faucet, img_bgr=img)
    assert not found, "dark non-skin blob treated as a hand"
    assert np.array_equal(cleaned, mask), "product mask amputated by a faucet"

    # the same geometry but SKIN-colored really is a hand: still stripped
    img2 = img.copy()
    hand = np.zeros((300, 300), np.uint8)
    cv2.ellipse(hand, (200, 170), (30, 55), 20, 0, 360, 255, -1)
    img2[hand > 0] = (110, 160, 200)                     # skin tone (BGR)
    cleaned2, found2 = editor._strip_foreign_body(mask, hand, img_bgr=img2)
    assert found2, "real skin hand not detected"
    assert int(cv2.countNonZero(cv2.bitwise_and(cleaned2, hand))) == 0


def test_editor_does_not_false_refuse_close_ups(monkeypatch, tmp_path):
    """Close-ups with gradient backgrounds and dark hardware (img_1-style):
    the dropped-parts guard must NOT make these unshippable — the edit
    succeeds and the kept product keeps its color."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import cv2
    import numpy as np
    import editor

    yy, xx = np.mgrid[0:480, 0:640].astype(np.float32)
    base = np.empty((480, 640, 3), np.float32)
    base[..., 0] = 150 + 40 * (yy / 480)          # warm gradient wall/floor
    base[..., 1] = 140 + 30 * (yy / 480)
    base[..., 2] = 135 + 25 * (yy / 480)
    img = base.clip(0, 255).astype(np.uint8)
    # dark metal hook structure through the middle (thin + rigid)
    cv2.line(img, (320, 60), (320, 300), (40, 40, 45), 14)
    cv2.ellipse(img, (320, 120), (60, 55), 0, 180, 360, (40, 40, 45), 14)
    cv2.ellipse(img, (320, 330), (90, 70), 0, 0, 180, (35, 35, 40), -1)
    tex = np.random.default_rng(5).normal(0, 6, (480, 640)).astype(np.float32)
    img = np.clip(img.astype(np.float32) + tex[..., None], 0, 255).astype(np.uint8)
    src = tmp_path / "closeup.png"
    cv2.imwrite(str(src), img)

    out_path = editor.edit_image(str(src))         # must NOT raise
    out = cv2.imread(out_path)
    assert out is not None
    px = out[330, 320]                             # product core stays dark
    assert int(px[0]) < 120 and int(px[2]) < 120, f"product recolored: {px}"
    corner = out[4, 4]
    assert corner[2] >= corner[1] >= corner[0], f"background not wood: {corner}"


def test_editor_refuses_vacuous_fragment(monkeypatch, tmp_path):
    """THE img_1 REGRESSION: when one candidate mask keeps only a small
    fragment of the product, every in-mask guard passes it vacuously (nothing
    inside to be wrong) and its sharpness ratio can WIN the best-mask race —
    shipping a near-empty composite. A fragment that keeps < UNION_RECALL_MIN
    of the candidates' union prior must be disqualified, while the complete
    mask still ships."""
    import cv2
    import numpy as np
    import editor

    # busy counter-ish background + a dark rigid product with a thin mesh
    # region, so the fragment candidate is genuinely 'clean' in isolation
    rng = np.random.default_rng(11)
    tex = np.clip(rng.normal(0, 9, (480, 640, 1)), -30, 30)
    img = np.clip(np.full((480, 640, 3), 170, np.float32) + tex, 0, 255).astype(np.uint8)
    full = np.zeros((480, 640), np.uint8)          # the COMPLETE product
    cv2.rectangle(full, (200, 140), (440, 380), 255, -1)
    cv2.line(full, (200, 260), (440, 260), 0, 8)   # split into two blobs by a
    full[240:272, 200:440] = 255                   # band (kept: band is part)
    frag = np.zeros((480, 640), np.uint8)          # a small FRAGMENT of it
    cv2.rectangle(frag, (200, 140), (280, 220), 255, -1)
    for m in (full, frag):                         # product texture: dark noise
        img[m > 0] = (60, 65, 70) + rng.integers(-12, 12, (int((m > 0).sum()), 3))

    cands = [editor.Candidate("fragment", frag), editor.Candidate("full", full)]
    monkeypatch.setattr(editor, "_candidate_masks",
                        lambda img_bgr, debug_dir=None: cands)
    monkeypatch.setattr(editor, "_verify_text_bands",
                        lambda im: np.zeros(im.shape[:2], np.uint8))
    monkeypatch.setattr(editor, "_human_overlap", lambda im: None)

    src = tmp_path / "fragment_trap.png"
    cv2.imwrite(str(src), img)
    out_path = editor.edit_image(str(src))
    mask = cv2.imread(str(src).replace(".png", "_mask.png"), 0)
    assert out_path is not None
    # the SHIPPED opaque mask must be the complete product, never the sliver
    assert (mask > 127).mean() >= 0.10, \
        f"fragment won again: opaque={(mask > 127).mean():.3f}"
    overlap = float(((mask > 127) & (full > 0)).sum()) / float((full > 0).sum())
    assert overlap >= 0.8, f"shipped mask missed {1 - overlap:.0%} of the product"


def test_editor_closeup_small_union_still_ships(monkeypatch, tmp_path):
    """The vacuous-pass floor must not false-refuse genuine close-ups where
    the nets themselves see a small product (small UNION prior, and the
    winner keeps ~all of it)."""
    import cv2
    import numpy as np
    import editor

    rng = np.random.default_rng(7)
    img = np.full((480, 640, 3), 175, np.float32)
    img += np.clip(rng.normal(0, 8, (480, 640, 1)), -25, 25)   # in-focus texture
    img = img.clip(0, 255).astype(np.uint8)
    small = np.zeros((480, 640), np.uint8)         # ~2.5% of frame
    cv2.rectangle(small, (300, 200), (360, 320), 255, -1)
    img[small > 0] = (70, 75, 80) + rng.integers(-12, 12, (int((small > 0).sum()), 3))
    img = img.clip(0, 255).astype(np.uint8)
    monkeypatch.setattr(editor, "_candidate_masks",
                        lambda im, debug_dir=None: [editor.Candidate("s", small)])
    monkeypatch.setattr(editor, "_verify_text_bands",
                        lambda im: np.zeros(im.shape[:2], np.uint8))
    monkeypatch.setattr(editor, "_human_overlap", lambda im: None)

    src = tmp_path / "closeup_small.png"
    cv2.imwrite(str(src), img)
    out_path = editor.edit_image(str(src))         # must NOT raise
    assert out_path is not None


def test_editor_has_no_cloud_sdk_imports():
    """Guard rail: editor.py must never import a paid/cloud SDK."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "editor.py"
    text = src.read_text(encoding="utf-8")
    for banned in ("google", "genai", "openai", "anthropic", "api_key"):
        assert banned not in text.lower(), f"editor.py references {banned!r}"


# ── analytics ─────────────────────────────────────────────────────────────────

def _mk_snaps():
    import time as _t
    import analytics as an
    now = _t.time()
    day = 86400.0
    # model A: 100 dl -> 160 dl over the last week; model B appeared today
    return [
        an.ModelSnapshot("A", "u", 100, 10, ts=now - 10 * day),
        an.ModelSnapshot("A", "u", 130, 12, ts=now - 4 * day),
        an.ModelSnapshot("A", "u", 160, 15, ts=now - 0.2 * day),
        an.ModelSnapshot("B", "v", 5, 2, ts=now - 0.1 * day),
    ]


def test_analytics_store_roundtrip_and_dedupe(tmp_path, monkeypatch):
    import analytics as an
    monkeypatch.setattr(an, "SNAP_FILE", str(tmp_path / "snap.json"))
    snaps = _mk_snaps()
    assert an.append_snapshots(snaps) == 4
    assert an.append_snapshots(snaps) == 0          # deduped
    loaded = an.load_snapshots()
    assert len(loaded) == 4 and loaded[0].name == "A"


def test_analytics_period_stats(monkeypatch, tmp_path):
    import time as _t
    import analytics as an
    monkeypatch.setattr(an, "SNAP_FILE", str(tmp_path / "snap.json"))
    now = 1_700_000_000.0
    day = 86400.0
    day0 = now - (now % day)                      # same boundary the module uses
    snaps = [an.ModelSnapshot("A", "u", 100, 10, ts=day0 - 10 * day),
             an.ModelSnapshot("A", "u", 130, 12, ts=day0 - 3 * day),
             an.ModelSnapshot("A", "u", 160, 15, ts=day0 + 3600)]   # inside today
    s = an.period_stats(snaps, now=now)
    assert s["current"]["downloads"] == 160
    # today: baseline = last snapshot before midnight (130, from 3 days ago)
    assert s["totals"]["today"]["downloads"] == 30
    # 7d: start is ~6.9 days back -> baseline is the 100 snapshot -> delta 60
    assert s["totals"]["7d"]["downloads"] == 60
    assert s["totals"]["7d"]["likes"] == 5
    # ytd and 30d are older than the 10-day history: baseline = first known
    # value (100) so the window still counts all tracked growth -> 60
    assert s["totals"]["ytd"]["downloads"] == 60
    assert s["totals"]["30d"]["downloads"] == 60
    # deltas never negative
    snaps_down = [an.ModelSnapshot("C", "w", 50, 5, ts=now - 2 * day),
                  an.ModelSnapshot("C", "w", 20, 1, ts=now - 0.1 * day)]
    s2 = an.period_stats(snaps_down, now=now)
    assert s2["totals"]["7d"]["downloads"] == 0     # clamped, not -30


def test_analytics_series_is_monotonic(monkeypatch, tmp_path):
    import analytics as an
    monkeypatch.setattr(an, "SNAP_FILE", str(tmp_path / "snap.json"))
    s = an.chart_series(_mk_snaps(), days=30)
    assert len(s["t"]) == len(s["downloads"]) == len(s["likes"])
    assert all(b >= a for a, b in zip(s["downloads"], s["downloads"][1:]))
    assert s["downloads"][-1] == 165 and s["likes"][-1] == 17


def test_points_store_and_stats(monkeypatch, tmp_path):
    """Points balance history: append/dedupe + today/7d/30d/ytd/total math."""
    import analytics as an
    monkeypatch.setattr(an, "POINTS_FILE", str(tmp_path / "points.json"))
    now = 1_700_000_000.0
    day = 86400.0
    day0 = now - (now % day)
    # history: 34,900 (10d ago) -> 35,100 (3d ago) -> 35,346 (today)
    assert an.append_points(day0 - 10 * day, 34900)
    assert an.append_points(day0 - 3 * day, 35100)
    assert an.append_points(day0 + 3600, 35346)
    # same-value dedupe within the hour
    assert not an.append_points(day0 + 3700, 35346)
    st = an.points_stats(now=now)
    assert st["current"] == 35346
    # today: baseline = 35,100 (last reading before midnight)
    assert st["totals"]["today"] == 246
    # 7d / 30d / ytd: baseline 34,900 (or first known) -> all tracked growth
    assert st["totals"]["7d"] == 446
    assert st["totals"]["30d"] == 446
    assert st["totals"]["ytd"] == 446
    # a points counter reset on the site (a drop) keeps the gains on both sides
    # of it: the 246 earned before the reset stays, the drop itself is not negative
    an.append_points(now, 1)
    st2 = an.points_stats(now=now + 60)
    assert st2["totals"]["today"] == 246
    an.append_points(now + 120, 5)
    st3 = an.points_stats(now=now + 180)
    assert st3["totals"]["today"] == 250
    assert all(v >= 0 for v in st3["totals"].values())


def test_chart_series_includes_points(monkeypatch, tmp_path):
    import analytics as an
    monkeypatch.setattr(an, "SNAP_FILE", str(tmp_path / "snap.json"))
    monkeypatch.setattr(an, "POINTS_FILE", str(tmp_path / "points.json"))
    s = an.chart_series(_mk_snaps(), days=30)
    assert len(s["points"]) == len(s["t"])
    assert all(v == 0 for v in s["points"])          # no points history yet


@pytest.fixture(scope="module")
def an_client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


def test_analytics_endpoints_shape(an_client):
    r = an_client.get("/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"current", "per_model", "totals", "last_scrape"}
    for k in ("today", "7d", "30d", "ytd"):
        assert k in body["totals"]
    r2 = an_client.get("/analytics/series?days=7")
    assert r2.status_code == 200
    s = r2.json()
    assert {"t", "downloads", "likes"} <= set(s)


def test_analytics_status_endpoint_composes(monkeypatch, an_client):
    import analytics

    async def fake_ls():
        return {"logged_in": True, "browser_up": True}
    monkeypatch.setattr(analytics, "login_status", fake_ls)
    r = an_client.get("/analytics/status")
    assert r.status_code == 200
    body = r.json()
    assert body["logged_in"] is True
    assert body["auto_interval_s"] > 0
    assert "last_refresh" in body


def test_analytics_signin_endpoint(monkeypatch, an_client):
    import analytics

    async def fake_open():
        return {"ok": True, "url": "https://id.creality.com/"}
    monkeypatch.setattr(analytics, "open_signin", fake_open)
    r = an_client.post("/analytics/signin")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_start_auto_is_idempotent():
    import analytics

    async def run():
        analytics._auto_task = None
        analytics.start_auto()
        t1 = analytics._auto_task
        analytics.start_auto()
        assert analytics._auto_task is t1
        t1.cancel()

    asyncio.run(run())


# ── discover ──────────────────────────────────────────────────────────────────

def test_discover_sites_have_required_keys():
    from discover import SITES
    for name, cfg in SITES.items():
        assert cfg["urls"], name
        assert cfg["link_re"], name
        assert cfg["link_sel"], name


def test_discover_unknown_site_raises():
    from discover import discover
    with pytest.raises(ValueError):
        asyncio.run(discover("myspace", 3))
