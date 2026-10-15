"""_download_images must not drop photos silently: a failed download is retried
once, and every photo that is not kept is reported with its reason (the
auto run shows that reason instead of a bare "no usable cover photo").

No network and no browser: _save is replaced with a local writer."""
import numpy as np
from PIL import Image

import downloader


def _textured_png(path):
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 256, (400, 400, 3), dtype=np.uint8)).save(path, "PNG")


def test_a_download_that_fails_twice_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def boom(url, path):
        calls.append(url)
        raise ConnectionError("reset by peer")

    monkeypatch.setattr(downloader, "_save", boom)
    report = []
    paths = downloader._download_images(["https://cdn.example/a.png?x=1"], "m",
                                        strict=False, report=report)
    assert paths == []
    assert len(calls) == 2                       # one retry
    assert report and report[0]["reason"].startswith("download failed: ConnectionError")


def test_a_download_that_fails_once_then_succeeds_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
    monkeypatch.setattr("time.sleep", lambda s: None)
    state = {"n": 0}

    def flaky(url, path):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("slow CDN")
        _textured_png(path)
        return path

    monkeypatch.setattr(downloader, "_save", flaky)
    report = []
    paths = downloader._download_images(["https://cdn.example/a.png"], "m",
                                        strict=False, report=report)
    assert state["n"] == 2
    assert len(paths) == 1
    assert report == []


def test_a_photo_that_is_too_small_is_reported_with_its_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))

    def tiny(url, path):
        # big enough in bytes to pass the 1 KB check, but only 120 px a side
        Image.fromarray(np.random.default_rng(2).integers(0, 256, (120, 120, 3),
                                                          dtype=np.uint8)).save(path, "PNG")
        return path

    monkeypatch.setattr(downloader, "_save", tiny)
    report = []
    paths = downloader._download_images(["https://cdn.example/t.png"], "m",
                                        strict=False, report=report)
    assert paths == []
    assert report[0]["reason"] == "under 200 px, or not a readable image"
    assert not list(tmp_path.glob("*.png"))      # the dropped file is removed
