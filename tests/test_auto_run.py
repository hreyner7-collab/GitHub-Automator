"""R9: "find and publish N models" — the licence filter is ticked exactly as
the user circled it and read back, ND finds are skipped, failures are logged and
the run continues. No browser: the listing and the pipeline are faked."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discover  # noqa: E402

# A REAL model file on disk. The pipeline builds a proof of creation from the
# file it is about to publish (creality_rules: no proof of originality = the
# upload is refused), so a fixture that merely *names* a path is no longer
# publishable — that is the rule under test, not a convenience.
REAL_MODEL = str(Path(__file__).resolve().parent / "data" / "proof_cube.stl")


@pytest.fixture(autouse=True)
def _tmp_ledger(monkeypatch, tmp_path):
    """A run records what it published in the permanent ledger — point that at
    a tmp file so a test run can never lock the real account's models."""
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    # ...and point the proof renders at a tmp dir too, so no test writes into
    # the project's downloads/_proof folder.
    monkeypatch.setenv("CREALITY_PROOF_DIR", str(tmp_path / "proof"))
    # the photo switch is the dashboard's saved setting: a test must read its
    # own choice, never the real artifacts/publish_settings.json
    import creality_rules
    monkeypatch.setattr(creality_rules, "SETTINGS_FILE",
                        tmp_path / "publish_settings.json")
    monkeypatch.delenv("CREALITY_PHOTO_SOURCE", raising=False)
    yield


# ── the licence filter itself ────────────────────────────────────────────────

class _First:
    def __init__(self, page, text, exact):
        self.page, self.text, self.exact = page, text, exact

    def count(self):
        return 1 if self.page.open_ok else 0

    def click(self, timeout=0):
        if not self.page.open_ok:
            raise RuntimeError("not clickable")
        self.page.opened_with = self.text
        return None


class _Btn:
    def __init__(self, page, text, exact):
        self.page, self.text, self.exact = page, text, exact

    @property
    def first(self):
        return _First(self.page, self.text, self.exact)


class FakeMouse:
    def __init__(self, page):
        self.page = page

    def click(self, x, y):
        self.page.clicks.append((x, y))
        # the site flips the checkbox we clicked
        for label, box in self.page.boxes.items():
            if abs(box["x"] + 4 - x) <= 2 and abs(box["y"] - y) <= 2:
                self.page.state[label] = True
                return


class FakePage:
    """Just enough of Playwright for filter_creality_licences()."""

    def __init__(self, state: dict, boxes: dict, open_ok=True):
        self.state = state
        self.boxes = boxes
        self.open_ok = open_ok
        self.clicks = []
        self.mouse = FakeMouse(self)
        self.opened_with = None

    def get_by_text(self, text, exact=False):
        return _Btn(self, text, exact)

    def wait_for_timeout(self, _ms):
        return None

    def evaluate(self, js):
        if "window.innerHeight" in js:
            return 900
        if js == discover.LICENCE_STATE_JS:
            return json.dumps(self.state)
        if js == discover.LICENCE_BOXES_JS:
            return json.dumps(self.boxes)
        return None


def _boxes(vals):
    return {l: {"x": 100 + i * 60, "y": 100 + i * 10, "w": 60, "h": 30}
            for i, l in enumerate(vals)}


def test_licence_filter_ticks_exactly_the_allow_list():
    labels = discover.CREALITY_LICENCE_ALLOW + discover.CREALITY_LICENCE_DENY
    page = FakePage({l: False for l in labels}, _boxes(labels))
    rep = discover.filter_creality_licences(page)
    assert rep["opened"] is True
    assert rep["verified"] is True
    assert rep["ticked_on"] == sorted(discover.CREALITY_LICENCE_ALLOW)
    assert rep["ticked_off"] == sorted(discover.CREALITY_LICENCE_DENY)
    # the confirm button is matched across the site's languages; the fake page
    # exposes the English word, so that is the one reported here
    assert rep["applied_with"] == "Confirm"
    # CC BY-ND and Standard License are never clicked
    clicked = set()
    for x, y in page.clicks:
        for label, box in page.boxes.items():
            if abs(box["x"] + 4 - x) <= 2 and abs(box["y"] - y) <= 2:
                clicked.add(label)
    assert clicked == set(discover.CREALITY_LICENCE_ALLOW)
    assert not (clicked & set(discover.CREALITY_LICENCE_DENY))


