"""One listing scan harvests a SURPLUS and stops at its own ceiling.

2026-10-23, run 20261023-181147: a 20-model request published 0. It harvested
exactly 20 candidates, every fetch came back with no model file, and the queue
was therefore empty — so the run scanned the whole listing again (58 of its 82
minutes, 601 scroll passes) and ended with nothing. These tests pin the two
changes that came out of that log, on a fake page: no browser is started and no
account is touched.

- the scan harvests `count + extra` candidates in ONE pass, so the replacements
  a run needs are already queue rows rather than a second full scan;
- the scan stops at `deadline_s` with what it has found, and says that is why.
"""
import json
import time
from contextlib import contextmanager

import browser_opener
import discover
import uploader

BASE = "https://www.crealitycloud.com/model-detail/m-%d"


class SimClock:
    """A clock the scan can be handed instead of the wall-clock: every wait the
    fake listing is asked for moves it, so a deadline test is deterministic."""

    def __init__(self, start=0.0):
        self.now = start

    def advance(self, ms):
        self.now += ms / 1000.0

    def as_time_module(self):
        outer = self

        class _Time:
            def perf_counter(self):
                return outer.now

            def __getattr__(self, name):
                return getattr(time, name)

        return _Time()


class ListingPage:
    """A listing that shows `initial` cards and lazy-loads `per_scroll` more when
    it is scrolled. `respond_after_ms` is how long the next batch takes to land
    (None: it is already there, so the first probe finds it). `clock`, when given,
    is advanced by every wait the page is asked for, so a deadline can be tested
    on simulated seconds instead of a real 2500ms sleep."""

    def __init__(self, initial=200, per_scroll=20, grow=True, clock=None,
                 respond_after_ms=None, total=10_000):
        self.shown = initial
        self.per_scroll = per_scroll
        self.grow = grow
        self.clock = clock
        self.respond_after_ms = respond_after_ms
        self.total = total
        self.scrolls = 0
        self._pending = None

    def set_viewport_size(self, _size):
        pass

    def goto(self, *_a, **_k):
        pass

    def wait_for_timeout(self, ms):
        if self.clock:
            self.clock.advance(ms)
        if self._pending is not None:
            self._pending -= ms
            if self._pending <= 0:
                self._pending = None
                self.shown = min(self.total, self.shown + self.per_scroll)

    def evaluate(self, js, *_a):
        if js == discover.CREALITY_CARDS_JS:
            return json.dumps([{"url": BASE % i, "name": "Model %d" % i,
                                "alt": None, "title": None}
                               for i in range(self.shown)])
        if "scrollTo" in js:
            self.scrolls += 1
            if self.grow:
                if self.respond_after_ms is None:
                    self.shown = min(self.total, self.shown + self.per_scroll)
                else:
                    self._pending = self.respond_after_ms
        return None

    def close(self):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [self]
        self._page = page

    def new_page(self):
        return self._page


def _harness(monkeypatch, page):
    class FakePW:
        class chromium:
            @staticmethod
            def connect_over_cdp(_url):
                return FakeBrowser(page)

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


def _find(monkeypatch, count, page=None, **kw):
    _harness(monkeypatch, page or ListingPage())
    return discover.find_creality(None, count, "Downloads", None, **kw)


def test_a_20_model_scan_harvests_a_surplus_of_replacements(monkeypatch):
    """The queue a run starts with is bigger than the number it asked for."""
    result = _find(monkeypatch, 20)
    # extra defaults to max(3, count // 2) = 10, so 30 candidates come back
    assert len(result["models"]) == 30
    assert result["harvested"] == 30
    assert result["count"] == 20
    assert not result["timed_out"]


def test_explicit_extra_sizes_the_pool(monkeypatch):
    assert len(_find(monkeypatch, 20, extra=5)["models"]) == 25
    # extra=0 is the old exact-count contract, which the cost tests rely on
    assert len(_find(monkeypatch, 20, extra=0)["models"]) == 20


def test_the_surplus_is_capped_by_the_replacements_a_run_actually_needs(monkeypatch):
    """`models` never grows past the target, however many cards the page offers:
    the surplus is bounded work, not the whole listing."""
    result = _find(monkeypatch, 3, extra=2)
    assert [m["url"] for m in result["models"]] == [BASE % i for i in range(5)]


def test_a_scan_that_cannot_fill_its_target_reports_what_the_listing_gave(monkeypatch):
    """The surplus must not turn an honest short listing into a silent one: with
    fewer cards than the target the note still counts against `count`."""
    result = _find(monkeypatch, 20, page=ListingPage(initial=12, grow=False))
    assert len(result["models"]) == 12
    assert "only 12 of the 20" in result["note"]


def test_the_deadline_stops_the_scroll_and_keeps_what_it_found(monkeypatch):
    """The ceiling is measured in simulated page waits, so this is deterministic.

    The listing grows forever, so without a ceiling this scan would run to the
    200-pass limit exactly as the 2026-10-23 run did.
    """
    clock = SimClock()
    monkeypatch.setattr(discover, "time", clock.as_time_module())
    # a listing that answers each scroll only after the full 2.5s budget, and
    # never stops growing: the ceiling is the ONLY way this scan can end
    page = ListingPage(initial=2, per_scroll=20, respond_after_ms=2400,
                       clock=clock, total=1_000_000)
    _harness(monkeypatch, page)

    result = discover.find_creality(None, 500, "Downloads", None,
                                    extra=0, deadline_s=10.0)

    assert result["timed_out"] is True
    assert 0 < len(result["models"]) < 500        # kept what it had
    assert "ceiling" in result["note"]
    assert "500" in result["note"]                # says what was asked for
    assert page.scrolls < 5                      # nowhere near the 200-pass ceiling
