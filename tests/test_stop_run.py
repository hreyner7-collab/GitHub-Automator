"""Stop must end an auto run at every stage: the listing scan, the discovery
hand-over, a fetch, before a model is published, before Submit, and between
models. Stop is checked before the browser tab opens, before each form step and
before the Submit click; a stop there leaves the model undecided and nothing is
submitted. Only a Submit already pressed is recorded. The browser is faked
(the uploader's own steps run against it where a test needs them), the
listings and fetcher are faked, and the ledger is a tmp file.
"""
import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser_opener  # noqa: E402
import discover  # noqa: E402
import uploader  # noqa: E402

# A REAL model file on disk: the pipeline builds a proof of creation from it.
REAL_MODEL = str(Path(__file__).resolve().parent / "data" / "proof_cube.stl")
CC_BY = "https://creativecommons.org/licenses/by/4.0"
TOTAL_CARDS = 60
FRESH = {3, 17, 44}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    monkeypatch.setenv("CREALITY_PROOF_DIR", str(tmp_path / "proof"))
    import creality_rules
    monkeypatch.setattr(creality_rules, "SETTINGS_FILE",
                        tmp_path / "publish_settings.json")
    monkeypatch.delenv("CREALITY_PHOTO_SOURCE", raising=False)
    import main
    monkeypatch.setitem(main.AUTO, "stop", False)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "FETCH_RETRY_DELAY", 0)
    yield


def _cover(tmp_path):
    p = tmp_path / "cover.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    return str(p)


def _fetched(name, url, images):
    from downloader import classify_license
    return {"name": name, "description": "d", "stl_path": REAL_MODEL,
            "image_paths": images, "slug": name.lower().replace(" ", "-"),
            "author": "someone", "source_url": url,
            "license": classify_license(CC_BY)}


def _models(*names):
    return [{"name": n, "url": "https://x/model-detail/" + n.lower().replace(" ", "-"),
             "keyword": "k"} for n in names]


def _run(monkeypatch, models, fake_dl, fake_up, job, find=None):
    import main
    if find is None:
        find = lambda *a, **k: {"models": models, "note": "",  # noqa: E731
                                "licence": {"opened": True, "verified": True}}
    monkeypatch.setattr(main, "find_creality", find)
    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", fake_up)
    asyncio.run(main._auto_worker(len(models) or 1, ["k"], job))
    return {it["url"].rsplit("/", 1)[-1]: it for it in main.AUTO["items"]}


# ── the listing scan (discover.find_creality) ────────────────────────────────

class FakePage:
    def __init__(self):
        self.shown = 0
        self.evaluations = 0
        self.scrolls = 0

    def set_viewport_size(self, _size):
        pass

    def goto(self, *_a, **_k):
        self.shown = 20

    def wait_for_timeout(self, _ms):
        pass

    def evaluate(self, js, *_a):
        if js == discover.CREALITY_CARDS_JS:
            self.evaluations += 1
            return json.dumps([{"url": "https://www.crealitycloud.com/model-detail/m-%d" % i,
                                "name": "Model %d" % i, "alt": None, "title": None}
                               for i in range(self.shown)])
        if "scrollTo" in js:
            self.scrolls += 1
            self.shown = min(TOTAL_CARDS, self.shown + 10)
        return None

    def close(self):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [self]
        self._page = page
        self.opened = 0

    def new_page(self):
        self.opened += 1
        return self._page


def _scan(monkeypatch, make_stop, filt=None):
    page = FakePage()
    should_stop = make_stop(page)
    browser = FakeBrowser(page)

    class FakePW:
        class chromium:
            @staticmethod
            def connect_over_cdp(_url):
                return browser

    @contextmanager
    def fake_pw():
        yield FakePW()

    monkeypatch.setattr(browser_opener, "sync_playwright_auto", fake_pw)
    monkeypatch.setattr(uploader, "ensure_browser", lambda: None)
    monkeypatch.setattr(discover, "_sort_by_downloads", lambda _p: None)
    monkeypatch.setattr(discover, "filter_creality_licences", filt or (
        lambda _p, **_k: {"opened": True, "verified": True,
                          "ticked_on": [], "ticked_off": []}))
    monkeypatch.setattr(discover, "judge_card",
                        lambda name, url, used, is_done=None: {"state": "harvest",
                                                               "reason": "",
                                                               "family": "f-" + name})
    passes = []
    result = discover.find_creality(None, 50, "Downloads", None,
                                    progress=lambda p: passes.append(p["pass"]),
                                    should_stop=should_stop)
    return result, page, browser, passes