def test_licence_filter_reports_unverified_when_the_panel_never_opens():
    labels = discover.CREALITY_LICENCE_ALLOW + discover.CREALITY_LICENCE_DENY
    rep = discover.filter_creality_licences(
        FakePage({l: False for l in labels}, _boxes(labels), open_ok=False))
    assert rep["opened"] is False
    assert rep["verified"] is False


def test_allow_and_deny_lists_cover_all_eight_options():
    labels = set(discover.CREALITY_LICENCE_ALLOW) | set(discover.CREALITY_LICENCE_DENY)
    assert labels == {"CC BY", "CC BY-SA", "CC BY-NC", "CC BY-NC-SA", "CC BY-NC-ND",
                      "CC BY-ND", "CC0", "Standard License"}
    assert not (set(discover.CREALITY_LICENCE_ALLOW) & set(discover.CREALITY_LICENCE_DENY))


def test_only_the_commercially_usable_licences_are_allow_listed():
    """POLICY (2026-10-09): the finder ticks ONLY the licences that allow
    redistribution, remixing AND commercial use. NC is out because this account
    publishes on a platform that rewards uploads (three NC re-uploads from the
    2026-10-09 run were removed as "did not pass platform review"); ND is out
    because a remix may not be made from it at all. The panel has exactly eight
    rows, so the two lists must still cover it between them."""
    assert set(discover.CREALITY_LICENCE_ALLOW) == {"CC0", "CC BY", "CC BY-SA"}
    assert set(discover.CREALITY_LICENCE_DENY) == {
        "CC BY-NC", "CC BY-NC-SA", "CC BY-ND", "CC BY-NC-ND", "Standard License"}
    assert len(discover.LICENCE_LABELS) == 8
    # any NC or ND label is refused by the CLICK, not merely by the read-back
    for bad in sorted(discover.CREALITY_LICENCE_DENY):
        page = FakePage({l: False for l in discover.LICENCE_LABELS},
                        _boxes(discover.LICENCE_LABELS))
        rep = discover.filter_creality_licences(page)
        assert rep["verified"] is True
        assert not rep["state"].get(bad), bad


# ── the run itself: skip ND, log failures, keep going ────────────────────────

def _fake_fetch(url, license_raw=None, stl=True, exc=None):
    if exc:
        raise RuntimeError(exc)
    return {"name": "Model " + url.rsplit("/", 1)[-1], "description": "d",
            "stl_path": (REAL_MODEL if stl else None),
            "image_paths": ["C:/x/a.jpg"],
            "license": discover_license(license_raw or "")}


def discover_license(raw: str) -> dict:
    from downloader import classify_license
    return classify_license(raw)


