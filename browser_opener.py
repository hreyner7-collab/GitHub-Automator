"""A sync-Playwright doorway that survives a dead playwright driver.

2026-10-10, run 20261010-001102: the auto run died at discovery with
"'PlaywrightContextManager' object has no attribute '_playwright'". The
playwright-python SYNC entrance (sync_api/_context_manager.py) starts a node
driver subprocess and waits for its handshake; when the driver dies during
that handshake (the same "Connection closed while reading from the driver"
deaths the dashboard's async analytics loop had been logging), the entrance
falls out of its greenlet without ever assigning `self._playwright` and
raises exactly that AttributeError.

The failure is per-DRIVER-PROCESS, not per-model: a fresh sync entrance
spawns a fresh driver and connects fine (verified live). So every sync
Playwright use in the pipeline goes through sync_playwright_auto(): up to
SYNC_PW_ATTEMPTS quick tries, each on a brand-new driver, and the LAST
error — now a real sentence instead of a bare AttributeError — is raised.
"""
from __future__ import annotations

import re
import time

SYNC_PW_ATTEMPTS = 3
SYNC_PW_BACKOFF = 1.5     # seconds between tries

# A dead driver announces itself a few different ways depending on where in
# the handshake it was killed; all of them mean "try again on a fresh one".
_DRIVER_DEAD_RE_STR = (
    r"object has no attribute '_playwright'"
    r"|connection closed while reading from the driver"
    r"|browser closed the connection"
    r"|target browser has been closed"
    r"|failed to start"
)


def _looks_driver_dead(message: str) -> bool:
    return bool(re.search(_DRIVER_DEAD_RE_STR, message or "", re.I))


def sync_playwright_auto():
    """`with sync_playwright_auto() as pw:` — a resilient entrance over
    playwright.sync_api.sync_playwright(). Each attempt is a FRESH entrance
    (and therefore a fresh node driver), so one dead driver never spends the
    whole run. Raises the last error, described honestly."""
    from playwright.sync_api import sync_playwright

    last = None
    for attempt in range(1, SYNC_PW_ATTEMPTS + 1):
        # a brand-new entrance on every attempt — never re-enter one that
        # has already failed (its greenlet/connection state is spent)
        entrance = sync_playwright()
        try:
            pwobj = entrance.start()
            return _SyncDoorway(entrance, pwobj)
        except AttributeError as e:
            # the known bug's own shape (the entrance fell through without
            # the handshake landing): re-wrap it as the driver death it is
            last = e
            err = ("the playwright driver died on start %d/%d (handshake never "
                   "finished: %s)" % (attempt, SYNC_PW_ATTEMPTS, str(e)[:120]))
        except Exception as e:
            last = e
            err = "playwright start failed on attempt %d: %s" % (attempt, str(e)[:160])
        if attempt < SYNC_PW_ATTEMPTS and _looks_driver_dead(str(last)):
            time.sleep(SYNC_PW_BACKOFF)
            continue
        raise RuntimeError(err) from last
    raise RuntimeError("playwright driver kept dying: %s" % last)


class _SyncDoorway:
    """Holds both the entrance and the started playwright so `with` exits
    exactly the way `with sync_playwright() as pw` does."""

    def __init__(self, entrance, pwobj):
        self._entrance = entrance
        self._pwobj = pwobj

    def __enter__(self):
        return self._pwobj

    def __exit__(self, *exc):
        try:
            return self._entrance.__exit__(*exc)
        except Exception:
            return False      # a dying driver at exit must not mask the work
