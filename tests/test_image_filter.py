"""The listing-photo filters in downloader.py (CHANGED 2026-10-14, user): an
image that carries an AI-generation label, or that is a flat CAD/slicer-style
render, is never a listing photo. A real photo of the printed object is kept."""
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import downloader


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