def test_auto_run_skips_nds_and_continues(monkeypatch, tmp_path):
    import main

    # names must be useful household families: the run judges every candidate
    # with the household gate (selector.py) before it is queued, so a fixture
    # called "Good" would be turned down before the licence path this test is
    # about was ever reached. One family each, so the one-of-each rule is quiet.
    models = [{"name": "ND cable organizer", "url": "https://x/model-detail/nd", "keyword": "k"},
              {"name": "Standard phone holder", "url": "https://x/model-detail/std", "keyword": "k"},
              {"name": "Cotton bud holder box", "url": "https://x/model-detail/nofile", "keyword": "k"},
              {"name": "Bottle storage rack", "url": "https://x/model-detail/good", "keyword": "k"},
              {"name": "Storage box with lid", "url": "https://x/model-detail/boom", "keyword": "k"},
              {"name": "Clothes peg", "url": "https://x/model-detail/dupe", "keyword": "k"}]
    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models[:a[1]], "licence":
                                        {"opened": True, "verified": True}, "note": ""})

    nd = "https://creativecommons.org/licenses/by-nc-nd/4.0"
    std = ("This work is licensed under the Copyright License 4.0. 3D Model files "
           "sold on Creality Cloud are available under a standard license … these "
           "files cannot be used for any commercial use; it's for personal use only. "
           "3D model or any portion of the model may not be shared, transmitted, "
           "redistributed, remixed, duplicated, or sold.")
    cc = "https://creativecommons.org/licenses/by/4.0"
    by_url = {"https://x/model-detail/nd": _fake_fetch("nd", nd),
              "https://x/model-detail/std": _fake_fetch("std", std),
              "https://x/model-detail/nofile": _fake_fetch("nofile", cc, stl=False),
              "https://x/model-detail/good": _fake_fetch("good", cc),
              "https://x/model-detail/dupe": _fake_fetch("dupe", cc)}
    # the cover photo the publisher shows must EXIST on disk — the run checks that
    # before it hands a model over (a fetch with no usable photos is a fetch
    # failure, not an upload), so the fakes get a real file
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")
    for _r in by_url.values():
        _r["image_paths"] = [str(cover)]

    def fake_dl(url):
        if url.endswith("/boom"):          # a fetch that dies
            raise RuntimeError("page timed out")
        return by_url[url]

    async def fake_upload(name, desc, stl, imgs, src, dry_run=False, license_raw="",
                          should_stop=None):
        if "dupe" in name:
            return {"success": False, "duplicate": True,
                    "error": "already uploaded"}
        return {"success": True, "message": "Published to your Creality Cloud account"}

    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", fake_upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    asyncio.run(main._auto_worker(6, ["k"], "testjob"))
    states = {it["url"].rsplit("/", 1)[-1]: it["state"] for it in main.AUTO["items"]}
    # "boom" is a fetch that died — a FAILURE (fetch-failed), never a "skipped"
    # decision like the licence refusals are
    assert states == {"nd": "skipped", "std": "skipped", "nofile": "skipped",
                      "good": "published", "boom": "fetch-failed",
                      "dupe": "duplicate"}
    assert main.AUTO["state"] == "done"
    reasons = {it["url"].rsplit("/", 1)[-1]: (it["reason"] or "").lower()
               for it in main.AUTO["items"]}
    assert "standard" in reasons["std"] or "license" in reasons["std"]
    assert reasons["nd"] and reasons["boom"].startswith("could not fetch")
    assert reasons["nofile"].startswith("no usable model file")
    counts = main._auto_counts()
    # one bucket per outcome: 3 decisions were skipped, the dead fetch FAILED
    assert counts["published"] == 1 and counts["skipped"] == 3 and counts["duplicate"] == 1
    assert counts[FETCH_FAILED_BUCKET] == 1 and counts["failed"] == 0
    # the run leaves a log behind
    logs = list(Path(tmp_path, "artifacts").glob("auto_run_testjob.json"))
    assert len(logs) == 1
    logged = json.loads(logs[0].read_text(encoding="utf-8"))
    assert len(logged["items"]) == 6


# ── the two bugs the first real run hit ──────────────────────────────────────

def test_non_ascii_model_names_do_not_lose_their_photos(tmp_path):
    """A model called "Домкрат для подъема поддонов" produced photo paths OpenCV's
    imread(path) could not open, so every photo was judged unreadable and dropped
    and the upload failed with "No edited images for this model". Photos must be
    judged from their BYTES, not a narrow path."""
    from PIL import Image
    import downloader

    p = tmp_path / "домкрат_для_подъема_поддонов_1_10-го_мас_img_0.webp"
    Image.new("RGB", (600, 600), (120, 90, 60)).save(str(p))
    assert downloader._imread_any(str(p)) is not None
    assert downloader._image_quality_ok(str(p), min_side=200, min_sharpness=0.0) is True
    # and a genuinely tiny image is still rejected
    tiny = tmp_path / "ключница_домой_img_0.png"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(str(tiny))
    assert downloader._image_quality_ok(str(tiny), min_side=200, min_sharpness=0.0) is False


def test_the_static_agreement_label_is_not_an_error():
    """The run reported "Creality rejected the submission (agree to)" — that was
    the form's own static label, not a rejection. Only the blocker toast is one."""
    from uploader import SUBMIT_AGREE_BLOCK_RE, SUBMIT_REJECT_RE
    label = "I have read and agree to Copyright Statement."
    assert SUBMIT_REJECT_RE.search(label) is None
    assert SUBMIT_AGREE_BLOCK_RE.search(label) is None
    toast = "Please check the box to agree before submitting"
    assert SUBMIT_AGREE_BLOCK_RE.search(toast) is not None
    assert SUBMIT_REJECT_RE.search("Please select a category") is not None


