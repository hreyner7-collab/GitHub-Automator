import pytest


@pytest.fixture(autouse=True)
def _tried_listings_in_tmp(monkeypatch, tmp_path):
    """Every run records refused listings in discover.TRIED_PATH. Point that at
    a tmp file for every test, so a fixture URL (or a test-only run) can never
    be written into the real artifacts/tried_listings.json."""
    import discover
    monkeypatch.setattr(discover, "TRIED_PATH", tmp_path / "tried_listings.json")
    yield


@pytest.fixture(autouse=True)
def _makerworld_ledger_in_tmp(monkeypatch, tmp_path):
    """The MakerWorld memory is one real file, and a "published" entry blocks
    that model for good. No test may write it: a fixture entry would be read as
    a live listing by the next real run."""
    import makerworld_ledger
    monkeypatch.setattr(makerworld_ledger, "LEDGER_PATH",
                        str(tmp_path / "makerworld_published.json"))
    yield


@pytest.fixture(autouse=True)
def _no_image_endpoint(monkeypatch):
    """No test draws a picture. photo_gen's own retry loop waits minutes on a
    rate limit, so a test that reaches it must fail at once instead of sleeping
    there — which is also the guarantee that the suite never spends anyone's
    free-tier allowance."""
    import photo_gen

    def blocked(prompt_text, **kw):
        raise RuntimeError("tests never call the image endpoint")

    monkeypatch.setattr(photo_gen, "fetch_jpeg", blocked)
    yield
