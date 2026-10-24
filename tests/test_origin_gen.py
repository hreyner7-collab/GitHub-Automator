"""The originals pipeline is honest geometry, not a reskin.

Browser-free: these build real meshes with manifold booleans and check the
export. No page, account, upload or render is touched.
"""
import ast
import json
import math
import zipfile
from pathlib import Path

import pytest
import trimesh

from origin import build, export, names, printcheck, spec

ROOT = Path(__file__).resolve().parent.parent
FAMILIES = list(spec.FAMILIES)


@pytest.fixture(scope="module")
def made():
    out = {}
    for fam in FAMILIES:
        s = spec.make_spec(fam, 1)
        m = build.build(s)
        out[fam] = (s, m, printcheck.check(m, s))
    return out


def test_every_family_is_one_watertight_solid(made):
    for fam, (s, m, c) in made.items():
        assert m.is_watertight, fam
        assert m.is_winding_consistent, fam
        assert m.volume > 500, fam
        assert len(m.split(only_watertight=False)) == 1, fam
        lo, hi = m.bounds
        dims = hi - lo
        assert 5 <= max(dims) <= 220, (fam, dims)
        assert 4 <= min(dims) <= 220, (fam, dims)


def test_print_checks_pass_and_report_evidence(made):
    for fam, (s, m, c) in made.items():
        assert c.ok, (fam, c.issues)
        assert c.metrics["components"] == 1
        assert c.metrics["bed_contact_mm2"] >= 60
        assert c.metrics["volume_mm3"] > 500
        assert c.metrics["est_mass_pla_g"] > 0


def test_titles_name_the_product_and_carry_no_brand(made):
    for fam, (s, m, c) in made.items():
        t = s.title
        assert 4 <= len(t) <= 60, t
        assert s.label.lower() in t.lower(), (fam, t)
        assert names.slugify(t), t
        for bad in names.BANNED_WORDS:
            assert bad not in t.lower(), (bad, t)


def test_description_is_original_and_points_at_this_stl(made):
    for fam, (s, m, c) in made.items():
        dims = tuple(float(v) for v in m.extents)
        note = names.print_note_for(fam, c.supports_needed)
        text = names.description_for(s, dims, note)
        low = text.lower()
        assert "original" in low
        assert "cc by 4.0" in low
        assert "exact stl" in low
        assert "http" not in low and "www." not in low
        for bad in names.BANNED_WORDS:
            assert bad not in low, (bad, fam)
        assert s.colour_name in text


def test_specs_are_deterministic_and_variants_differ():
    a = spec.make_spec("desk_organiser", 3)
    b = spec.make_spec("desk_organiser", 3)
    c = spec.make_spec("desk_organiser", 4)
    assert a.seed == b.seed and a.params == b.params and a.title == b.title
    assert a.params != c.params
    assert a.key != c.key


def test_catalogue_covers_all_families_and_unique_keys():
    cat = spec.catalogue(per_family=2)
    assert len(cat) == 16
    assert {s.family for s in cat} == set(FAMILIES)
    assert len({s.key for s in cat}) == 16


def test_negative_controls_make_printcheck_fail():
    s = spec.make_spec("parts_tray", 1)
    # print_mesh seats the mesh on z=0, so a floating box cannot exist; use a
    # box balanced on one corner instead: no bed contact, COM over nothing.
    corner = trimesh.creation.box(extents=(40, 30, 10))
    corner.apply_transform(trimesh.transformations.rotation_matrix(
        math.radians(45), [1, 0, 0]))
    corner.apply_transform(trimesh.transformations.rotation_matrix(
        math.radians(45), [0, 1, 0]))
    c = printcheck.check(corner, s)
    assert not c.ok
    assert any("bed contact" in i for i in c.issues)

    two = trimesh.util.concatenate(
        [trimesh.creation.box(extents=(20, 20, 10)),
         trimesh.creation.box(extents=(20, 20, 10)).apply_translation((60, 0, 0))])
    c2 = printcheck.check(two, s)
    assert not c2.ok
    assert any("disconnected shells" in i for i in c2.issues)


def test_export_writes_a_real_stl_and_3mf(tmp_path, made):
    s, m, c = made["cup_holder"]
    files = export.export_model(m, tmp_path, "test-cup", s.title)
    stl = Path(files["stl"])
    assert stl.is_file() and stl.stat().st_size > 84
    assert stl.stat().st_size == 84 + 50 * len(m.faces)
    assert len(files["stl_sha256"]) == 64
    assert files["three_mf_ok"], files["three_mf_detail"]
    with zipfile.ZipFile(files["threemf"]) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    assert s.title in model
    assert "CC BY 4.0" in model
    assert '<metadata name="Designer"' not in model


def test_generator_imports_no_network_or_source_reader():
    for name in ("build.py", "spec.py", "printcheck.py", "names.py"):
        tree = ast.parse((ROOT / "origin" / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("requests", "playwright", "downloader", "creality_api",
                          "creality_rules", "main", "analytics"):
            assert forbidden not in imported, (name, forbidden)


def test_printcheck_has_no_approval_path():
    text = (ROOT / "origin" / "printcheck.py").read_text(encoding="utf-8")
    assert "publish" not in text.lower()
    src = (ROOT / "origin" / "build.py").read_text(encoding="utf-8").lower()
    assert "source_url" not in src
    for word in ("image", "photo", "render", "upload", "publish"):
        assert word not in src, word


def test_banned_titles_are_refused():
    with pytest.raises(ValueError):
        names.clean_title("Creality Phone Stand")
    assert names.clean_title("Cove Cable Clip - 10 mm")