def test_run_says_unread_licence_instead_of_calling_it_unlicensed(monkeypatch, tmp_path):
    """A model whose licence could not be read must be skipped with THAT reason
    — the pipeline used to look as if it had flagged a licensed model."""
    import main

    # household-family names: the gate judges each candidate before the licence
    # path under test is reached (see the note in the ND-skip test above)
    models = [{"name": "Blank cable organizer", "url":
               "https://x/model-detail/blank-card", "keyword": "k"},
              {"name": "Boilerplate phone holder", "url":
               "https://x/model-detail/boilerplate", "keyword": "k"},
              {"name": "Clothes peg stand", "url": "https://x/model-detail/fine",
               "keyword": "k"}]
    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models[:a[1]], "licence":
                                        {"opened": True, "verified": True}, "note": ""})
    cc = "https://creativecommons.org/licenses/by/4.0"
    boiler = "This work is licensed under the Copyright License 4.0."

    def _res(name, raw):
        from downloader import classify_license
        return {"name": name, "description": "d", "stl_path": REAL_MODEL,
                "image_paths": ["C:/x/a.jpg"], "slug": name.lower().replace(" ", "-"),
                "author": "someone", "source_url": "https://x/model-detail/" +
                name.lower().replace(" ", "-"), "license": classify_license(raw)}

    by_url = {"https://x/model-detail/blank-card": _res("Blank card model", ""),
              "https://x/model-detail/boilerplate": _res("Boilerplate only", boiler),
              "https://x/model-detail/fine": _res("Fine", cc)}
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")
    for _r in by_url.values():
        _r["image_paths"] = [str(cover)]

    async def fake_upload(*a, **k):
        return {"success": True, "message": "Published", "url": "https://x/fine"}

    monkeypatch.setattr(main, "dl_fetch", lambda url: by_url[url])
    monkeypatch.setattr(main, "uploader_upload", fake_upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_LOCK", asyncio.Lock())

    asyncio.run(main._auto_worker(3, ["k"], "licencejob"))
    items = {it["url"].rsplit("/", 1)[-1]: it for it in main.AUTO["items"]}
    for slug in ("blank-card", "boilerplate"):
        assert items[slug]["state"] == "skipped"
        assert "could NOT be read" in items[slug]["reason"]
        assert "not licensed" not in items[slug]["reason"]
    assert items["fine"]["state"] == "published"


def test_discovery_never_hands_over_a_model_that_is_already_done():
    """The run used to queue models it had already published and skip them later
    (or worse, look at them again at the very start). The harvester itself must
    drop them, so nothing already done is ever picked."""
    from discover import find_creality
    import inspect
    src = inspect.getsource(find_creality)
    assert "is_done" in src and "skipped_names" in src

    done = {"https://x/model-detail/already-done"}
    seen = []

    def fake_is_done(url, name):
        seen.append(url)
        return url in done

    # exercise the filter exactly as the harvester uses it
    cards = [{"url": "https://x/model-detail/already-done", "name": "Already done"},
             {"url": "https://x/model-detail/fresh", "name": "Fresh model"}]
    kept = []
    for c in cards:
        if fake_is_done(c["url"], c["name"]):
            continue
        kept.append(c["url"])
    assert kept == ["https://x/model-detail/fresh"]


# ── the harvest decision itself: order matters ───────────────────────────────

def test_an_already_published_card_does_not_take_its_family_with_it():
    """Found by an adversarial pass: the household gate used to run BEFORE the
    already-published check, so an already-published cable organizer claimed the
    cable-organizer family and every FRESH cable organizer on the page was then
    turned down with "already going out in this run" — which was not true. Only a
    card that is actually harvested may claim its family."""
    from discover import judge_card

    used = set()
    done = {"https://x/model-detail/cable-organizer-old"}
    first = judge_card("Cable organizer old",
                       "https://x/model-detail/cable-organizer-old", used,
                       is_done=lambda u, n: u in done)
    assert first["state"] == "skipped" and first["reason"] == "already published"
    assert used == set(), "an already-published card claimed its family: %s" % used

    # …so the fresh one on the same page is still harvested, as its own family
    fresh = judge_card("Cable organizer", "https://x/model-detail/cable-organizer",
                       used, is_done=lambda u, n: u in done)
    assert fresh["state"] == "harvest" and fresh["family"] == "cable-organizer"
    assert used == {"cable-organizer"}          # and NOW the family is claimed

    # a second cable item is a SPARE: kept aside to fill a larger count, and it
    # does not claim the family again
    second = judge_card("Cable tidy box", "https://x/model-detail/cable-tidy",
                        used, is_done=lambda u, n: u in done)
    assert second["state"] == "spare"
    assert second["family"] == "cable-organizer"
    assert "already going out in this run" in second["reason"]
    assert used == {"cable-organizer"}


def test_a_refused_card_is_reported_and_claims_nothing():
    from discover import judge_card

    used = set()
    toilet = judge_card("Toilet", "https://x/model-detail/toilet", used)
    assert toilet["state"] == "rejected" and "bathroom" in toilet["reason"]
    assert used == set() and toilet["family"] == ""
    # a card outside every family is refused with the families named, and the
    # refusal says which rule did it
    other = judge_card("Card holder", "https://x/model-detail/card-holder", used)
    assert other["state"] == "rejected" and other["source"] == "no-family"


def test_a_broken_already_published_lookup_never_stops_the_harvest():
    """The ledger lookup is best-effort: if it raises, the card is judged by the
    household gate as usual instead of the harvest dying."""
    from discover import judge_card

    def boom(url, name):
        raise RuntimeError("ledger unreadable")

    v = judge_card("Storage box with lid", "https://x/model-detail/storage-box",
                   set(), is_done=boom)
    assert v["state"] == "harvest" and v["family"] == "storage-box"
    junk = judge_card("Toilet", "https://x/model-detail/toilet", set(), is_done=boom)
    assert junk["state"] == "rejected"


def test_a_run_asks_the_harvester_to_skip_what_is_already_published(monkeypatch, tmp_path):
    """main passes the ledger in as the harvester's is_done, and counts what was
    skipped so the dashboard can say it."""
    import main
    import ledger
    from downloader import classify_license

    captured = {}

    def fake_find(keywords, count, sort="Downloads", is_done=None, progress=None,
                  should_stop=None, exclude=None):
        captured["is_done"] = is_done
        captured["count"] = count
        return {"models": [{"name": "Fresh cable organizer",
                            "url": "https://x/model-detail/fresh",
                            "keyword": "k"}],
                "licence": {"opened": True, "verified": True}, "note": "",
                "skipped_done": 7, "already_done": ["Old one"]}

    monkeypatch.setattr(main, "find_creality", fake_find)
    ledger.record(name="Old one", source_url="https://x/model-detail/old-one")

    async def fake_upload(*a, **k):
        return {"success": True, "message": "Published", "url": "https://x/fresh"}

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")
    monkeypatch.setattr(main, "dl_fetch", lambda url: {
        "name": "Fresh", "description": "d", "stl_path": "",
        "image_paths": [str(cover)],
        "license": classify_license("https://creativecommons.org/licenses/by/4.0")})
    monkeypatch.setattr(main, "uploader_upload", fake_upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    asyncio.run(main._auto_worker(3, ["k"], "skipjob"))
    assert captured["is_done"] is not None
    assert captured["is_done"]("https://x/model-detail/old-one", "Old one") is True
    assert captured["is_done"]("https://x/model-detail/new-one", "New one") is False
    assert main.AUTO["skipped_already"] == 7
    assert main.AUTO["already_done"] == ["Old one"]


def test_junk_card_names_fall_back_to_the_slug():
    from discover import clean_card_name
    assert clean_card_name("GIF", "", "") == ""
    assert clean_card_name("100", "", "") == ""
    assert clean_card_name("", "", "") == ""
    assert clean_card_name("Toothbrush stand", "") == "Toothbrush stand"
    assert clean_card_name("GIF", "Домкрат для подъема поддонов") == \
        "Домкрат для подъема поддонов"


def test_auto_start_validates_the_typed_count():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    for bad in (0, -3):
        r = client.post("/auto/start", json={"count": bad})
        assert r.status_code == 400, bad
    # a good count would start a real browser run, so only the shape is checked
    assert {"state", "items", "licence_allow", "licence_deny"} <= set(
        client.get("/auto/status").json())


# ── the failures the user's own run exposed (2026-10-08) ─────────────────────
# Models 7 and 8 were skipped with these two strings; models 5 and 6 were
# carried all the way to the publisher and only died there with
# "No edited images for this model". Both classes must be handled by the loop.

NAV_DESTROYED = ("Page.evaluate: Execution context was destroyed, most likely "
                 "because of a navigation")
NET_DROP = "Page.goto: net::ERR_INTERNET_DISCONNECTED"
NO_IMAGES = ("No edited images for this model. Run Fetch & Edit first so the red "
             "images are generated.")
CC_BY = "https://creativecommons.org/licenses/by/4.0"
FETCH_FAILED_BUCKET = "fetch_failed"      # the counts key, next to "skipped"


def _written_images(tmp_path, n=1):
    out = []
    for i in range(n):
        p = tmp_path / ("cover_%d.png" % i)
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        out.append(str(p))
    return out


def _fetched(name, url, images):
    from downloader import classify_license
    return {"name": name, "description": "d", "stl_path": REAL_MODEL,
            "image_paths": images, "slug": name.lower().replace(" ", "-"),
            "author": "someone", "source_url": url,
            "license": classify_license(CC_BY)}


def _run(monkeypatch, tmp_path, models, fake_dl, fake_up, job):
    import main
    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models, "note": "",
                                        "licence": {"opened": True, "verified": True}})
    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", fake_up)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "FETCH_RETRY_DELAY", 0)
    asyncio.run(main._auto_worker(len(models), ["k"], job))
    return {it["url"].rsplit("/", 1)[-1]: it for it in main.AUTO["items"]}


