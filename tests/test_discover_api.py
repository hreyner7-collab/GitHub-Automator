"""The API discovery path: every card's licence decides, and Stop costs nothing.

No browser: `creality_api.get_reader` is replaced with a tab-shaped stub, so the
real `_find_creality_api` loop, the real `classify_license` and the real
`judge_card` seam all run. The two facts pinned here are the ones the plan says
must not regress when the search API replaces the filter panel:

- a card that forbids re-publishing, or names no licence at all, is refused
  BEFORE it can be harvested (the API has no licence filter parameter);
- Stop pressed before the scan opens no tab and asks the site nothing.
"""
import types

import browser_opener
import creality_api as api
import discover
import uploader


def _card(n, licence, alias=None):
    return {"groupName": "Model %d" % n, "urlAlias": alias or "model-%d" % n,
            "license": licence, "id": "%024x" % n, "shareId": str(n),
            "totalFileSize": "1000",
            "userInfo": {"nickName": "maker%d" % n},
            "covers": [{"url": "https://pic2-cdn.creality.com/p/%d.png" % n}]}


class StubReader:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.page = types.SimpleNamespace(goto=lambda *a, **k: None)

    def url(self):
        return "https://www.crealitycloud.com/"

    def search(self, keyword, page, page_size, sort_type):
        self.calls.append((keyword, page))
        return self.pages.get(page)


def _run(monkeypatch, reader, count=1, should_stop=None, progress=None):
    class FakePW:
        class chromium:
            @staticmethod
            def connect_over_cdp(_url):
                return types.SimpleNamespace(contexts=[types.SimpleNamespace()])

    class FakePWCM:
        def __enter__(self):
            return FakePW()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(browser_opener, "sync_playwright_auto", lambda: FakePWCM())
    monkeypatch.setattr(uploader, "ensure_browser", lambda: True)
    monkeypatch.setattr(api, "get_reader", lambda ctx=None: reader)
    monkeypatch.setattr(discover, "judge_card",
                        lambda name, url, used, is_done=None:
                        {"state": "harvest", "reason": "", "family": "f-" + name})
    return discover._find_creality_api(["cable clips"], count, "Downloads", None,
                                       progress, should_stop, None, None, 30)


def test_a_restricted_card_is_refused_and_an_allowed_one_harvested(monkeypatch):
    # CXY-SL is Creality's Standard License under the API's own code: the judge
    # cannot name it, so it is UNDECIDED and refused (live first card of a
    # downloads-sorted search). A named NC-ND card is a decisive refusal.
    reader = StubReader({1: {"result": {"list": [
        _card(1, "CXY-SL"), _card(2, "CC BY-NC-ND"), _card(3, "CC BY"),
        _card(4, "")]}}})
    out = _run(monkeypatch, reader)

    kept = [m["url"].rsplit("/", 1)[-1] for m in out["models"]]
    assert kept == ["model-3"], kept
    lic = out["licence"]
    assert lic["mode"] == "api:per-card"
    assert lic["checked"] == 4
    assert lic["refused"] == 1        # CC BY-NC-ND
    assert lic["undecided"] == 2      # CXY-SL and the card naming no licence
    refused = {r["url"].rsplit("/", 1)[-1]: r["source"] for r in out["rejected"]}
    assert refused == {"model-1": "licence", "model-2": "licence",
                       "model-4": "licence"}, refused


def test_the_search_asks_for_the_next_page_when_the_pool_is_short(monkeypatch):
    # a full 20-card page is the API's way of saying "there is more": a short
    # page ends the scan, so each page here holds 20 cards and most are refused
    page1 = [_card(100 + n, "CXY-SL") for n in range(19)] + [_card(1, "CC BY")]
    page2 = [_card(200 + n, "CXY-SL") for n in range(19)] + [_card(2, "CC0")]
    reader = StubReader({1: {"result": {"list": page1}},
                         2: {"result": {"list": page2}},
                         3: None})
    seen = []
    out = _run(monkeypatch, reader, count=4,
               progress=lambda p: seen.append((p["pass"], p["mode"])))
    # count 4 + its surplus (max(3, 4//2) = 3) = 7 rows wanted; two pages yield 2
    assert [p for _k, p in reader.calls] == [1, 2, 3], reader.calls
    assert out["pages"] == 3
    assert len(out["models"]) == 2
    assert out["note"], "a short pool must say so"
    assert seen and all(mode == "api" for _p, mode in seen)


def test_stop_pressed_before_the_scan_opens_no_tab(monkeypatch):
    reader = StubReader({})
    opened = {"ensure": 0}

    class FakePWCM:
        def __enter__(self):
            return types.SimpleNamespace()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(browser_opener, "sync_playwright_auto", lambda: FakePWCM())
    monkeypatch.setattr(uploader, "ensure_browser",
                        lambda: opened.__setitem__("ensure", opened["ensure"] + 1))
    monkeypatch.setattr(api, "get_reader", lambda ctx=None: reader)

    out = discover._find_creality_api(["cable clips"], 1, "Downloads", None, None,
                                      lambda: True, None, None, 30)
    assert out["stopped"] is True
    assert out["models"] == []
    assert reader.calls == [] and opened["ensure"] == 0


def test_no_keywords_leaves_the_unfiltered_listing_to_the_dom_path(monkeypatch):
    # measured live 2026-10-24: smart_search answers count 0 for keyword ""
    reader = StubReader({1: {"result": {"list": [_card(1, "CC BY")]}}})
    monkeypatch.setattr(api, "get_reader", lambda ctx=None: reader)
    assert discover._find_creality_api(None, 5, "Downloads", None, None,
                                       None, None, None, 30) is None
    assert discover._find_creality_api([], 5, "Downloads", None, None,
                                       None, None, None, 30) is None
    assert reader.calls == []


def test_a_crashing_api_path_is_named_in_the_dom_record(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("CDP handshake timed out")

    monkeypatch.setattr(discover, "_find_creality_api", boom)
    monkeypatch.setattr(discover, "_find_creality_dom",
                        lambda *a, **k: {"models": [], "note": ""})
    out = discover.find_creality(None, 1, "Downloads")
    assert out["read_via"] == "dom"
    assert "CDP handshake timed out" in out["api_error"]
