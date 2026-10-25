"""Browser-free tests for the MakerWorld side: the plan the pipeline publishes
from, its separate dedup memory, and the refusals that must happen before any
tab opens. Nothing here connects to Chrome or reads the account (AGENTS.md)."""
from __future__ import annotations

import ast
import asyncio
import json
import pathlib

import pytest

import makerworld_flow as mw
import makerworld_ledger as mwl


# ── measured URL shapes ─────────────────────────────────────────────────────
def test_remix_entry_is_the_measured_wizard_url():
    assert mw.entry_url("remix") == "https://makerworld.com/en/my/models/publish?type=remix"
    assert mw.entry_url("original").endswith("/my/models/publish?type=original")
    assert mw.entry_url("nonsense") == mw.entry_url("remix")


def test_draft_id_and_design_id_come_from_measured_url_shapes():
    assert mw.draft_id_from_url(
        "https://makerworld.com/en/my/models/drafts/10186871/edit") == "10186871"
    assert mw.draft_id_from_url("https://makerworld.com/en/my/models/publish?type=remix") == ""
    assert mw.design_id_from_url("https://makerworld.com/en/models/2964241-gt-r-r34") == "2964241"
    assert mw.design_url("2964241", "Gt R R34 Mini Kit") == \
        "https://makerworld.com/en/models/2964241-gt-r-r34-mini-kit"
    assert mw.draft_edit_url("10186871").endswith("/drafts/10186871/edit")


def test_step1_accepts_the_formats_the_page_lists():
    assert ".stl" in mw.MODEL_ACCEPT_EXTS and ".3mf" in mw.MODEL_ACCEPT_EXTS
    assert mw.is_model_file("part.STL") and not mw.is_model_file("cover.png")


# ── licence ─────────────────────────────────────────────────────────────────
def test_share_alike_source_gets_the_share_alike_answer():
    p = mw.license_plan("https://creativecommons.org/licenses/by-sa/4.0/")
    assert p["code"] == "by-sa" and not p["refused"]
    assert p["answers"][mw.LICENSE_Q_ADAPT] == mw.ADAPT_OPTIONS[1]
    assert p["answers"][mw.LICENSE_Q_COMMERCIAL] == "Yes"


def test_no_derivatives_source_is_refused_with_no_answers():
    for raw in ("CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nc-nd/4.0/"):
        p = mw.license_plan(raw)
        assert p["refused"] and p["answers"] == {} and p["why"]


def test_noncommercial_source_is_refused_for_this_account():
    for raw in ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/"):
        p = mw.license_plan(raw)
        assert p["refused"] and p["answers"] == {} and "noncommercial" in p["why"]


def test_unknown_licence_falls_back_to_the_strictest_safe_plan():
    p = mw.license_plan("")
    assert p["code"] == "" and not p["refused"]
    assert p["answers"] == mw.LICENSE_PLAN["by"]


def test_licence_read_back_only_accepts_the_source_name():
    assert mw.license_name_matches("CC BY-NC 4.0", "by-nc")
    assert mw.license_name_matches("CC BY-SA", "by-sa")
    assert not mw.license_name_matches("CC BY", "by-sa")
    assert not mw.license_name_matches("Standard Digital File License", "by")
    # no code at all is not a licence we can prove the form matched
    assert not mw.license_name_matches("CC0", "")


# ── text planning ───────────────────────────────────────────────────────────
def test_name_is_clamped_to_the_measured_50_characters():
    assert len(mw.clamp_name("x" * 90)) == 50
    assert mw.clamp_name("  squishy   dumpling  ") == "squishy dumpling"


def test_tags_follow_the_measured_limits_and_dedupe():
    tags = mw.plan_tags(["cat", "Cat ", "", "dog"] + [f"t{i}" for i in range(60)],
                        source_name="Squishy Dumpling")
    assert tags[0] == "Squishy Dumpling"
    assert len(tags) <= mw.MAX_TAGS
    assert [t for t in tags if t.lower() == "cat"] == ["cat"]
    assert all(len(t) <= mw.MAX_TAG_INPUT for t in tags)


def test_description_always_carries_the_source_and_a_remix_statement():
    d = mw.plan_description("Sanded and re-scaled.", "https://makerworld.com/en/models/1-x",
                            "X")
    assert "https://makerworld.com/en/models/1-x" in d
    assert "remix" in d.lower()
    d2 = mw.plan_description("", "", "")
    assert d2 and "source model above" in d2.lower()