def test_stop_pressed_during_the_listing_scan_ends_the_scan(monkeypatch):
    # Stop is pressed once two scrolls have happened, i.e. during pass 2's
    # wait: pass 3 must never start
    result, page, _browser, passes = _scan(
        monkeypatch, lambda page: lambda: page.scrolls >= 2)
    assert result["stopped"] is True
    assert passes == [1, 2], passes          # no third pass after Stop
    assert page.scrolls == 2
    assert result["note"] == ""              # a stop is not a shortfall


def test_stop_pressed_before_the_scan_opens_no_listing(monkeypatch):
    result, _page, browser, passes = _scan(monkeypatch, lambda page: lambda: True)
    assert result["stopped"] is True
    assert browser.opened == 0
    assert passes == []
    assert result["models"] == []


def test_stop_pressed_during_the_licence_filter_ends_the_scan(monkeypatch):
    # the filter came back unverified because Stop cut it short: the scan must
    # report a stop, not the 'did not read back' refusal, and harvest nothing
    flag = {"on": False}

    def filt(_page, should_stop=None):
        flag["on"] = True
        return {"opened": True, "verified": False,
                "ticked_on": [], "ticked_off": []}

    result, _page, _browser, passes = _scan(
        monkeypatch, lambda page: lambda: flag["on"], filt=filt)
    assert result["stopped"] is True
    assert result["note"] == ""
    assert passes == []
    assert result["models"] == []


class _LicencePage:
    """Just enough page for the licence ticking: every box is on screen and
    nothing is ticked yet, so the filter tries to tick all of them."""

    def __init__(self):
        self.clicks = 0
        self.mouse = self

    def evaluate(self, js, *_a):
        if js == discover.LICENCE_STATE_JS:
            return "{}"
        if js == discover.LICENCE_BOXES_JS:
            return json.dumps({lic: {"x": 10, "y": 100 + 30 * i, "w": 200, "h": 20}
                               for i, lic in enumerate(discover.CREALITY_LICENCE_ALLOW)})
        if "innerHeight" in js:
            return 900
        return None

    def click(self, _x, _y):
        self.clicks += 1

    def wait_for_timeout(self, _ms):
        pass


def test_stop_during_licence_ticking_stops_the_clicks(monkeypatch):
    monkeypatch.setattr(discover, "_open_filters", lambda _p: True)
    page = _LicencePage()
    rep = discover.filter_creality_licences(page, should_stop=lambda: page.clicks >= 1)
    assert page.clicks == 1                  # no further licence is ticked after Stop
    assert rep["verified"] is False


# ── the worker ───────────────────────────────────────────────────────────────

def test_stop_from_the_discovery_hand_over_publishes_nothing(monkeypatch, tmp_path):
    """Stop pressed while the listings were read: the discovery reports stopped,
    the run ends as stopped, and no model is fetched or published."""
    import main
    models = _models("Fresh cable organizer", "Clothes peg stand")

    def find(*a, **k):
        main.AUTO["stop"] = True
        return {"models": models, "note": "", "stopped": True,
                "licence": {"opened": True, "verified": True}}

    def no_fetch(url):
        raise AssertionError("Stop was pressed: nothing may be fetched: " + url)

    async def no_publish(*a, **k):
        raise AssertionError("Stop was pressed: nothing may be published")

    _run(monkeypatch, models, no_fetch, no_publish, "stop-discover", find=find)
    assert main.AUTO["state"] == "stopped"
    assert main._auto_counts()["published"] == 0
    assert all(it["state"] != "fetching" for it in main.AUTO["items"])


