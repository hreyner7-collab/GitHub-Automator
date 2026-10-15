"""The listing-photo filters in downloader.py (CHANGED 2026-10-14, user): an
image that carries an AI-generation label, or that is a flat CAD/slicer-style
render, is never a listing photo. A real photo of the printed object (on a desk,
table, floor or black backdrop) is kept; a slicer/CAD screenshot on a neutral
grey or white bed is dropped."""
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import downloader

FIXTURES = Path(__file__).parent / "fixtures"
NEUTRAL_BED = "neutral grey or white render bed"


def _save(path, arr, pnginfo=None):
    Image.fromarray(arr).save(path, "PNG", pnginfo=pnginfo)
    return str(path)


def test_a_flat_cad_style_render_is_rejected(tmp_path):
    # a model drawn on a plain grey background: two colours, background > 40%
    arr = np.full((400, 400, 3), 240, np.uint8)
    arr[100:300, 100:300] = (60, 120, 200)
    p = _save(tmp_path / "render.png", arr)
    assert "flat CAD/slicer render" in downloader._flat_render_reason(p)


def test_a_textured_photo_is_kept(tmp_path):
    # a real photo has sensor noise and texture: thousands of distinct colours
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, (400, 400, 3), dtype=np.uint8)
    p = _save(tmp_path / "photo.png", arr)
    assert downloader._flat_render_reason(p) == ""


def _object_on(bg, rng_seed=None, noise=0.0):
    """A frame: the given background array with a solid object in the middle."""
    arr = bg.astype(np.float32)
    if noise:
        arr += np.random.default_rng(rng_seed).normal(0, noise, arr.shape)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    arr[140:260, 140:260] = (200, 80, 40)
    return arr


def _grey_gradient():
    bg = np.repeat(np.linspace(215, 150, 400)[:, None], 400, axis=1)
    return np.stack([bg, bg, bg + 10], axis=2)


def test_a_slicer_render_on_a_smooth_gradient_is_rejected(tmp_path):
    # Slicer/CAD look: a smooth lit grey gradient behind the model, no noise.
    p = _save(tmp_path / "slicer.png", _object_on(_grey_gradient()))
    assert NEUTRAL_BED in downloader._background_reason(p)


def test_a_slicer_render_saved_as_jpeg_is_still_rejected(tmp_path):
    p = tmp_path / "slicer.jpg"
    Image.fromarray(_object_on(_grey_gradient())).save(p, "JPEG", quality=90)
    assert NEUTRAL_BED in downloader._background_reason(str(p))


def test_a_slicer_grid_bed_is_rejected_even_though_its_grid_lines_add_texture(tmp_path):
    # Slicer screenshot: a light grey bed with dark grid lines. The lines add
    # edges, so texture alone would pass it; the lack of colour must reject it.
    bg = np.full((400, 400, 3), 205, np.float32)
    bg[::40, :] = 150
    bg[:, ::40] = 150
    p = _save(tmp_path / "grid.png", _object_on(bg))
    assert NEUTRAL_BED in downloader._background_reason(p)


def test_a_photo_on_a_desk_with_grain_is_kept(tmp_path):
    # a desk/table: a warm, coloured surface with grain and sensor noise
    grain = np.sin(np.arange(400) * 0.9)[None, :] * 12
    bg = np.full((400, 400, 3), (120, 95, 70), np.float32) + grain[..., None]
    p = _save(tmp_path / "desk.png", _object_on(bg, rng_seed=3, noise=8))
    assert downloader._background_reason(p) == ""


def test_a_photo_on_a_black_backdrop_is_kept(tmp_path):
    bg = np.full((400, 400, 3), 12, np.float32)
    p = _save(tmp_path / "black.png", _object_on(bg, rng_seed=4, noise=2))
    assert downloader._background_reason(p) == ""


def test_the_reference_desk_photo_is_kept():
    # the user's reference: a black stand on a wooden desk
    assert downloader._background_reason(str(FIXTURES / "desk_stand.jpg")) == ""


def test_the_reference_slicer_screenshot_is_rejected():
    # the user's "never" example: a slicer screenshot on a grey grid
    reason = downloader._background_reason(str(FIXTURES / "slicer_render.jpg"))
    assert NEUTRAL_BED in reason


def test_download_keeps_the_desk_photo_and_drops_the_slicer_screenshot(tmp_path, monkeypatch):
    # end to end through _download_images, with the network replaced by copies
    # of the reference files
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))

    def fake_save(url, dest):
        shutil.copyfile(url, dest)

    monkeypatch.setattr(downloader, "_save", fake_save)
    urls = [str(FIXTURES / "slicer_render.jpg"), str(FIXTURES / "desk_stand.jpg")]
    kept = downloader._download_images(urls, "e2e-check", strict=True)
    assert len(kept) == 1
    assert kept[0].endswith("e2e-check_img_1.jpg")


def test_an_image_labelled_as_ai_generated_is_rejected(tmp_path):
    info = PngInfo()
    info.add_text("parameters", "a printed cable clip, Steps: 20, Sampler: Euler")
    arr = np.random.default_rng(1).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    p = _save(tmp_path / "generated.png", arr, pnginfo=info)
    assert "AI-generation label" in downloader._ai_label_reason(p)


def test_a_plain_image_has_no_ai_label(tmp_path):
    arr = np.random.default_rng(2).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    p = _save(tmp_path / "plain.png", arr)
    assert downloader._ai_label_reason(p) == ""