def test_the_source_row_is_picked_by_name_and_never_by_position():
    rows = ["Dumpling Clicker", "dumpling squishy clicker v2", "Another model 10438871"]
    # the exact row wins even though a prefix look-alike is listed first
    assert mw.pick_source_row(rows, "dumpling squishy clicker v2") == 1
    # a row that only carries other words is not the source
    assert mw.pick_source_row(rows, "squishy clicker") == -1
    assert mw.pick_source_row(rows, "not offered at all") == -1
    # the numeric design id is the last acceptable proof, and only whole
    assert mw.pick_source_row(rows, "", "10438871") == 2
    assert mw.pick_source_row(rows, "", "1043887") == -1
    assert mw.pick_source_row([], "dumpling squishy clicker v2") == -1


# ── the pre-publish plan ────────────────────────────────────────────────────
@pytest.fixture
def ready_files(tmp_path):
    stl = tmp_path / "part.stl"
    stl.write_text("solid x\nendsolid x\n", encoding="ascii")
    covers = [tmp_path / f"cover{i}.png" for i in range(4)]
    photos = [tmp_path / f"photo{i}.jpg" for i in range(3)]
    for p in covers + photos:
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(stl), [str(c) for c in covers], [str(p) for p in photos]


def _valid_kwargs(ready_files):
    stl, covers, photos = ready_files
    return dict(name="Squishy Dumpling Clicker", description="Remixed: resized and sanded.",
                source_url="https://makerworld.com/en/models/2964241-gt-r-r34",
                model_file=stl, cover_images=covers, print_photos=photos,
                remix_source_url="https://makerworld.com/en/models/2964241-gt-r-r34",
                license_raw="CC BY 4.0")


def test_a_complete_plan_passes(ready_files):
    v = mw.validate_plan(**_valid_kwargs(ready_files))
    assert v["ok"], v["blockers"]
    assert v["plan"]["name"] == "Squishy Dumpling Clicker"


def test_every_measured_requirement_is_blocked_before_the_browser_opens(ready_files):
    stl, covers, photos = ready_files
    kw = _valid_kwargs(ready_files)
    cases = {
        "no model file": {**kw, "model_file": ""},
        "not a format the form takes": {**kw, "model_file": photos[0]},
        "no cover": {**kw, "cover_images": []},
        "fewer than 3 print photos": {**kw, "print_photos": photos[:2]},
        "remix with no source": {**kw, "remix_source_url": ""},
        "source that is not on MakerWorld": {**kw, "remix_source_url":
                                            "https://www.thingiverse.com/thing:1"},
        "no name": {**kw, "name": "   "},
        "NoDerivatives source": {**kw, "license_raw": "CC BY-ND 4.0"},
    }
    for why, args in cases.items():
        v = mw.validate_plan(**args)
        assert not v["ok"], f"{why} should have blocked the publish"


def test_dry_run_walks_the_wizard_without_photos_but_still_needs_a_source(ready_files):
    kw = _valid_kwargs(ready_files)
    v = mw.validate_plan(dry_run=True, **{**kw, "print_photos": []})
    assert v["ok"], v["blockers"]
    assert any("print photo" in w.lower() for w in v["warnings"])
    v2 = mw.validate_plan(dry_run=True, **{**kw, "remix_source_url": ""})
    assert not v2["ok"]


def test_original_path_does_not_require_a_makerworld_source(ready_files):
    kw = _valid_kwargs(ready_files)
    v = mw.validate_plan(kind="original", **{**kw, "remix_source_url": ""})
    assert v["ok"], v["blockers"]


# ── the separate ledger ─────────────────────────────────────────────────────
@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    path = tmp_path / "makerworld_published.json"
    monkeypatch.setattr(mwl, "LEDGER_PATH", str(path))
    return path


def test_makerworld_memory_is_not_the_creality_one(isolated_ledger):
    import ledger
    mwl.record(name="A", source_url="https://makerworld.com/en/models/1-a")
    assert isolated_ledger.exists()
    assert str(ledger.ledger_path()) != str(isolated_ledger)
    # the Creality ledger never hears about a MakerWorld listing
    assert "1-a" not in json.dumps(ledger.entries(), ensure_ascii=False)


