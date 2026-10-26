"""Browser-free tests for the MakerWorld side: the plan the pipeline publishes
from, its separate dedup memory, and the refusals that must happen before any
tab opens. Nothing here connects to Chrome or reads the account (AGENTS.md)."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import re

import pytest

import makerworld_flow as mw
import makerworld_ledger as mwl
from discover import MAKERWORLD_LICENSES


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
    assert mw.license_name_matches("CC BY-SA 4.0", "by-sa")
    assert not mw.license_name_matches("CC BY", "by-sa")
    assert not mw.license_name_matches("CC BY-SA-ND", "by-sa")
    assert not mw.license_name_matches("Standard Digital File License", "by")
    # no code at all is not a licence we can prove the form matched
    assert not mw.license_name_matches("CC0", "")


def test_the_live_form_renders_the_long_cc_name_and_the_api_sends_a_bare_code():
    """Measured 2026-10-25: the draft renders "Creative Commons
    Attribution-Share Alike", while the design API reports the source as
    "BY-SA". Both must be recognised for the same source licence."""
    assert mw.cc_code("BY-SA") == "by-sa"
    assert mw.cc_code("BY-NC-ND") == "by-nc-nd"
    assert mw.cc_code("CC0") == "cc0"
    assert mw.license_name_matches("Creative Commons Attribution-Share Alike", "by-sa")
    assert mw.license_name_matches("Creative Commons Attribution", "by")
    assert not mw.license_name_matches("Creative Commons Attribution", "by-sa")


def test_makerworlds_own_standard_licence_is_refused():
    p = mw.license_plan("Standard Digital File License")
    assert p["refused"] and "Standard Digital File" in p["why"]


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


def test_description_is_the_source_text_unchanged():
    """The user's requirement: the uploaded listing carries the original
    model's own description, not a generated remix preamble or credit line."""
    d = mw.plan_description("Sanded and re-scaled.", "https://makerworld.com/en/models/1-x",
                            "X")
    assert d == "Sanded and re-scaled."
    assert "Source model" not in d and "remix" not in d.lower()
    assert mw.plan_description("", "", "") == ""


def test_the_sources_authored_description_is_taken_from_the_api_as_text():
    """The API stores the description as HTML (usually a <boostme> block).
    The block's title is site chrome; the <boostcontent> is the designer's own
    text — the live source listing shows exactly that sentence."""
    import downloader
    src = ("<boostme><boosttitle>Boost Me</boosttitle>"
           "<boostcontent>As i've made this model available for commercial use, "
           "please leave a 5 star review.</boostcontent></boostme>")
    assert downloader._mw_html_to_text(src) == (
        "As i've made this model available for commercial use, "
        "please leave a 5 star review.")
    assert downloader._mw_html_to_text("<p>A &amp; B</p><p>Second</p>") == "A & B\nSecond"


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
    assert {"ORIGIN_CARD_JS", "ORIGIN_STATE_JS"} <= _defined_upper_names(src)
    # negative control: the same check must catch a driver that reads a constant
    # nobody defined.
    assert _undefined_upper_names("A = 1\nprint(A, B)\n") == {"B"}


# ── its own dashboard, at its own address ───────────────────────────────────
ROOT = pathlib.Path(mw.__file__).resolve().parent
PAGE = ROOT / "makerworld" / "index.html"
CXY_PAGE = ROOT / "creality" / "index.html"


def _page() -> str:
    assert PAGE.is_file(), "the MakerWorld dashboard is its own file, not a card in Creality's"
    return PAGE.read_text(encoding="utf-8")


def test_the_makerworld_page_is_served_at_its_own_address():
    import main
    src = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    assert '@app.get("/makerworld", response_class=HTMLResponse)' in src
    assert '"makerworld" / "index.html"' in src
    html = _page()
    m = re.search(r"<title>MakerWorld Pipeline (V\d+)</title>", html)
    assert m, "the page carries MakerWorld's own branding, not the Creality title"
    assert 'id="mw-version"' in html and ">%s</span>" % m.group(1) in html


def test_the_makerworld_page_wires_only_makerworld_endpoints():
    html = _page()
    for ep in ("/upload/makerworld/plan", "/upload/makerworld", "/makerworld/files",
               "/makerworld/published", "/makerworld/forget", "/discover",
               "/makerworld/auto/start", "/makerworld/auto/stop", "/makerworld/auto/status"):
        assert ep in html, ep
    # Not the Creality path wearing new labels: no Creality publish, no Creality
    # image re-edit, no Creality auto run, no Creality memory. Anchored on the
    # call site, because "/auto/start" is also the tail of this page's own
    # "/makerworld/auto/start".
    for banned in ("fetch('/upload'", '"/upload"', "/edit-images", "('/auto/",
                   "/published/sync", "crealitycloud"):
        assert banned not in html, banned


def test_the_auto_run_is_the_makerworld_shape_not_the_creality_one():
    """Find-and-publish on this page goes through the MakerWorld driver and the
    MakerWorld ledger, and it carries the licence filter with it: the queue can
    only ever be built from the listings MakerWorld itself restricts to
    Public Domain / BY / BY-SA."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    body = re.search(r"async def _makerworld_auto_worker.*?\n\n\n@app", src, re.S).group(0)
    assert 'discover_models("makerworld"' in body
    assert "makerworld_upload(" in body
    assert "makerworld_ledger.find(" in body and "makerworld_ledger.record(" in body
    assert "dry_run=False" in body
    # the pictures are drawn for this listing; the source's own gallery never
    # reaches the form at all
    assert "_makerworld_generated_photos" in body and "print_photos=photos" in body
    assert 'usable_images(res.get("image_paths")' not in body, \
        "the source listing's photographs must not be attached to a remix"
    for creality_only in ("uploader_upload", "edit_all", "creality_rules"):
        assert creality_only not in body, creality_only
    # the Creality ledger, not makerworld_ledger (hence the lookbehind)
    assert not re.search(r"(?<!\w)ledger\.(record|find|count)\(", body)
    # and the run is fenced off from the Creality one — one browser, one form
    assert 'AUTO.get("state") in ("discovering", "running")' in \
        re.search(r"@app.post\(\"/makerworld/auto/start\"\).*?MW_AUTO_TASK = asyncio",
                  src, re.S).group(0)


def test_the_run_checks_the_source_page_not_only_the_listing():
    """The licence chips on the listing are MakerWorld's summary of the design;
    the source page is the authority. An unattended run reads the page and stops
    on it — and refuses to guess when the page names no licence at all, which is
    the case the manual path can afford to fall back on but a batch cannot."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    body = re.search(r"async def _makerworld_auto_worker.*?\n\n\n@app", src, re.S).group(0)
    assert 'makerworld_flow.license_plan(res.get("license_raw"' in body
    assert 'row["state"] = "rejected"' in body
    assert 'if not lic["code"]' in body
    assert 'license_raw=res.get("license_raw", "")' in body
    # and the fetch is what reads it, off the page it already opened
    dl = (ROOT / "downloader.py").read_text(encoding="utf-8")
    scrape = re.search(r"def _fetch_makerworld.*", dl, re.S).group(0)[:9000]
    assert "creativecommons" in scrape
    assert '"license_raw": license_raw' in scrape
    # the API's own licence is tried first, the page's CC link is the fallback
    assert 'meta.get("license_raw") or data.get("license_raw"' in dl
    assert "/api/v1/design-service/design/" in dl


