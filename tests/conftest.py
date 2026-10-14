import pytest


@pytest.fixture(autouse=True)
def _tried_listings_in_tmp(monkeypatch, tmp_path):
    """Every run records refused listings in discover.TRIED_PATH. Point that at
    a tmp file for every test, so a fixture URL (or a test-only run) can never
    be written into the real artifacts/tried_listings.json."""
    import discover
    monkeypatch.setattr(discover, "TRIED_PATH", tmp_path / "tried_listings.json")
    yield
