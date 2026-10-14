"""The listing scan must stop at the END of the listing.

Regression: the end-of-listing check compared the cards on the page with the
number of ACCEPTED models. Rejected and already-published cards are on the page
but never accepted, so the check never saw the end and every listing was
scrolled for the full 200-pass ceiling. This drives find_creality with a fake
browser page, so nothing is opened and no account is touched.
"""
import json
from contextlib import contextmanager

import browser_opener
import discover
import uploader


TOTAL_CARDS = 60
FRESH = {3, 17, 44}          # the only cards that are new, so only 3 can be found


class FakePage:
    def __init__(self):
        self.shown = 0
        self.evaluations = 0

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
            self.shown = min(TOTAL_CARDS, self.shown + 10)
        return None

    def close(self):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [self]
        self._page = page

    def new_page(self):
        return self._page


def test_scan_stops_at_end_of_listing_when_most_cards_are_already_done(monkeypatch):
    page = FakePage()
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
    monkeypatch.setattr(discover, "filter_creality_licences",
                        lambda _p, **_k: {"opened": True, "verified": True,
                                    "ticked_on": [], "ticked_off": []})

    def judge(name, url, used_families, is_done=None):
        if is_done and is_done(url, name):
            return {"state": "skipped", "reason": "already published"}
        return {"state": "harvest", "reason": "", "family": "f-" + name}

    monkeypatch.setattr(discover, "judge_card", judge)

    done = {"https://www.crealitycloud.com/model-detail/m-%d" % i
            for i in range(TOTAL_CARDS) if i not in FRESH}
    passes = []
    result = discover.find_creality(None, 50, "Downloads",
                                    lambda url, name: url in done,
                                    progress=lambda p: passes.append(p["pass"]))

    assert len(result["models"]) == 3
    assert "only 3 of the 50" in result["note"]
    # the scan ended at the end of the 60-card listing, far below the 200 ceiling
    assert max(passes) < 20
    assert page.evaluations < 40