def test_the_run_loop_publishes_each_find_and_stops_at_the_facts(monkeypatch, tmp_path):
    """The whole point of the Auto card: type a number, it finds that many and
    publishes them. Driven here with fakes — no Chrome, no network, no account —
    and it must publish the eligible ones, refuse the ND source, skip the one
    whose page names no licence, skip the one with no file, and stop at N."""
    import main as m

    (tmp_path / "artifacts").mkdir()
    monkeypatch.setattr(m, "BASE_DIR", tmp_path)
    monkeypatch.setattr(m.makerworld_ledger, "find",
                        lambda **k: {"found": False, "entry": None, "how": ""})
    recorded = []
    monkeypatch.setattr(m.makerworld_ledger, "record",
                        lambda **k: recorded.append(k) or {})

    finds = [("Good One", "by"), ("No Licence", ""), ("Derivatives Forbidden", "by-nd"),
             ("No File", "by"), ("Second Good", "by-sa")]
    async def fake_discover(site, count):
        assert site == "makerworld", "the queue comes from MakerWorld's own listing"
        return [{"name": n, "url": "https://makerworld.com/en/models/%d-%s"
                 % (1000 + i, n.lower().replace(" ", "-"))}
                for i, (n, _code) in enumerate(finds)]
    monkeypatch.setattr(m, "discover_models", fake_discover)

    stl = tmp_path / "m.3mf"
    stl.write_bytes(b"PK\x03\x04fake")
    photos = []
    for i in range(3):
        p = tmp_path / f"photo{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 400)
        photos.append(str(p))

    def fake_fetch(url):
        slug = url.rsplit("/", 1)[-1]
        code = dict((n.lower().replace(" ", "-"), c) for n, c in finds)[slug.split("-", 1)[1]]
        return {"name": slug, "description": "d", "image_paths": photos,
                "stl_path": None if slug.startswith("1003") else str(stl),
                "file_error": ("the logged-in Chrome could not save it "
                               "(Error: connect ECONNREFUSED 127.0.0.1:9222)")
                if slug.startswith("1003") else "",
                "categories": ["Miniatures"], "tags": ["mini", "fairy"],
                "license_raw": ("https://creativecommons.org/licenses/%s/4.0/" % code) if code else ""}
    monkeypatch.setattr(m, "dl_fetch", fake_fetch)

    calls = []
    async def fake_upload(**kw):
        calls.append(kw)
        return {"success": True, "url": "https://makerworld.com/en/models/9999-published",
                "design_id": "9999"}
    monkeypatch.setattr(m, "makerworld_upload", fake_upload)

    # The listing's pictures are drawn, not scraped — so the drawer is faked
    # here too, and what it hands back is split the way the form needs it:
    # separate files for the cover slots and for the print-photo slot.
    drawn_sets = []
    def fake_draw(name, description="", count=0):
        n = len(drawn_sets)
        covers = [str(tmp_path / f"cover{n}a.jpg"), str(tmp_path / f"cover{n}b.jpg")]
        shots = [str(tmp_path / f"shot{n}{i}.jpg") for i in range(3)]
        d = {"covers": covers, "print_photos": shots, "photos": covers + shots,
             "ok": True, "got": 5, "asked": 3, "seconds": 12.3, "notes": []}
        drawn_sets.append(d)
        return d
    monkeypatch.setattr(m, "_makerworld_generated_photos", fake_draw)

    m.MW_AUTO.update({"state": "running", "items": [], "published": 0, "stop": False,
                      "requested": 2, "error": ""})
    asyncio.run(m._makerworld_auto_worker(2, "testjob"))

    states = [i["state"] for i in m.MW_AUTO["items"]]
    assert states == ["published", "skipped", "rejected", "skipped", "published"], states
    assert m.MW_AUTO["published"] == 2
    assert m.MW_AUTO["state"] == "done"
    assert len(calls) == 2, "it stops at the number typed, not at the number found"
    assert all(c["dry_run"] is False for c in calls)
    assert all(len(c["print_photos"]) >= 3 for c in calls)
    # the pictures are the drawn ones, the two slots get different files, and
    # nothing from the source listing's gallery is attached anywhere
    assert calls[0]["print_photos"] == drawn_sets[0]["print_photos"]
    assert calls[0]["image_paths"] == drawn_sets[0]["covers"]
    assert not set(calls[0]["print_photos"]) & set(calls[0]["image_paths"]), \
        "the same file in the cover slot and the photo slot is what fails the form"
    sent = {p for c in calls for p in list(c["print_photos"]) + list(c["image_paths"])}
    assert not sent & set(photos), "a source listing's own photograph reached the form"
    assert len(drawn_sets) == 2, "each published listing draws its own set"
    # the source is still declared, whatever the pictures are
    assert calls[0]["remix_source_url"] == m.MW_AUTO["items"][0]["url"]
    # the description is the source's own; the category and tags come from
    # the source's public design metadata, not from a guess
    assert calls[0]["description"] == "d"
    assert calls[0]["category"] == "Miniatures"
    assert calls[0]["tags"] == ["mini", "fairy"]
    # a skipped download reports the browser's own error, not a guess about logins
    no_file = m.MW_AUTO["items"][3]["why"]
    assert "connect ECONNREFUSED" in no_file and "login" not in no_file.lower()
    # only real publish attempts reach the ledger; a licence stop is run history,
    # not a model this account has a claim on
    assert [r["outcome"] for r in recorded] == ["published", "published"]
    assert (tmp_path / "artifacts" / "makerworld_auto_testjob.json").exists()