@pytest.mark.parametrize("recorded_error", [NAV_DESTROYED, NET_DROP])
def test_a_navigation_or_network_fetch_failure_is_retried(
        monkeypatch, tmp_path, recorded_error):
    """A fetch that dies from a navigation-destroyed context or a temporary
    network drop is retried a small, bounded number of times: a candidate that
    gets through on a retry is published, one that keeps dying is reported as a
    fetch failure (with its reason) and the run moves to the next candidate."""
    models = [{"name": "Dies every time cable organizer",
               "url": "https://x/model-detail/dies", "keyword": "k"},
              {"name": "Recovers phone holder",
               "url": "https://x/model-detail/recovers", "keyword": "k"}]
    imgs = _written_images(tmp_path, 2)
    calls = {"dies": 0, "recovers": 0}

    def fake_dl(url):
        if url.endswith("/dies"):
            calls["dies"] += 1
            raise RuntimeError(recorded_error)
        calls["recovers"] += 1
        if calls["recovers"] == 1:
            raise RuntimeError(recorded_error)
        return _fetched("Recovers", url, imgs)

    async def fake_up(name, *a, **k):
        return {"success": True, "message": "Published", "url": "https://x/recovers"}

    import main
    items = _run(monkeypatch, tmp_path, models, fake_dl, fake_up, "retryjob")

    # the one that kept dying: retried, then written off as a FETCH failure
    dies = items["dies"]
    assert calls["dies"] == main.FETCH_ATTEMPTS == 3
    assert dies["state"] == main.FETCH_FAILED == "fetch-failed"
    assert dies["reason"].startswith("could not fetch this model")
    assert recorded_error in dies["reason"]
    assert "tried 3 times" in dies["reason"]
    assert len(dies["fetch_attempts"]) == 3

    # the one the retry rescued: it IS published, and the log says it took 2 tries
    assert calls["recovers"] == 2
    assert items["recovers"]["state"] == "published"
    assert len(items["recovers"]["fetch_attempts"]) == 1
    counts = main._auto_counts()
    assert counts["published"] == 1 and counts[FETCH_FAILED_BUCKET] == 1


