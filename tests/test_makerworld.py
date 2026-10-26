"""Browser-free tests for the MakerWorld side: the plan the pipeline publishes
from, its separate dedup memory, and the refusals that must happen before any
tab opens. Nothing here connects to Chrome or reads the account (AGENTS.md)."""
from __future__ import annotations

import ast
import asyncio
import json
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
    # the photographs are the source listing's own files, straight off the fetch
    assert "print_photos=photos" in body and "usable_images(res.get(\"image_paths\")" in body
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
    # the photographs are the source listing's own, and the source is declared
    assert calls[0]["print_photos"] == calls[0]["image_paths"]
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
    assert "print_photos.length >= 3" in html
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
    assert "photo_background: document.getElementById('photo-bg').value" in html
    assert "photo_recolour: document.getElementById('photo-hex').value.trim()" in html
    assert '<option value="">Leave it exactly as I shot it</option>' in html
    assert '<option value="">Leave it as printed</option>' in html
    assert '<span class="build" id="mw-version" title="MakerWorld dashboard ' \
           'version: bumped on every update">V6</span>' in html


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


def test_the_publish_route_restyles_before_it_opens_the_browser():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    body = re.search(r"@app.post\(\"/upload/makerworld\"\).*?\n\n\n@app.get",
                     src, re.S).group(0)
    assert 'makerworld_flow.prepare_print_photos(' in body
    assert 'print_photos=prep["photos"]' in body
    assert "print_photos=req.print_photos" not in body
    # the restyle is outside the browser lock, and a bad option is a 400
    assert body.index("prepare_print_photos") < body.index("async with UPLOAD_LOCK")
    assert 'status_code=400' in body


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