def test_find_and_publish_opens_the_debug_browser_before_the_run_starts():
    """The 2026-10-25 run skipped two designs because port 9222 was closed: the
    capture stage needed the logged-in Chrome but nothing had started it, and the
    dashboard reported it as MakerWorld refusing the file. Starting the run now
    opens that browser first, refuses to run when it cannot, and the download
    reports the real reason."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    body = re.search(r'@app\.post\("/makerworld/auto/start"\).*?MW_AUTO_TASK = asyncio',
                     src, re.S).group(0)
    assert "makerworld_ensure_browser" in body and "status_code=503" in body
    assert body.index("makerworld_ensure_browser") < body.index("MW_AUTO.update("), \
        "the browser must be up before the run is announced"
    assert 'res.get("file_error")' in src
    assert "would not hand out the model file" not in src
    dl = (ROOT / "downloader.py").read_text(encoding="utf-8")
    assert "def _mw_debug_browser" in dl
    assert "ensure_browser" in dl and "port 9222" in dl
    # the downloader names the failure it saw instead of returning a bare ''
    assert '"file_error": file_error' in dl


def test_an_unreadable_licence_is_not_a_refusal_but_a_batch_must_not_guess():
    assert mw.license_plan("")["refused"] is False      # manual path: falls back to CC BY
    assert mw.license_plan("https://creativecommons.org/licenses/by/4.0/")["refused"] is False
    for bad in ("https://creativecommons.org/licenses/by-nd/4.0/",
                "https://creativecommons.org/licenses/by-nc/4.0/",
                "https://creativecommons.org/licenses/by-nc-nd/4.0/"):
        assert mw.license_plan(bad)["refused"] is True, bad


def test_discovery_refuses_to_list_models_it_may_not_publish():
    """The MakerWorld listing URLs carry the licence filter, and the filter is
    read back off the rendered page: an unknown licences= value is ignored
    silently by MakerWorld, which would hand back NoDerivatives sources."""
    src = (ROOT / "discover.py").read_text(encoding="utf-8")
    assert MAKERWORLD_LICENSES in src
    for url in re.findall(r"https://makerworld\.com/en/3d-models[^\"]*", src):
        assert "licenses=" in url, url
    assert "if not lic.get(\"panel\")" in src and "if not lic.get(\"exact\")" in src
    # ND and NC are the chips that must never be green
    for chip in ("BY-ND", "BY-NC", "BY-NC-ND", "Standard Digital File License"):
        assert chip in src, chip


def test_finding_models_stays_research_on_this_page_too():
    """The 'find models' card is a browser: it lists and hands the chosen url to
    step 1. The batch that actually publishes is a different, explicit control
    (the Auto card), so reading a listing can never turn into a publish by
    accident."""
    html = _page()
    assert "post('/discover'" in html and "site: 'makerworld'" in html
    body = re.search(r"async function discoverModels\(\).*?\n\s*function ", html, re.S).group(0)
    for banned in ("/upload", "/fetch", "/edit-images"):
        assert banned not in body, banned
    # the chosen card goes to the remix source box, not to a publisher
    assert "document.getElementById('src-url').value = m.url" in html
    assert "loadSource()" in html


def test_the_print_photo_assertion_is_what_unlocks_publish():
    html = _page()
    assert 'id="assert-prints"' in html
    # 3 for the photo slot, plus one more when no cover was ticked and one of
    # these has to become it.
    assert "const need = 3 + (p.image_paths.length ? 0 : 1)" in html
    assert "p.print_photos.length >= need" in html
    assert '<button class="go" id="pub-btn" onclick="publish()" disabled>' in html
    # the scraped gallery is offered as covers only — never as print photos
    assert "_raw_orig_images" in html and "print_photos: []" not in html


def test_the_description_field_posts_the_source_text_unchanged():
    """The measured choice: the remix listing carries the source listing's own
    description, not a generated blurb. The field is prefilled with it in full
    and sent as typed; no credit line, no remix note, no truncation."""
    html = _page()
    assert "(_src.description || '').slice(0, 2000)" not in html
    assert "document.getElementById('desc').value = _src.description || ''" in html
    assert "description: document.getElementById('desc').value.trim()" in html
    # the page says what it does, and no longer promises added text
    assert "copied from the source listing" in html
    assert "added automatically" not in html


# ── restyling the maker's own photographs ─────────────────────────────────────

def _bench_photo(tmp_path, name):
    """A photograph-like stand-in: a textured product on a light bench."""
    import cv2
    import numpy as np
    img = np.full((200, 200, 3), 236, np.uint8)
    cv2.circle(img, (100, 100), 55, (150, 120, 40), -1)
    noise = np.random.default_rng(5).normal(0, 12, (200, 200)).astype(np.float32)
    img = np.clip(img.astype(np.float32) + noise[..., None], 0, 255).astype(np.uint8)
    p = tmp_path / name
    cv2.imwrite(str(p), img)
    return str(p), p.read_bytes()


def test_print_photos_are_sent_exactly_as_shot_when_nothing_is_asked(tmp_path):
    (tmp_path / "one.png").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    out = mw.prepare_print_photos([str(tmp_path / "one.png"), "notes.txt",
                                   str(tmp_path / "gone.png"), ""])
    assert out["photos"] == [str(tmp_path / "one.png")]
    assert out["styled"] is False and out["notes"] == []


def test_a_restyle_writes_a_new_file_and_never_touches_the_photograph(tmp_path):
    src, original_bytes = _bench_photo(tmp_path, "bench.png")
    import cv2
    out = mw.prepare_print_photos([src], "gray", "blue")
    assert out["styled"] is True
    assert (out["background"], out["recolour"]) == ("gray", "blue")
    edited = pathlib.Path(out["photos"][0])
    assert edited.name == "bench_edited_gray_blue.png" and edited.is_file()
    assert str(edited) != src
    got = cv2.imread(str(edited))
    assert int(got[4, 4][0]) - int(got[4, 4][2]) <= 2, "background is not neutral"
    core = got[100, 100]
    assert core[0] > core[2] + 20 and core[0] > core[1] + 20, f"product not blue: {core}"
    assert pathlib.Path(src).read_bytes() == original_bytes, "the photo was overwritten"
    assert len(out["notes"]) == 1 and "->" in out["notes"][0]


def test_a_refused_photo_ships_as_the_original_and_says_so(tmp_path, monkeypatch):
    """The >=3 photo rule must never break because one restyle failed."""
    (tmp_path / "one.png").write_bytes(b"x")
    import editor

    def boom(*a, **k):
        raise RuntimeError("QA refused")
    monkeypatch.setattr(editor, "edit_image", boom)
    out = mw.prepare_print_photos([str(tmp_path / "one.png")], "gray")
    assert out["photos"] == [str(tmp_path / "one.png")]
    assert "left as shot" in out["notes"][0] and "QA refused" in out["notes"][0]


def test_a_typo_in_the_style_is_named_before_a_single_photo_is_touched(tmp_path,
                                                                       monkeypatch):
    (tmp_path / "one.png").write_bytes(b"x")
    import editor

    def boom(*a, **k):
        raise AssertionError("the finisher must not run on a bad option")
    monkeypatch.setattr(editor, "edit_image", boom)
    with pytest.raises(ValueError, match="unknown background"):
        mw.prepare_print_photos([str(tmp_path / "one.png")], "graay")
    with pytest.raises(ValueError, match="unknown colour"):
        mw.prepare_print_photos([str(tmp_path / "one.png")], "", "chartreuse-ish")


def test_the_page_sends_both_options_and_defaults_to_neither(tmp_path):
    html = _page()
    for want in ('id="photo-bg"', 'id="photo-colour"', 'id="photo-hex"'):
        assert want in html, want
    # Only "tidied on this machine" may change a photograph: in the other two
    # modes the hidden selects are sent empty, so a stale value cannot restyle a
    # picture the user asked to leave as shot.
    assert "photo_background: restyle ?" in html
    assert "photo_recolour: restyle" in html
    assert "const restyle = mode === 'restyle'" in html
    assert "id=\"style-box\"" in html and "mode === 'restyle' ? '' : 'none'" in html
    assert '<option value="">Leave it exactly as I shot it</option>' in html
    assert '<option value="">Leave it as printed</option>' in html
    # The marker itself, not a particular number: the page shows it so a stale tab
    # can be spotted, and the title is the same string.
    mark = re.search(r'<span class="build" id="mw-version"[^>]*>(V\d+)</span>', html)
    assert mark, "the dashboard must carry its own version marker"
    assert re.search(r"<title>MakerWorld Pipeline " + mark.group(1) + r"</title>", html), \
        "the title and the marker have to agree"


def test_the_page_draws_pictures_by_default_and_sends_no_originals():
    """The default is the one the user asked for: every publish draws its own
    pictures, so the scraped gallery and the ticked photographs are not even put
    in the payload. The other two modes still work, and each has its own tick."""
    html = _page()
    assert 'id="photo-mode"' in html
    assert '<option value="generate">' in html.split('id="photo-mode"')[1][:220], \
        "generate must be the first (selected by default) option"
    assert "photo_mode: mode" in html and "photo_count: parseInt" in html
    assert "image_paths: gen ? [] : picked" in html
    assert "print_photos: gen ? []" in html
    assert 'id="assert-ai"' in html and 'id="assert-prints"' in html
    assert "onPhotoMode();" in html, "the card must open in the mode it shows"


def test_the_drawer_is_offline_safe_and_names_every_file_it_made(tmp_path):
    import photo_gen
    calls = []

    def fake(prompt_text, seed=0, **kw):
        calls.append((prompt_text, seed, kw.get("timeout")))
        # first two answers are the endpoint's own rate limit, then real pixels
        if len(calls) <= 2:
            raise photo_gen.Busy(402)
        import numpy as np
        import cv2
        rng = np.random.default_rng(7)
        buf = rng.integers(0, 90, size=(600, 800, 3), dtype=np.uint8)
        cv2.rectangle(buf, (200, 120), (600, 520), (30, 90, 200), -1)
        cv2.circle(buf, (400, 300), 60, (240, 240, 240), -1)
        cv2.GaussianBlur(buf, (3, 3), 0, dst=buf)
        return cv2.imencode(".jpg", buf, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()

    out = tmp_path / "downloads"
    slept = []
    got = photo_gen.generate("a printed thing", str(out), name="Cushion Soap Dispenser",
                             fetch=fake, waits=(0, 5, 20), budget=90, sleep=slept.append)
    assert got["ok"] and got["tries"] == 3, got
    assert slept == [5, 20], slept
    p = pathlib.Path(got["path"])
    assert p.name.startswith("ai_cushion_soap_dispenser_") and p.stat().st_size > 1000
    # the budget is a wall clock, not a suggestion: no request may outlive it
    assert all(t and t <= 90 for _, _, t in calls), calls

    built = photo_gen.prompt("Cushion Soap Dispenser", "highly realistic cushion structure")
    for want in ("3D printed", "no watermark", "Exactly one object", "terracotta"):
        assert want.lower() in built.lower(), want
    assert "http" not in built and "<" not in built, "the listing's own markup stays out"

    set_ = photo_gen.generate_photos("Cable Clip", "holds 5 cables", count=3,
                                     out_dir=str(out), fetch=fake, min_photos=3,
                                     budget_each=90, sleep=lambda s: None)
    assert set_["got"] == 3 and set_["ok"] and len(set_["photos"]) == 3
    # a set that comes back short is reported short, not padded
    def dead(prompt_text, **kw):
        raise photo_gen.Busy(402)
    short = photo_gen.generate_photos("Cable Clip", count=3, out_dir=str(out), fetch=dead,
                                      min_photos=3, waits=(0,), budget_each=1,
                                      sleep=lambda s: None)
    assert short["ok"] is False and short["got"] == 0 and "402" in short["notes"][-1]


def test_a_machine_with_no_network_is_stopped_at_once_not_after_four_minutes(tmp_path):
    """The endpoint's own refusals are worth waiting out; a box that cannot reach
    it at all is not. The failure text requests hands back names the pool
    ("HTTPSConnectionPool(host=…)"), so a substring check on "HTTP" reads a wall
    as a queue and sleeps through the whole back-off for every picture."""
    import photo_gen
    slept = []

    def offline(prompt_text, **kw):
        raise RuntimeError("HTTPSConnectionPool(host='image.pollinations.ai', port=443): "
                           "Max retries exceeded with url: /prompt/x (Caused by "
                           "NameResolutionError)")

    out = tmp_path / "downloads"
    got = photo_gen.generate("a printed thing", str(out), fetch=offline,
                             waits=(0, 20, 45, 90, 120, 180), budget=420,
                             sleep=slept.append)
    assert got["ok"] is False and got["tries"] == 1, got
    assert slept == [], "no back-off for a wall that will never come down"
    assert "NameResolutionError" in got["why"], got["why"]
    assert not list(out.iterdir()), "nothing was written"


def test_two_drawings_in_the_same_second_do_not_share_a_file(tmp_path):
    """The cover slot and the photo slot must never be handed the same file —
    that is the "Please set the model cover" rejection — so two pictures written
    in one second get two names."""
    import photo_gen
    import numpy as np
    import cv2
    rng = np.random.default_rng(11)
    buf = rng.integers(0, 90, size=(600, 800, 3), dtype=np.uint8)
    cv2.rectangle(buf, (150, 100), (650, 500), (30, 90, 200), -1)
    frame = cv2.imencode(".jpg", buf, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()

    def always(prompt_text, **kw):
        return frame

    out = tmp_path / "downloads"
    paths = [photo_gen.generate("p " + str(i), str(out), name="Cable Clip", fetch=always,
                                sleep=lambda s: None)["path"] for i in range(3)]
    assert len(set(paths)) == 3, paths
    assert len({os.path.realpath(p) for p in paths}) == 3, "two names, one file"
    assert all(os.path.getsize(p) > 8000 for p in paths)


def _corner_band_var(img):
    """Detail in the bottom-right band where this endpoint puts its name."""
    import cv2
    h, w = img.shape[:2]
    band = cv2.cvtColor(img[h - int(h * 0.08):, int(w * 0.55):], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(band, cv2.CV_64F).var())


def test_a_drawn_picture_is_written_without_the_generators_own_corner_mark(tmp_path):
    """nologo=true is ignored: every picture comes back stamped "pollinations.ai"
    bottom-right, so the strip it sits in must be gone before a file is kept."""
    import cv2
    import numpy as np
    import photo_gen
    h, w = 600, 800
    buf = np.repeat(np.linspace(40, 90, w, dtype=np.uint8).reshape(1, w, 1),
                    h, axis=0).repeat(3, axis=2).copy()
    cv2.rectangle(buf, (200, 90), (600, 460), (30, 90, 200), -1)
    cv2.putText(buf, "pollinations.ai", (470, h - 18), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (250, 250, 250), 2, cv2.LINE_AA)
    marked = cv2.imencode(".jpg", buf, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
    assert photo_gen._decode(marked), "the fixture has to pass as a real photograph"
    assert _corner_band_var(cv2.imdecode(np.frombuffer(marked, np.uint8), 1)) > 60, \
        "control: the mark must be visible in the band before the crop"

    out = tmp_path / "ai_marked_0_000000.jpg"
    out.write_bytes(photo_gen._clean(marked))
    clean = cv2.imread(str(out))
    assert clean.shape[1] == w and clean.shape[0] < h, "the frame is cut, not stretched"
    assert _corner_band_var(clean) < 60, "the corner mark is still in the picture"
    assert cv2.countNonZero(cv2.inRange(clean, (25, 85, 200), (35, 95, 210))) > 0, \
        "the object itself survives the cut"

    # the same thing through the path a publish actually uses
    def one(prompt_text, **kw):
        return marked

    kept = photo_gen.generate("a printed thing", str(tmp_path / "d"), name="Cable Clip",
                              fetch=one, sleep=lambda s: None)
    assert kept["ok"]
    drawn = cv2.imread(kept["path"])
    assert drawn.shape[0] < h and _corner_band_var(drawn) < 60, kept["path"]


def test_a_flat_error_card_is_not_counted_as_a_photograph(tmp_path):
    import photo_gen
    import numpy as np
    flat = np.full((600, 800, 3), 200, dtype=np.uint8)
    import cv2
    blank = cv2.imencode(".jpg", flat)[1].tobytes()
    assert photo_gen._decode(blank) is None, "the endpoint answers a 402 with an image"
    assert photo_gen._decode(b"\xff\xd8\xff" + b"0" * 20) is None      # too small
    assert photo_gen._decode(b"not an image at all, longer than 8kB" + b"x" * 9000) is None


def test_the_cover_and_photo_slots_never_share_a_file():
    from makerworld_flow import split_photo_sets
    files = [f"f{i}.jpg" for i in range(5)]
    covers, shots = split_photo_sets(files)
    assert covers == files[:2] and shots == files[2:]
    covers, shots = split_photo_sets(files[:4])
    assert not set(covers) & set(shots) and len(shots) >= 3
    covers, shots = split_photo_sets(files[:3])
    assert covers and not set(covers) & set(shots), \
        "a short set is reported short, not duplicated across both slots"
    assert split_photo_sets([]) == ([], [])


def test_neither_a_drawn_nor_another_makers_photo_can_be_ticked_as_a_print(tmp_path, monkeypatch):
    """Two kinds of file sit in downloads/ and neither is a photograph of the
    user's print: the studio's drawings (ai_*) and the source gallery the
    downloader writes as {slug}_img_N. The tick next to a thumbnail is the claim
    MakerWorld checks, so the route has to name both apart from the rest."""
    from fastapi.testclient import TestClient
    import main
    (tmp_path / "my_print.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 200)
    (tmp_path / "ai_cushion_0_1200.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 200)
    (tmp_path / "cable_clip_set_img_2.webp").write_bytes(b"RIFFxxxxWEBP" + b"x" * 200)
    monkeypatch.setattr(main, "DOWNLOADS_DIR", tmp_path)
    files = TestClient(main.app).get("/makerworld/files").json()["files"]
    by = {f["name"]: f for f in files}
    assert by["ai_cushion_0_1200.jpg"]["generated"] is True
    assert by["cable_clip_set_img_2.webp"]["from_source"] is True
    assert by["my_print.jpg"] == {**by["my_print.jpg"], "generated": False,
                                  "from_source": False}
    html = _page()
    assert "(f.generated || f.from_source)" in html
    assert "cannot be ticked as a print you made" in html
    assert "from another maker’s listing" in html


def _mw_publish(client, calls, record=None, **payload):
    """POST the real route with the browser driver and the ledger both watched."""
    import main
    body = dict(name="Cable Clip", description="holds five cables in a row",
                stl_path="cable.3mf", kind="remix",
                source_url="https://makerworld.com/en/models/2964241-gt-r",
                remix_source_url="https://makerworld.com/en/models/2964241-gt-r",
                license_raw="CC BY 4.0", photo_mode="generate", photo_count=3,
                assert_ai=True,
                image_paths=[], print_photos=[], tags=["cable"], category="Organization")
    body.update(payload)
    record = [] if record is None else record

    async def driver(**kw):
        calls.append(kw)
        return {"success": True, "design_id": "5001",
                "url": "https://makerworld.com/en/models/5001-clip", "verify": {}}
    monkey = pytest.MonkeyPatch()
    monkey.setattr(main, "makerworld_upload", driver)
    monkey.setattr(main.makerworld_ledger, "record",
                   lambda **kw: record.append(kw))
    try:
        return client.post("/upload/makerworld", json=body)
    finally:
        monkey.undo()


def _draw_five(monkeypatch):
    """A drawer that fills the set, for tests about everything except the draw."""
    import main

    def full(name, description="", count=0):
        files = [f"/d{i}.jpg" for i in range(5)]
        return {"ok": True, "got": 5, "asked": 3, "seconds": 1, "photos": files,
                "covers": files[:2], "print_photos": files[2:], "notes": [],
                "generated": True}
    monkeypatch.setattr(main, "_makerworld_generated_photos", full)


def test_a_short_draw_refuses_the_publish_and_opens_no_browser(tmp_path, monkeypatch):
    """The headline guarantee, run through the route rather than read out of it:
    when the drawer cannot fill the set, nothing opens, nothing is recorded, and
    the source listing's own photographs are not used to make up the difference."""
    from fastapi.testclient import TestClient
    import main

    def short(name, description="", count=0):
        return {"ok": False, "got": 4, "asked": 3, "covers": ["d0.jpg"],
                "print_photos": ["d1.jpg", "d2.jpg", "d3.jpg"],
                "notes": ["picture 5 of 5 did not come back — HTTP 402"],
                "seconds": 300, "photos": [], "generated": True}
    monkeypatch.setattr(main, "_makerworld_generated_photos", short)
    calls, rec = [], []
    client = TestClient(main.app)
    r = _mw_publish(client, calls, rec,
                    image_paths=["other_maker_img_0.webp"],
                    print_photos=["other_maker_img_1.webp"])
    assert r.status_code == 502, r.text
    assert "nothing was published" in r.json()["detail"]
    assert "402" in r.json()["detail"]
    assert calls == [], "the form driver ran on a short set"
    assert rec == [], "the ledger was written for a publish that never happened"
    assert mwl.find(name="Cable Clip", source_url="https://makerworld.com/en/models/2964241-gt-r"
                    )["found"] is False


