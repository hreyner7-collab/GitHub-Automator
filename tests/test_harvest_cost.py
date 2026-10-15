"""What a harvest pass COSTS, measured rather than asserted in prose.

`find_creality` walks a lazy-loading listing and used to sleep a FIXED 2500ms per
pass before counting the cards -- up to 200 passes per keyword. Nothing recorded
that cost, so "it is faster now" could not be checked by anyone.

This drives the real function with a fake page that records every wait it is
asked for and every read it is made to do, so the trade is a number with a test
behind it. It is a cost model at the browser-call level, not a live wall-clock
benchmark; the live numbers come from the run log's `timings` table.
"""
import json
from contextlib import contextmanager

import browser_opener
import discover
import uploader

# The two costs that are NOT the thing being tuned, kept here so the assertions
# can be written against the OLD model explicitly:
FIXED_LOAD_MS = 6000     # the one post-goto wait per keyword (unchanged)
OLD_PASS_MS = 2500       # what every harvest pass used to sleep, unconditionally
OLD_READS_PER_PASS = 2   # the cards read + the growth read


class MeteredPage:
    """A listing that starts with a few cards and loads more as it is scrolled."""

    def __init__(self, initial=2, per_scroll=8, grow=True, respond_after_ms=100):
        self.shown = initial
        self.per_scroll = per_scroll
        self.grow = grow
        self.respond_after_ms = respond_after_ms
        self.evaluations = 0
        self.waited_ms = 0
        self.clock = 0
        self._pending_at = None

    # ── the page interface find_creality uses ────────────────────────────────
    def set_viewport_size(self, _size):
        pass

    def goto(self, *_a, **_k):
        pass

    def wait_for_timeout(self, ms):
        self.waited_ms += ms
        self.clock += ms
        if self._pending_at is not None and self.clock >= self._pending_at:
            self.shown += self.per_scroll
            self._pending_at = None

    def evaluate(self, js, *_a):
        if js == discover.CREALITY_CARDS_JS:
            self.evaluations += 1
            return json.dumps([{"url": "https://www.crealitycloud.com/model-detail/m-%d" % i,
                                "name": "Model %d" % i, "alt": None, "title": None}
                               for i in range(self.shown)])
        if "scrollTo" in js and self.grow:
            self._pending_at = self.clock + self.respond_after_ms
        return None

    def close(self):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [self]
        self._page = page

    def new_page(self):
        return self._page


def _run(monkeypatch, page, count=1):
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
    monkeypatch.setattr(discover, "judge_card",
                        lambda name, url, used, is_done=None:
                        {"state": "harvest", "reason": "", "family": "f-" + name})
    return discover.find_creality(None, count, "Downloads", None)


def test_the_early_probe_catches_a_batch_that_lands_quickly(monkeypatch):
    """The saving: a listing that grows in ~100ms is no longer charged 2500ms.

    Old behaviour: every pass cost a flat 2500ms, because the sleep came FIRST
    and the count second. Measured here as the total wait the page was asked for.
    """
    page = MeteredPage(initial=2, per_scroll=8, respond_after_ms=100)
    result = _run(monkeypatch, page, count=3)
    assert len(result["models"]) == 3
    # one scroll+wait grew the listing; the third model was then on the page
    assert page.shown == 10
    scroll_wait = page.waited_ms - FIXED_LOAD_MS
    assert scroll_wait == 150, scroll_wait          # one early probe, not the budget
    assert scroll_wait < OLD_PASS_MS                # 2500ms before
    # 3 reads for 2 passes: one extra probe, where the old code would have made 4
    assert page.evaluations == 3, page.evaluations


def test_a_slow_batch_still_respects_the_old_budget_without_spinning(monkeypatch):
    """Worst case must be the OLD cost, not a busy poll: one 2.5s budget and one
    more read at the end of it."""
    page = MeteredPage(initial=2, per_scroll=8, respond_after_ms=2400)
    result = _run(monkeypatch, page, count=3)
    assert len(result["models"]) == 3
    scroll_wait = page.waited_ms - FIXED_LOAD_MS
    assert scroll_wait <= OLD_PASS_MS, scroll_wait   # never more than the old wait
    assert page.evaluations == 2 * OLD_READS_PER_PASS, page.evaluations


def test_the_end_of_a_listing_does_not_get_more_expensive(monkeypatch):
    """At the end nothing grows, so the early probe cannot help. The TIME there
    must stay bounded to the old per-pass budget, and the extra cost is one read
    per pass, which is the honest price of asking instead of sleeping blind."""
    page = MeteredPage(initial=4, grow=False)
    result = _run(monkeypatch, page, count=9)      # asks for more than exists
    assert len(result["models"]) == 4
    assert "only 4 of the 9" in result["note"]
    passes = 2                                     # two empty passes end the scan
    scroll_wait = page.waited_ms - FIXED_LOAD_MS
    assert scroll_wait <= passes * OLD_PASS_MS, scroll_wait
    assert page.evaluations == 6, page.evaluations  # old: 4 -> +1 read per pass


def test_a_scan_that_finds_everything_up_front_never_scrolls(monkeypatch):
    """No regression for the easy case: the first read can already satisfy the
    request, and then no scroll, wait or extra read happens at all."""
    page = MeteredPage(initial=20, grow=False)
    result = _run(monkeypatch, page, count=2)
    assert len(result["models"]) == 2
    assert page.evaluations == 1
    assert page.waited_ms == FIXED_LOAD_MS          # only the post-load wait, as before
