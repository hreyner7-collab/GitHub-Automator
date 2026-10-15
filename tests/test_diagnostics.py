"""The dashboard's self-test, and the promise that it starts nothing.

The point of /diagnostics is to answer "is this thing healthy, and if not, what
do I run?" without side effects: it must never launch a browser or read the
account, because it is meant to be safe to click at any time. These tests pin
that, pin that every check carries a fix line when it fails, and pin that the
self-test inside it can actually fail (a check that cannot fail is not a check).
"""
from fastapi.testclient import TestClient

import main


def _client():
    return TestClient(main.app)


def test_diagnostics_reports_every_check_with_a_verdict():
    body = _client().get("/diagnostics").json()
    assert body["build"] == main.BUILD
    names = [c["name"] for c in body["checks"]]
    for expected in ("server is answering", "port owner", "running the code on disk",
                     "ledger readable", "tried-listings record readable",
                     "artifacts writable", "harvest policy loads",
                     "Creality's rules load", "starts with Windows",
                     "step checks catch faults"):
        assert expected in names, expected
    assert body["ok"] == all(c["ok"] for c in body["checks"])


def test_every_failing_check_says_how_to_fix_it():
    """A failed check with no fix line is the old experience: an error and no
    way forward."""
    for check in _client().get("/diagnostics").json()["checks"]:
        if not check["ok"]:
            assert check["fix"], check["name"]


def test_the_fast_checks_never_start_a_browser(monkeypatch):
    """This is an explicit product rule, not a nicety: a health endpoint that
    launches Chromium spawns a browser every time anything polls it."""
    called = []

    def boom():
        called.append(1)
        return True

    monkeypatch.setattr(main, "_chromium_available", boom)
    for _ in range(2):
        _client().get("/diagnostics")
    assert called == [], "the fast path must not launch a browser"


def test_the_browser_check_is_the_one_place_that_does_start_one(monkeypatch):
    called = []

    def fake():
        called.append(1)
        return True

    monkeypatch.setattr(main, "_chromium_available", fake)
    body = _client().get("/diagnostics/browser").json()
    assert called == [1]
    assert body["ok"] is True and "Chromium" in body["check"]


def test_the_browser_check_says_what_to_run_when_it_fails(monkeypatch):
    monkeypatch.setattr(main, "_chromium_available", lambda: False)
    body = _client().get("/diagnostics/browser").json()
    assert body["ok"] is False
    assert "playwright install" in body["fix"]


def test_the_built_in_self_test_can_fail():
    """If the checks ever stopped catching faults, this must notice."""
    assert main._step_check_self_test() == []

    import verify

    original = verify.step_two_open
    verify.step_two_open = lambda model: verify.Check(True, "everything is fine")
    try:
        faults = main._step_check_self_test()
        assert any("step 1" in f for f in faults), faults
    finally:
        verify.step_two_open = original