def test_stop_during_a_fetch_publishes_nothing_and_leaves_no_item_fetching(
        monkeypatch, tmp_path):
    import main
    models = _models("Fresh cable organizer", "Clothes peg stand")
    cover = _cover(tmp_path)
    fetched = []

    def fake_dl(url):
        fetched.append(url.rsplit("/", 1)[-1])
        main.AUTO["stop"] = True                  # Stop clicked while fetching
        return _fetched("Fresh cable organizer", url, [cover])

    async def no_publish(*a, **k):
        raise AssertionError("a model fetched after Stop must not be published")

    items = _run(monkeypatch, models, fake_dl, no_publish, "stop-fetch")
    assert main.AUTO["state"] == "stopped"
    assert fetched == ["fresh-cable-organizer"]  # the second model never starts
    assert items["fresh-cable-organizer"]["state"] == "queued"
    assert items["clothes-peg-stand"]["state"] == "queued"
    assert all(it["state"] != "fetching" for it in main.AUTO["items"])


def test_stop_after_the_proof_is_built_still_publishes_nothing(monkeypatch, tmp_path):
    """The last check before the publish step: Stop pressed after the proof is
    built, so the model is left undecided rather than submitted."""
    import main
    models = _models("Fresh cable organizer")
    cover = _cover(tmp_path)
    real_proof = main._build_proof

    def proof_then_stop(*a, **k):
        out = real_proof(*a, **k)
        main.AUTO["stop"] = True
        return out

    async def no_publish(*a, **k):
        raise AssertionError("Stop was pressed before publish: nothing may be submitted")

    monkeypatch.setattr(main, "_build_proof", proof_then_stop)
    items = _run(monkeypatch, models,
                 lambda url: _fetched("Fresh cable organizer", url, [cover]),
                 no_publish, "stop-before-publish")
    assert main.AUTO["state"] == "stopped"
    assert items["fresh-cable-organizer"]["state"] == "queued"
    assert "before this model was published" in items["fresh-cable-organizer"]["reason"]


def test_stop_before_submit_leaves_the_model_undecided_and_unrecorded(
        monkeypatch, tmp_path):
    """The uploader stopped before Submit: nothing was submitted, so the model is
    queued (not published, not recorded in the ledger) and the next one never
    starts."""
    import main
    models = _models("Fresh cable organizer", "Clothes peg stand")
    cover = _cover(tmp_path)
    fetched, uploads = [], []

    names = {m["url"]: m["name"] for m in models}

    def fake_dl(url):
        fetched.append(url.rsplit("/", 1)[-1])
        return _fetched(names[url], url, [cover])

    async def fake_up(name, *a, **k):
        uploads.append(name)
        main.AUTO["stop"] = True                  # Stop clicked before Submit
        return uploader._stopped_result()

    items = _run(monkeypatch, models, fake_dl, fake_up, "stop-before-submit")
    assert uploads == ["Fresh cable organizer"]   # Submit never reached
    assert fetched == ["fresh-cable-organizer"]    # the next one never started
    fresh = items["fresh-cable-organizer"]
    assert fresh["state"] == "queued"
    assert "nothing was submitted" in fresh["reason"]
    assert items["clothes-peg-stand"]["state"] == "queued"
    assert main.AUTO["state"] == "stopped"
    assert main._auto_counts()["published"] == 0
    hit = main.ledger.find(source_url=models[0]["url"], name="Fresh cable organizer",
                           author="someone", slug="fresh-cable-organizer",
                           file_path=REAL_MODEL)
    assert hit["found"] is False                  # nothing recorded