def test_a_full_draw_reaches_the_form_and_no_original_file_with_it(tmp_path, monkeypatch):
    """The other half: a complete set goes to the form, split so the two slots
    never share a file, and the source gallery the page also knows about is not
    part of either."""
    from fastapi.testclient import TestClient
    import main
    drawn = [str(tmp_path / f"d{i}.jpg") for i in range(5)]

    def full(name, description="", count=0):
        return {"ok": True, "got": 5, "asked": 3, "seconds": 300, "photos": list(drawn),
                "covers": drawn[:2], "print_photos": drawn[2:], "notes": [],
                "generated": True}
    monkeypatch.setattr(main, "_makerworld_generated_photos", full)
    calls, rec = [], []
    r = _mw_publish(TestClient(main.app), calls, rec,
                    image_paths=[str(tmp_path / "other_maker_img_0.webp")],
                    print_photos=[str(tmp_path / "my_print.jpg")])
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["image_paths"] == drawn[:2]
    assert calls[0]["print_photos"] == drawn[2:]
    assert not set(calls[0]["image_paths"]) & set(calls[0]["print_photos"])
    leaked = set(calls[0]["image_paths"]) | set(calls[0]["print_photos"])
    assert "other_maker_img_0.webp" not in " ".join(leaked)
    assert "my_print.jpg" not in " ".join(leaked)
    assert rec and rec[0]["outcome"] == "published"
    assert r.json()["photo_draw"]["files"] == ["d2.jpg", "d3.jpg", "d4.jpg"]