def test_a_published_model_is_found_by_any_of_its_identities(isolated_ledger, ready_files):
    stl, covers, photos = ready_files
    mwl.record(name="Squishy Clicker", author="ana", source_url="https://www.thingiverse.com/thing:9",
               published_url="https://makerworld.com/en/models/777-squishy-clicker",
               file_path=stl, outcome="published")
    for hit in (
        mwl.find(source_url="https://www.thingiverse.com/thing:9"),
        mwl.find(published_url="makerworld.com/en/models/777-squishy-clicker"),
        mwl.find(design_id="777"),
        mwl.find(name="Squishy Clicker", author="Ana"),
        mwl.find(file_path=stl),
    ):
        assert hit["found"], hit
    assert not mwl.find(source_url="https://www.thingiverse.com/thing:1234")["found"]


def test_a_failed_publish_stays_retryable(isolated_ledger):
    mwl.record(name="Try", source_url="https://makerworld.com/en/models/5-try",
               outcome="failed")
    assert not mwl.find(source_url="https://makerworld.com/en/models/5-try")["found"]
    mwl.record(name="Try", source_url="https://makerworld.com/en/models/5-try",
               outcome="published")
    assert mwl.find(source_url="https://makerworld.com/en/models/5-try")["found"]


def test_forget_is_the_only_way_to_unlock_one(isolated_ledger):
    mwl.record(name="Done", published_url="https://makerworld.com/en/models/9-done")
    assert mwl.find(design_id="9")["found"]
    assert mwl.forget(match="https://makerworld.com/en/models/9-done")["removed"] == 1
    assert not mwl.find(design_id="9")["found"]


# ── the driver, offline ─────────────────────────────────────────────────────
def test_the_makerworld_driver_never_imports_the_creality_one():
    src = pathlib.Path(mw.__file__).with_name("makerworld_uploader.py").read_text(
        encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "uploader" not in imported and "ledger" not in imported and "creality_api" not in imported
    assert "crealitycloud" not in src.lower()


def test_publish_without_print_photos_is_refused_before_any_browser(monkeypatch):
    import makerworld_uploader as mwu
    calls = []
    monkeypatch.setattr(mwu, "_debug_port_up", lambda: calls.append(1) or True)
    res = asyncio.run(mwu.upload(name="X", description="d", stl_path="x.stl",
                                 image_paths=[], print_photos=[], source_url="",
                                 remix_source_url="https://makerworld.com/en/models/1-x"))
    assert res["success"] is False
    assert "photographs of the PRINTED model" in res["error"]
    assert calls == [], "the refusal must not touch Chrome"


def test_nd_source_is_refused_before_any_browser(monkeypatch, ready_files):
    import makerworld_uploader as mwu
    stl, covers, photos = ready_files
    calls = []
    monkeypatch.setattr(mwu, "_debug_port_up", lambda: calls.append(1) or True)
    res = asyncio.run(mwu.upload(name="X", description="remixed a thing", stl_path=stl,
                                 image_paths=covers, print_photos=photos,
                                 remix_source_url="https://makerworld.com/en/models/1-x",
                                 license_raw="CC BY-ND 4.0"))
    assert res["success"] is False
    assert "NoDerivatives" in res["error"]
    assert calls == []


def _defined_upper_names(src: str) -> set[str]:
    defined: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    defined.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                defined.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if (a.asname or a.name).isupper():
                    defined.add(a.asname or a.name)
    return defined


def _undefined_upper_names(src: str) -> set[str]:
    tree = ast.parse(src)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load) and n.id.isupper()}
    return used - _defined_upper_names(src)


def test_every_control_the_driver_reaches_for_is_defined_in_it():
    """The Model Origin rewrite referenced four JS constants it never defined —
    a NameError that would only fire inside the live wizard, after the draft was
    already created. This catches that class of bug offline."""
    src = pathlib.Path(mw.__file__).with_name("makerworld_uploader.py").read_text(
        encoding="utf-8")
    assert _undefined_upper_names(src) == set()
    assert {"SOURCE_ROW_SELECTOR", "SOURCE_ROWS_JS", "SOURCE_ROW_CLICK_JS",
            "ORIGIN_STATE_JS"} <= _defined_upper_names(src)
    # negative control: the same check must catch a driver that reads a constant
    # nobody defined.
    assert _undefined_upper_names("A = 1\nprint(A, B)\n") == {"B"}