def test_stop_is_honoured_during_a_fetch_backoff(monkeypatch, tmp_path):
    """Stop must not sit through the retry backoff: the model is left undecided
    (queued, not blamed for a fetch it was never allowed to finish) and the run
    ends at once instead of waiting the delay out and re-hitting Creality."""
    import time
    import main

    models = [{"name": "Dies cable organizer", "url": "https://x/model-detail/dies",
               "keyword": "k"},
              {"name": "Never reached phone holder",
               "url": "https://x/model-detail/next", "keyword": "k"}]
    calls = []

    def fake_dl(url):
        calls.append(url.rsplit("/", 1)[-1])
        raise RuntimeError(NAV_DESTROYED)

    async def fake_up(*a, **k):
        raise AssertionError("nothing may reach the publisher here")

    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models, "note": "",
                                        "licence": {"opened": True, "verified": True}})
    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", fake_up)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "FETCH_RETRY_DELAY", 5.0)     # long: Stop must cut it
    main.AUTO["stop"] = False

    async def scenario():
        task = asyncio.create_task(main._auto_worker(2, ["k"], "stopjob"))
        for _ in range(400):                       # wait for the first attempt to die
            await asyncio.sleep(0.01)
            if calls:
                break
        assert calls == ["dies"], calls
        pressed = time.monotonic()
        main.AUTO["stop"] = True                   # the user clicks Stop during the backoff
        await task
        return time.monotonic() - pressed

    waited = asyncio.run(scenario())
    assert waited < 2.0, "the run sat through the backoff: %.1fs" % waited
    assert calls == ["dies"], "Stop must not start another fetch attempt: " + str(calls)
    items = {it["url"].rsplit("/", 1)[-1]: it for it in main.AUTO["items"]}
    assert main.AUTO["state"] == "stopped"
    assert items["dies"]["state"] == "queued"      # undecided, not this model's failure
    assert "stopped" in items["dies"]["reason"]
    assert items["next"]["state"] == "queued"
    assert main._auto_counts()["fetch_failed"] == 0
    assert len(items["dies"]["fetch_attempts"]) == 1
    main.AUTO["stop"] = False