def test_an_unknown_photo_mode_cannot_slip_an_original_onto_the_form(monkeypatch):
    """photo_mode is a string from the browser. Anything that is not one of the
    three names must fall to the drawer, because the other two are the modes that
    attach files the caller named — a typo is not a licence to publish another
    maker's photograph."""
    import main
    for bad in ("", "Generate", "generate ", "as_shots", "original", "GENERATE"):
        assert main._makerworld_photo_mode(bad) == "generate", bad
    for good in ("generate", "as_shot", "restyle"):
        assert main._makerworld_photo_mode(good) == good
    # and the route honours it: with mode "as_shots" and files attached, the
    # drawer still decides what the form gets.
    from fastapi.testclient import TestClient
    _draw_five(monkeypatch)
    calls, rec = [], []
    r = _mw_publish(TestClient(main.app), calls, rec, photo_mode="as_shots",
                    image_paths=["/tmp/other_maker_img_0.webp"],
                    print_photos=["/tmp/other_maker_img_1.webp"])
    assert r.status_code == 200, r.text
    assert calls[0]["image_paths"] == ["/d0.jpg", "/d1.jpg"]
    assert calls[0]["print_photos"] == ["/d2.jpg", "/d3.jpg", "/d4.jpg"]


def test_as_shot_sends_the_photographs_untouched_and_restyle_does_not(monkeypatch, tmp_path):
    """The page's two own-photo modes differ by one thing, and the server has to
    keep them apart: "exactly as I shot them" must not run the finisher even if a
    stale backdrop is still in the form."""
    import main
    shots = []
    for i in range(4):
        p = tmp_path / f"print{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + b"x" * 400)
        shots.append(str(p))
    seen = {}

    def style(paths, background="", recolour=""):
        seen["args"] = (list(paths), background, recolour)
        return {"photos": list(paths), "styled": bool(background or recolour),
                "background": background, "recolour": recolour, "notes": []}
    monkeypatch.setattr(main.makerworld_flow, "prepare_print_photos", style)
    from fastapi.testclient import TestClient
    calls = []
    r = _mw_publish(TestClient(main.app), calls, [], photo_mode="as_shot",
                    assert_prints=True, print_photos=shots,
                    photo_background="gray", photo_recolour="blue")
    assert r.status_code == 200, r.text
    assert seen["args"][1] == "" and seen["args"][2] == "", \
        "as_shot was allowed to restyle"
    # no cover ticked: one of the user's own photographs becomes it, and the
    # photo slot keeps the rest — never the same file twice.
    assert calls[0]["image_paths"] == shots[:1]
    assert calls[0]["print_photos"] == shots[1:]
    assert not set(calls[0]["image_paths"]) & set(calls[0]["print_photos"])


