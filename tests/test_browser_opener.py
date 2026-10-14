"""The resilient sync-Playwright doorway: a dead node driver subprocess must
never spend an auto run (2026-10-10, run 20261010-001102 died at discovery
with "'PlaywrightContextManager' object has no attribute '_playwright'" — the
playwright sync entrance's own silence when its driver handshake fails
mid-way). The opener retries on a FRESH driver and then reports an honest
sentence instead of that bare AttributeError."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser_opener          # noqa: E402
import pytest                  # noqa: E402


def test_the_real_driver_survives_and_drp_the_opener_enters(monkeypatch):
    """The door opens on a healthy driver (no monkeypatching — a real sync
    entry, real driver, launched and closed)."""
    with browser_opener.sync_playwright_auto() as pw:
        b = pw.chromium.launch(headless=True)
        b.close()


def test_two_dead_drivers_then_a_live_one(monkeypatch):
    """The exact 001102 shape: drivers die mid-handshake. The opener must try
    again on a FRESH driver (3 attempts) and enter fine."""
    import playwright._impl._transport as T
    state = {"n": 0}
    orig = T.PipeTransport.connect

    async def flaky(self):
        state["n"] += 1
        if state["n"] <= 2:                    # the first two drivers die
            await orig(self)
            try:
                self._proc.kill()
            except Exception:
                pass
            return
        await orig(self)                        # the third lives

    monkeypatch.setattr(T.PipeTransport, "connect", flaky)
    with browser_opener.sync_playwright_auto() as pw:
        b = pw.chromium.launch(headless=True)
        b.close()
    assert state["n"] == 3        # two dead ones really were consumed


def test_every_driver_dead_is_an_honest_refusal(monkeypatch):
    """When no driver can be had, the caller gets a real sentence naming the
    failure (never the bare AttributeError) — and it matches the run's own
    retryable pattern, so the run retries it like any transport glitch."""
    import playwright._impl._transport as T
    orig = T.PipeTransport.connect

    async def dead(self):
        await orig(self)
        try:
            self._proc.kill()
        except Exception:
            pass

    monkeypatch.setattr(T.PipeTransport, "connect", dead)
    with pytest.raises(RuntimeError) as e:
        browser_opener.sync_playwright_auto().__enter__()
    assert "playwright driver" in str(e.value).lower()


def test_the_dead_driver_words_are_retryable_in_the_run(monkeypatch):
    """main._fetch_failure_is_recoverable() must treat the driver-death words
    (including the old bare AttributeError) as a retryable transport glitch."""
    import main
    assert main._fetch_failure_is_recoverable(
        "'PlaywrightContextManager' object has no attribute '_playwright'")
    assert main._fetch_failure_is_recoverable(
        "Connection.init: Connection closed while reading from the driver")
    assert main._fetch_failure_is_recoverable(
        "the playwright driver died on start 3/3")
    assert not main._fetch_failure_is_recoverable("a page that really is broken")