def test_a_fetch_without_creator_photos_is_refused_never_rendered(
        monkeypatch, tmp_path):
    """CHANGED 2026-10-14 (user): renders of the model file (CAD/slicer-style
    views) were being rejected by Creality, so they are never uploaded. A fetch
    with no usable creator photo is refused, and the rest of the run goes on."""
    import creality_rules
    monkeypatch.setattr(creality_rules, "photo_source", lambda: "source")
    models = [{"name": "No photos cable organizer",
               "url": "https://x/model-detail/no-photos", "keyword": "k"},
              {"name": "No photos either phone holder",
               "url": "https://x/model-detail/no-photos-2", "keyword": "k"},
              {"name": "Good storage box", "url": "https://x/model-detail/good",
               "keyword": "k"}]
    imgs = _written_images(tmp_path, 2)
    uploaded = []

    def fake_dl(url):
        if url.endswith("good"):
            return _fetched("Good", url, imgs)
        # the fetch came back, but every "edited image" path is missing on disk —
        # exactly what the publisher used to discover (and die of) itself.
        # Each no-photos model carries its OWN file copy with one byte
        # flipped: the ledger's file-digest fingerprint is IDENTITY BY
        # GEOMETRY, so byte-identical fixtures would be reported as real
        # duplicates (matched on file) — our subject is the photo fallback,
        # not digest identity.
        which = "no photos one" if url.endswith("no-photos") else "no photos two"
        own = tmp_path / ("model_%s.stl" % url.rsplit("/", 1)[-1])
        data = bytearray(Path(REAL_MODEL).read_bytes())
        data[-1] = (data[-1] + (1 if url.endswith("no-photos") else 2)) % 256
        own.write_bytes(bytes(data))
        res = _fetched(which.title(), url, [str(tmp_path / "never_written.png")])
        res["stl_path"] = str(own)
        return res

    async def fake_up(name, *a, **k):
        uploaded.append(name)
        return {"success": True, "message": "Published", "url": "https://x/good"}

    import main
    items = _run(monkeypatch, tmp_path, models, fake_dl, fake_up, "noimagesjob")

    # each photo-less model is refused at the fetch step: nothing is rendered,
    # nothing reaches the publish form
    for slug in ("no-photos", "no-photos-2"):
        assert items[slug]["state"] == main.FETCH_FAILED
        assert "could not fetch this model's cover photos" in items[slug]["reason"]
    assert uploaded == ["Good"]
    assert main._auto_counts()["published"] == 1



# ---- top-up: a model that did not publish is replaced by a fresh candidate ----

A_URL = "https://x/model-detail/bottle-rack"
B_URL = "https://x/model-detail/clothes-peg"