def test_nothing_is_sent_or_started_without_the_tick(monkeypatch):
    """The two acknowledgements are ticks on the page, and the page's own words
    promise "without this tick nothing is sent". This route has other callers —
    scripts, curl, a stale tab — so both ticks and the unattended start are
    answered here too, before a picture is drawn or a browser is opened."""
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    drew, calls = [], []
    monkeypatch.setattr(main, "_makerworld_generated_photos",
                        lambda *a, **k: (drew.append("drew"), {"ok": True})[1])
    r = _mw_publish(client, calls, [], assert_ai=False)
    assert r.status_code == 400, r.text
    assert "machine" in r.json()["detail"] and "nothing is sent" in r.json()["detail"]
    assert drew == [] and calls == [], "the draw ran on an unacknowledged publish"

    r = _mw_publish(client, calls, [], assert_ai=False, assert_prints=False,
                    photo_mode="as_shot", print_photos=["/p1.jpg", "/p2.jpg", "/p3.jpg"])
    assert r.status_code == 400, r.text
    assert "print" in r.json()["detail"]
    assert calls == []

    opened = []
    before = main.MW_AUTO.get("state")
    monkeypatch.setattr(main, "makerworld_ensure_browser",
                        lambda: opened.append("browser") or False)
    r = client.post("/makerworld/auto/start", json={"count": 1})
    assert r.status_code == 400, r.text
    assert "machine" in r.json()["detail"]
    assert opened == [], "the run went looking for a browser before it asked permission"
    assert main.MW_AUTO.get("state") == before, \
        "a refused start still changed the run state"
    html = _page()
    assert "assert_ai: gen ?" in html and "assert_prints: gen ?" in html
    assert 'id="assert-ai-auto"' in html and "assert_ai: true" in html


def test_a_duplicate_that_points_at_the_draft_does_not_lock_the_model(monkeypatch):
    """MakerWorld's page text carries the description just typed, so "already
    exists" found there is not a rejection. Locking on it hid a model that was
    never published, on a draft link, for good."""
    import main
    _draw_five(monkeypatch)
    from fastapi.testclient import TestClient
    dup = {"success": False, "duplicate": True, "draft_id": "8899",
           "url": "https://makerworld.com/en/models/upload/3mf?draft=8899",
           "error": "MakerWorld says this model already exists (already exists).",
           "verify": {}}

    async def driver(**kw):
        return dup
    monkeypatch.setattr(main, "makerworld_upload", driver)
    rec = []
    monkeypatch.setattr(main.makerworld_ledger, "record", lambda **kw: rec.append(kw))
    r = TestClient(main.app).post("/upload/makerworld", json=dict(
        name="Cable Clip", description="holds five cables", stl_path="c.3mf",
        source_url="https://makerworld.com/en/models/2964241-gt-r",
        remix_source_url="https://makerworld.com/en/models/2964241-gt-r",
        license_raw="CC BY 4.0", photo_mode="generate", assert_ai=True))
    assert r.status_code == 200, r.text
    assert rec == [], "a draft-page duplicate locked a model that never went out"
    # the same verdict with a real design behind it is believed
    dup["url"] = "https://makerworld.com/en/models/7777-cable-clip"
    r2 = TestClient(main.app).post("/upload/makerworld", json=dict(
        name="Cable Clip", description="holds five cables", stl_path="c.3mf",
        source_url="https://makerworld.com/en/models/2964241-gt-r",
        remix_source_url="https://makerworld.com/en/models/2964241-gt-r",
        license_raw="CC BY 4.0", photo_mode="generate", assert_ai=True))
    assert r2.status_code == 200, r2.text
    assert rec and rec[0]["outcome"] == "published"