def test_stop_pressed_after_submit_went_through_still_records_it(monkeypatch, tmp_path):
    """A submit that went through is recorded as published even when Stop came
    in during it; the next model is never started."""
    import main
    models = _models("Fresh cable organizer", "Clothes peg stand")
    cover = _cover(tmp_path)
    fetched, published = [], []

    names = {m["url"]: m["name"] for m in models}

    def fake_dl(url):
        fetched.append(url.rsplit("/", 1)[-1])
        return _fetched(names[url], url, [cover])

    async def fake_up(name, *a, **k):
        published.append(name)
        main.AUTO["stop"] = True                  # Stop clicked after Submit
        return {"success": True, "message": "Published", "url": "https://x/done"}

    items = _run(monkeypatch, models, fake_dl, fake_up, "stop-after-submit")
    assert published == ["Fresh cable organizer"]
    assert fetched == ["fresh-cable-organizer"]    # the next one never started
    assert items["fresh-cable-organizer"]["state"] == "published"
    assert items["clothes-peg-stand"]["state"] == "queued"
    assert main.AUTO["state"] == "stopped"
    assert main._auto_counts()["published"] == 1


# -- the real uploader against a fake browser ----------------------------

class _FakeCtx:
    def __init__(self):
        self.opened = 0

    async def new_page(self):
        self.opened += 1
        raise AssertionError("Stop was pressed: no tab may be opened")


class _FakeBrowser:
    def __init__(self, ctx):
        self.contexts = [ctx]


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def connect_over_cdp(self, _url):
        return self._browser


class _FakeAsyncPlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _real_upload(monkeypatch, on_attach):
    """Run the real uploader.upload with the browser attach faked. `on_attach`
    runs once the browser is attached (where a Stop would land)."""
    ctx = _FakeCtx()
    browser = _FakeBrowser(ctx)

    def fake_attach():
        on_attach()
        return True

    monkeypatch.setattr(uploader, "_debug_port_up", fake_attach)
    monkeypatch.setattr(uploader, "async_playwright",
                        lambda: _FakeAsyncPlaywright(browser))
    return ctx


def test_real_upload_stopped_before_its_tab_opens_submits_nothing(
        monkeypatch, tmp_path):
    ctx = _real_upload(monkeypatch, lambda: None)
    result = asyncio.run(uploader.upload(
        "Fresh cable organizer", "d", REAL_MODEL, [_cover(tmp_path)],
        "https://x/model-detail/fresh", should_stop=lambda: True))
    assert result["stopped"] is True
    assert result["success"] is False
    assert ctx.opened == 0


def test_stop_while_the_browser_attaches_publishes_nothing_end_to_end(
        monkeypatch, tmp_path):
    """Stop pressed while the browser attaches: the real uploader returns
    stopped before any tab opens, and the worker leaves the model queued."""
    import main
    models = _models("Fresh cable organizer", "Clothes peg stand")
    cover = _cover(tmp_path)
    fetched = []

    def fake_dl(url):
        fetched.append(url.rsplit("/", 1)[-1])
        return _fetched("Fresh cable organizer", url, [cover])

    def stop_now():
        main.AUTO["stop"] = True

    ctx = _real_upload(monkeypatch, stop_now)
    items = _run(monkeypatch, models, fake_dl, uploader.upload,
                 "stop-attach")
    assert ctx.opened == 0
    assert fetched == ["fresh-cable-organizer"]
    assert items["fresh-cable-organizer"]["state"] == "queued"
    assert items["clothes-peg-stand"]["state"] == "queued"
    assert main.AUTO["state"] == "stopped"
    assert main._auto_counts()["published"] == 0


def test_an_unexpected_error_mid_model_marks_it_failed_not_working(monkeypatch, tmp_path):
    import main
    models = _models("Fresh cable organizer")
    cover = _cover(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("proof step broke")

    async def no_publish(*a, **k):
        raise AssertionError("a broken model must not be published")

    monkeypatch.setattr(main, "_build_proof", boom)
    items = _run(monkeypatch, models,
                 lambda url: _fetched("Fresh cable organizer", url, [cover]),
                 no_publish, "error-job")
    assert main.AUTO["state"] == "error"
    assert items["fresh-cable-organizer"]["state"] == "failed"
    assert "run error" in items["fresh-cable-organizer"]["reason"]
    counts = main._auto_counts()
    assert counts["working"] == 0