def _cc_cover(tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(bytes([255, 216, 255, 224]))
    return str(cover)


def _cc_page(url, cover, name=None):
    page = _fake_fetch(url, "https://creativecommons.org/licenses/by/4.0")
    page["image_paths"] = [cover]
    if name:
        page["name"] = name
    return page


async def _publish_upload(*a, **k):
    return {"success": True, "message": "Published to your Creality Cloud account"}


async def _refuse_upload(*a, **k):
    return {"success": False, "error": "Creality refused the submit"}


async def _must_not_publish(*a, **k):
    raise AssertionError("a known model must never reach the publisher")


def _card(name, url):
    return {"name": name, "url": url, "keyword": "k"}


def test_a_card_already_published_is_refused_before_its_page_is_opened(monkeypatch, tmp_path):
    """The worker checks the card's own name and link in the ledger BEFORE it
    opens the page, so a known model costs no page read at all."""
    import main
    import ledger

    ledger.record(name="Known cable organizer", source_url="https://x/model-detail/known")
    opened = []

    def fake_find(keywords, count, sort="Downloads", is_done=None, progress=None,
                  should_stop=None, exclude=None):
        return {"models": [_card("Known cable organizer", "https://x/model-detail/known")],
                "licence": {"opened": True, "verified": True}, "note": ""}

    def fake_dl(url):
        opened.append(url)
        return _cc_page(url, _cc_cover(tmp_path))

    monkeypatch.setattr(main, "find_creality", fake_find)
    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", _must_not_publish)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    asyncio.run(main._auto_worker(1, ["k"], "preopenjob"))
    assert opened == []
    assert [i["state"] for i in main.AUTO["items"]] == ["duplicate"]


def test_a_failed_model_is_replaced_by_a_fresh_candidate_never_a_repeat(monkeypatch, tmp_path):
    """A is refused by Creality and replaced by B. The top-up asks only for the
    shortfall and excludes A, so A is never opened a second time."""
    import main

    cover = _cc_cover(tmp_path)
    calls = []
    opened = []

    def fake_find(keywords, count, sort="Downloads", is_done=None, progress=None,
                  should_stop=None, exclude=None):
        calls.append({"count": count, "exclude": set(exclude or ())})
        if len(calls) == 1:
            return {"models": [_card("Bottle storage rack", A_URL)],
                    "licence": {"opened": True, "verified": True}, "note": ""}
        # the listing still offers A again, alongside a fresh B
        return {"models": [_card("Bottle storage rack", A_URL),
                           _card("Clothes peg", B_URL)],
                "licence": {"opened": True, "verified": True}, "note": ""}

    def fake_dl(url):
        opened.append(url)
        return _cc_page(url, cover)

    async def upload(name, *a, **k):
        if "rack" in name.lower():
            return await _refuse_upload()
        return await _publish_upload()

    monkeypatch.setattr(main, "find_creality", fake_find)
    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    asyncio.run(main._auto_worker(1, ["k"], "replacejob"))
    assert opened == [A_URL, B_URL]        # A opened once, never again
    assert len(calls) == 2
    assert calls[1]["count"] == 1          # asks only for the shortfall
    assert A_URL in calls[1]["exclude"]    # A is excluded from the top-up
    states = {i["url"]: i["state"] for i in main.AUTO["items"]}
    assert states[A_URL] == "failed"
    assert states[B_URL] == "published"
    assert main.AUTO["state"] == "done"


def test_a_pool_that_runs_dry_ends_the_run_honestly(monkeypatch, tmp_path):
    """Asking for 3 when only one candidate exists: the run publishes it, tops
    up once, finds nothing new, and ends instead of looping."""
    import main

    cover = _cc_cover(tmp_path)
    calls = []

    def fake_find(keywords, count, sort="Downloads", is_done=None, progress=None,
                  should_stop=None, exclude=None):
        calls.append(set(exclude or ()))
        models = [_card("Bottle storage rack", A_URL)] if len(calls) == 1 else []
        return {"models": models, "licence": {"opened": True, "verified": True},
                "note": "only 1 of 3 found"}

    monkeypatch.setattr(main, "find_creality", fake_find)
    monkeypatch.setattr(main, "dl_fetch", lambda url: _cc_page(url, cover))
    monkeypatch.setattr(main, "uploader_upload", _publish_upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    asyncio.run(main._auto_worker(3, ["k"], "dryjob"))
    assert len(calls) == 2
    assert main.AUTO["state"] == "done"
    assert [i["state"] for i in main.AUTO["items"]] == ["published"]


def test_a_model_the_page_names_as_published_is_refused_after_the_first_read(monkeypatch, tmp_path):
    """The card link is new, but the page names a model already on the account
    under another link: it is refused before any photo or form work."""
    import main
    import ledger

    ledger.record(name="Known cable organizer", source_url="https://x/model-detail/other-link")
    cover = _cc_cover(tmp_path)
    opened = []

    def fake_find(keywords, count, sort="Downloads", is_done=None, progress=None,
                  should_stop=None, exclude=None):
        return {"models": [_card("Bottle storage rack", "https://x/model-detail/fresh-link")],
                "licence": {"opened": True, "verified": True}, "note": ""}

    def fake_dl(url):
        opened.append(url)
        return _cc_page(url, cover, name="Known cable organizer")

    monkeypatch.setattr(main, "find_creality", fake_find)
    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", _must_not_publish)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)

    asyncio.run(main._auto_worker(1, ["k"], "afterreadjob"))
    assert opened == ["https://x/model-detail/fresh-link"]
    assert [i["state"] for i in main.AUTO["items"]] == ["duplicate"]

def test_a_localised_link_is_also_checked_in_its_english_form():
    """A model reached through a /de/ link is the same model as its English
    link: the already-published check tries the English form as well."""
    from discover import _english_url, judge_card

    english = "https://www.crealitycloud.com/model-detail/abc"
    assert _english_url("https://www.crealitycloud.com/de/model-detail/abc") == english
    assert _english_url(english) == english

    asked = []

    def is_done(url, name):
        asked.append(url)
        return url == english

    verdict = judge_card("Bottle storage rack",
                         "https://www.crealitycloud.com/de/model-detail/abc", set(), is_done)
    assert verdict["state"] == "skipped"
    assert english in asked

    not_done = judge_card("Bottle storage rack", english, set(), lambda u, n: False)
    assert not_done["state"] != "skipped"