def test_cc0_is_refused_before_anything_opens(monkeypatch):
    """Measured: the remix form's two licence questions come back as CC BY, so a
    public-domain source cannot be represented at all. The refusal has to happen
    in the plan and in the run loop, not in front of a half-filled form."""
    for raw in ("CC0", "CC0 1.0 Universal", "Public Domain Mark 1.0",
                "https://creativecommons.org/publicdomain/zero/1.0/"):
        lp = mw.license_plan(raw)
        assert lp["refused"] is True, raw
        assert "no answer" in lp["why"] or "CC BY" in lp["why"], lp
    assert mw.license_plan("CC BY 4.0")["refused"] is False
    assert mw.license_plan("CC BY-SA 4.0")["refused"] is False
    # and the plan route carries it as a blocker, with no browser involved
    from fastapi.testclient import TestClient
    import main
    d = TestClient(main.app).post("/upload/makerworld/plan", json=dict(
        name="Cable Clip", stl_path="c.3mf", license_raw="CC0",
        source_url="https://makerworld.com/en/models/2964241-gt-r",
        remix_source_url="https://makerworld.com/en/models/2964241-gt-r")).json()
    assert d["ok"] is False
    assert any("Refusing" in b for b in d["blockers"]), d["blockers"]


def test_the_plan_in_generate_mode_describes_the_run_it_is_checking(tmp_path, monkeypatch):
    """The check button must not contradict the publish button: in this mode the
    page sends no files at all, because the pictures are drawn when the run
    starts, so the plan has to count the set that will be drawn."""
    from fastapi.testclient import TestClient
    import main
    stl = tmp_path / "clip.3mf"
    stl.write_bytes(b"not really a 3mf")
    client = TestClient(main.app)
    body = dict(name="Cable Clip", description="holds five cables", stl_path=str(stl),
                source_url="https://makerworld.com/en/models/2964241-gt-r",
                remix_source_url="https://makerworld.com/en/models/2964241-gt-r",
                license_raw="CC BY 4.0", photo_mode="generate", photo_count=3,
                image_paths=[], print_photos=[], category="Organization")
    d = client.post("/upload/makerworld/plan", json=body).json()
    assert d["ok"] is True, d
    assert d["photos"]["will_generate"] == 5
    assert not any("cover" in b.lower() for b in d["blockers"]), d["blockers"]
    assert not any("real print photo" in b.lower() for b in d["blockers"]), d["blockers"]
    # a mode that names files is still checked against the files it names
    d2 = client.post("/upload/makerworld/plan",
                     json=dict(body, photo_mode="as_shot", photo_count=0)).json()
    assert d2["ok"] is False
    assert any("print photo" in b.lower() for b in d2["blockers"]), d2["blockers"]
    assert d2["photos"]["will_generate"] == 0


def test_the_publish_route_settles_the_pictures_before_it_opens_the_browser():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    body = re.search(r"@app.post\(\"/upload/makerworld\"\).*?\n\n\n@app.get",
                     src, re.S).group(0)
    assert 'makerworld_flow.prepare_print_photos(' in body
    assert "_makerworld_generated_photos" in body
    # neither the drawer nor the finisher runs inside the browser lock, and a
    # short drawn set is a 502 that publishes nothing rather than a fallback
    assert body.index("_makerworld_generated_photos") < body.index("async with UPLOAD_LOCK")
    assert body.index("prepare_print_photos") < body.index("async with UPLOAD_LOCK")
    assert 'print_photos=print_photos' in body and 'image_paths=cover_photos' in body
    assert "print_photos=req.print_photos" not in body
    assert 'status_code=400' in body and 'status_code=502' in body


def test_the_ai_studio_is_its_own_page_and_says_what_it_draws():
    """The one place in this app that produces pixels nobody photographed. It is
    a separate address, it is labelled, and it publishes nothing."""
    from fastapi.testclient import TestClient
    import main
    html = (ROOT / "makerworld" / "ai_studio.html").read_text(encoding="utf-8")
    assert "js.puter.com/v2/" in html and "puter.ai.txt2img" in html
    for must in ("AI-generated image", "not a photograph of a print", "ai_"):
        assert must in html, must
    assert "<form" not in html.lower()
    c = TestClient(main.app)
    r = c.get("/makerworld/studio")
    assert r.status_code == 200 and "MakerWorld AI Studio" in r.text
    # the dashboard links to it, and the link does not pretend it is a photo tool
    page = _page()
    assert 'href="/makerworld/studio"' in page


def test_the_ai_save_route_writes_an_ai_prefixed_file_only(tmp_path, monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main, "DOWNLOADS_DIR", tmp_path)
    c = TestClient(main.app)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\ndemo bytes").decode()
    d = c.post("/makerworld/ai/save",
               json={"name": "../../windows.png", "data_url": "data:image/png;base64," + png}).json()
    assert d["name"].startswith("ai_") and d["generated"] is True
    assert (tmp_path / d["name"]).is_file()
    assert not (tmp_path.parent / "windows.png").exists()
    assert c.post("/makerworld/ai/save", json={"name": "x.png",
               "data_url": "data:text/html;base64,xx"}).status_code == 400
    assert c.post("/makerworld/ai/save", json={"name": "x.png",
               "data_url": "not an image"}).status_code == 400


def test_the_studio_words_are_said_every_time_not_only_when_asked():
    """The instruction is standing: strip the text, keep the object, then change
    colour and backdrop. If any of those three became optional the prompt would
    silently stop saying it, so they are checked inside the sentence builder."""
    html = (ROOT / "makerworld" / "ai_studio.html").read_text(encoding="utf-8")
    body = re.search(r"function buildPrompt\(\) \{.*?\n\}", html, re.S).group(0)
    for always in ("remove every word", "keep the object itself identical",
                   "replace the background with"):
        assert always in body, always
        # sitting in the unconditional parts list, not behind an if
        assert body.index(always) < body.index("if (extra)"), always
    assert re.search(r"buildPrompt\(\);", html), "no prompt on first load"




def test_the_creality_page_no_longer_publishes_to_makerworld():
    html = CXY_PAGE.read_text(encoding="utf-8")
    for banned in ("/upload/makerworld", "makerworld-section", "mw-real-photos",
                   "_mwPayload", "print_photos"):
        assert banned not in html, banned
    assert 'href="/makerworld"' in html      # one link between them, nothing more


