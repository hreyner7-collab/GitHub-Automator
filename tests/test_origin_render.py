"""The shipped engine turns one real STL into the full photo contract.

Browser-free: Blender is a renderer here, not a page. One family, 400 px,
one sample, so the check is fast and still exercises the exact command the
dashboard uses (engine flag, manifest, crops, contact sheet, record last).
"""
import json
from pathlib import Path

import pytest

from origin import build, printcheck, shots, spec


def _blender_ready() -> bool:
    try:
        shots.find_blender()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _blender_ready(),
                                reason="Blender is not installed")


def test_default_engine_renders_the_whole_contract(tmp_path):
    s = spec.make_spec("cable_clip", 1)
    mesh = build.build(s)
    check = printcheck.check(mesh, s)
    assert check.ok, check.issues
    dims = [round(float(v), 1) for v in (mesh.bounds[1] - mesh.bounds[0])]
    out = tmp_path / s.key

    rec = shots.make_photos(mesh, s, check, dims, out, samples=1, res=400)

    assert rec["engine"] == shots.DEFAULT_ENGINE
    assert len(rec["photos_4x3"]) == 5
    assert len(rec["photos_3x4"]) == 5
    for p in rec["photos_4x3"] + rec["photos_3x4"]:
        assert Path(p).is_file() and Path(p).stat().st_size > 5000, p
    assert Path(rec["contact"]).is_file()
    assert Path(rec["files"]["stl"]).is_file()
    assert len(rec["files"]["stl_sha256"]) == 64

    man = rec["render"]
    assert man["engine"] == shots.DEFAULT_ENGINE
    assert man["samples"] == 1
    assert man["device"] in ("CPU", "GPU")
    assert [x["name"] for x in man["shots"]] == [
        "in_use", "front", "hero_right", "high", "detail"]
    for shot in man["shots"]:
        assert Path(shot["file"]).is_file(), shot

    # Every crop carries a real exposure and real contrast, not a blank frame.
    for name, st in rec["brightness"].items():
        assert st["std"] >= 2.0, (name, st)
        assert 20 < st["mean"] < 250, (name, st)

    record = out / ("%s_photos.json" % s.key)
    assert record.is_file()
    written = json.loads(record.read_text(encoding="utf-8"))
    assert written["engine"] == shots.DEFAULT_ENGINE
    assert written["render"]["engine"] == shots.DEFAULT_ENGINE