def test_the_files_route_lists_only_images_already_in_downloads(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    (tmp_path / "print1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "print2.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "model.stl").write_text("solid x\nendsolid x\n", encoding="ascii")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    monkeypatch.setattr(main, "DOWNLOADS_DIR", tmp_path)
    d = TestClient(main.app).get("/makerworld/files").json()
    assert sorted(f["name"] for f in d["files"]) == ["print1.png", "print2.jpg"]
    for f in d["files"]:
        assert pathlib.Path(f["path"]).is_absolute()
        assert pathlib.Path(f["path"]).parent == tmp_path
        assert f["url"] == "/file/" + f["name"]


def test_the_memory_route_shows_the_makerworld_file_and_forget_never_guesses(
        isolated_ledger, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main, "DOWNLOADS_DIR", isolated_ledger.parent)
    mwl.record(name="Squishy Clicker", outcome="published",
               published_url="https://makerworld.com/en/models/7-clicker")
    mwl.record(name="The One That Failed", outcome="failed")
    c = TestClient(main.app)
    d = c.get("/makerworld/published").json()
    assert d["path"] == str(isolated_ledger)
    assert (d["count"], d["published"], d["retryable"]) == (2, 1, 1)
    assert d["entries"][0]["published_url"] == "https://makerworld.com/en/models/7-clicker"
    # an empty match must not be treated as "forget everything"
    assert c.post("/makerworld/forget", json={"match": "   "}).status_code == 400
    assert mwl.count() == 2
    assert c.post("/makerworld/forget", json={"match": "Squishy Clicker"}).json()["removed"] == 1
    assert mwl.count() == 1


def test_a_frozen_background_tab_cannot_hang_the_run(monkeypatch):
    """The measured cause of "it never uploads": two discarded tabs in the debug
    Chrome made Playwright's connect_over_cdp — which initialises every tab before
    it answers — never return, so the download thread was still attaching when its
    own wall clock ran out and the row said "the download did not arrive". The
    attach now has a bound and wakes the tabs between tries."""
    import asyncio
    import makerworld_uploader as mu

    woke = []
    monkeypatch.setattr(mu, "wake_wedged_tabs",
                        lambda wait=1.5: (woke.append(len(woke)), 3)[1])

    class Wedged:
        n = 0

        async def connect_over_cdp(self, url):
            type(self).n += 1
            if type(self).n < 3:
                await asyncio.sleep(30)         # the frozen-tab symptom
            return "browser"

    class PW:
        chromium = Wedged()

    got = asyncio.run(mu.cdp_attach(PW(), timeout=0.4, tries=3))
    assert got == "browser"
    assert woke == [0, 1], woke                  # woken once per failed attach
    assert Wedged.n == 3

    # a browser with nothing to wake is said out loud, not waited on
    monkeypatch.setattr(mu, "wake_wedged_tabs", lambda wait=1.5: 0)

    class Dead:
        async def connect_over_cdp(self, url):
            await asyncio.sleep(30)

    class DeadPW:
        chromium = Dead()

    with pytest.raises(RuntimeError) as e:
        asyncio.run(mu.cdp_attach(DeadPW(), timeout=0.4, tries=3))
    assert "frozen tab" in str(e.value)


def test_the_wake_pokes_only_page_tabs_and_survives_a_closed_browser(monkeypatch):
    import makerworld_uploader as mu
    hits = []

    class Resp:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.payload

    def fake_urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        hits.append(url)
        if url.endswith("/json"):
            return Resp(json.dumps([
                {"type": "page", "id": "A1"}, {"type": "page", "url": "x"},
                {"type": "iframe", "id": "B2"},
                {"type": "browser_ui", "id": "C3"}]).encode())
        return Resp(b"Target activated")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert mu.wake_wedged_tabs(wait=0) == 1        # only the page target with an id
    assert [h for h in hits if "/json/activate/" in h] == [mu.CDP_URL + "/json/activate/A1"]

    def boom(req, timeout=None):
        raise OSError("nothing is listening")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert mu.wake_wedged_tabs(wait=0) == 0, "a closed browser must not raise"


def test_the_download_wall_is_longer_than_what_it_waits_on():
    """The bug was arithmetic: a 180s join around 90s of page load, 7s of settle
    and 90s of download wait, so the thread was always still working when the run
    gave up on it — and the reason it was still working was thrown away."""
    src = (ROOT / "downloader.py").read_text(encoding="utf-8")
    body = re.search(r"def _mw_download_via_chrome.*?\n    return holder\[.path.\], ..",
                     src, re.S).group(0)
    join = int(re.search(r"t\.join\(timeout=(\d+)\)", body).group(1))
    waits = [int(x) for x in re.findall(r"timeout=(\d{4,})\)", body)]
    assert join >= sum(waits) / 1000 + 20, (join, waits)
    assert "cdp_attach" in body, "the download must use the bounded attach"
    assert "holder[\"stage\"]" in body or 'holder["stage"]' in body
    assert "stalled while" in body, "the row must say which stage it stalled at"


def test_the_cover_slots_point_at_the_cover_boxes_not_the_model_files_box():
    """Measured 2026-10-26 on live draft 10203098: the Raw Model Files dropzone
    carries the same `.dropzone` class as the 4:3 cover, so the shipped root
    `.dropzone:not(.appUpload)` resolved to file input 0 — the 3D-file input —
    and the cover picture was handed to the model upload. `.dropzone.appUpload`
    matched nothing at all, because `appUpload` is the dropzone's PARENT, not a
    class on it. Both attaches reported success; MakerWorld then refused the
    publish with its own "Please set the model cover"."""
    src = (ROOT / "makerworld_uploader.py").read_text(encoding="utf-8")
    js = re.search(r'SLOT_INDEX_JS = r"""(.*?)"""', src, re.S).group(1)
    roots = {name: re.findall(r'"([^"]+)"', sel)
             for name, sel in re.findall(r'\["([^"]+)",\s*\[(.*?)\]\]', js)}
    cover = roots[mw.SLOT_COVER_43] + roots[mw.SLOT_COVER_34]
    assert roots[mw.SLOT_COVER_43], "the 4:3 cover slot has no root at all"
    assert roots[mw.SLOT_COVER_34], "the 3:4 cover slot has no root at all"
    for sel in cover:
        assert "js-scroll-cover" in sel or "appUpload" in sel, (
            f"{sel!r} is not scoped inside the covers section, so it can reach the "
            "model-files dropzone")
        assert ".dropzone.appUpload" not in sel, (
            "appUpload is the dropzone's parent — this matches nothing")
        assert sel != ".dropzone:not(.appUpload)", "the model-files box also carries .dropzone"


def test_an_empty_cover_box_stops_the_run_before_publish_is_pressed():
    """A cover attached to the wrong input still reports ok, so the attach call
    can never be the check. The wizard has to ask the form whether its own
    required box holds a picture and stop there — not press Publish and read the
    rejection back out of MakerWorld's error list."""
    src = (ROOT / "makerworld_uploader.py").read_text(encoding="utf-8")
    body = src[src.index("async def upload("):]
    read = body.index("COVER_STATE_JS")
    press = body.index("_click_button(page, mw.PUBLISH_BUTTON)")
    assert read < press, "the cover box is checked after Publish, which is too late"
    guard = body[read:press]
    assert "required_set" in guard, "the check must be on the form's own required cover box"
    assert '"success": False' in guard, "an empty required cover must stop the run"
    assert "Nothing was published" in guard
    assert '"cover_state"' in guard, "the row must carry what the form reported"
