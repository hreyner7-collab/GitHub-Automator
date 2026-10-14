"""A pasted French-locale model URL must not leak to the generic scrape
(2026-10-10, run 20261010-001102: every pasted model link came back as
"https://www.crealitycloud.com/fr/model-detail/..." because the account's
Chrome copies its stored /fr/ locale — the router's model-detail regex didn't
tolerate the prefix and the fetch fell through to _fetch_generic, which has no
model-page handling). Pinned here: the URL is stripped to the English site
BEFORE any routing or slug-extraction, and any crealitycloud URL routes to the
Creality fetcher."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import downloader                # noqa: E402
import pytest                    # noqa: E402


# ── _english_site_url: the stored locale never leaks into a model URL ────────

@pytest.mark.parametrize("pasted,english", [
    ("https://www.crealitycloud.com/fr/model-detail/foo-bar?src=x",
     "https://www.crealitycloud.com/model-detail/foo-bar?src=x"),
    ("https://www.crealitycloud.com/de/model-detail/foo-bar",
     "https://www.crealitycloud.com/model-detail/foo-bar"),
    ("https://www.crealitycloud.com/es/models/abc",
     "https://www.crealitycloud.com/models/abc"),
    ("https://www.crealitycloud.com/model-category/3d-print-all",
     "https://www.crealitycloud.com/model-category/3d-print-all"),
    ("https://www.crealitycloud.com/models/abc",
     "https://www.crealitycloud.com/models/abc"),
])
def test_english_site_url_strips_the_locale_prefix(pasted, english):
    assert downloader._english_site_url(pasted) == english


# ── _canon_model_url: the slug is the key ────────────────────────────────────

def test_canon_model_url_french_pasted():
    assert downloader._canon_model_url(
        "https://www.crealitycloud.com/fr/model-detail/foo-bar?src=x"
    ) == "https://crealitycloud.com/model-detail/foo-bar"


def test_canon_model_url_query_dropped():
    assert downloader._canon_model_url(
        "https://www.crealitycloud.com/model-detail/foo-bar?src=x"
    ) == "https://crealitycloud.com/model-detail/foo-bar"


def test_canon_model_url_english_unchanged():
    assert downloader._canon_model_url(
        "https://www.crealitycloud.com/model-detail/foo-bar"
    ) == "https://crealitycloud.com/model-detail/foo-bar"


def test_canon_model_url_non_model_falls_back():
    u = "https://www.crealitycloud.com/models/abc"
    assert downloader._canon_model_url(u) == u


# ── the router: no crealitycloud URL ever reaches the generic scrape ────────

def test_french_model_url_routes_to_creality_fetcher(monkeypatch):
    hits = []
    monkeypatch.setattr(downloader, "_fetch_creality",
                        lambda url: hits.append(url) or {"ok": True})
    monkeypatch.setattr(downloader, "_fetch_generic",
                        lambda url: hits.append("GENERIC:" + url) or {"ok": True})
    r = downloader.fetch("https://www.crealitycloud.com/fr/model-detail/foo-bar")
    assert r.get("ok") is True
    assert hits == ["https://www.crealitycloud.com/fr/model-detail/foo-bar"]


def test_english_model_url_routes_to_creality_fetcher(monkeypatch):
    hits = []
    monkeypatch.setattr(downloader, "_fetch_creality",
                        lambda url: hits.append(url) or {"ok": True})
    monkeypatch.setattr(downloader, "_fetch_generic",
                        lambda url: hits.append("GENERIC:" + url) or {"ok": True})
    r = downloader.fetch("https://www.crealitycloud.com/model-detail/foo-bar")
    assert r.get("ok") is True
    assert hits == ["https://www.crealitycloud.com/model-detail/foo-bar"]


def test_non_creality_url_never_routes_to_creality_fetcher(monkeypatch):
    hits = []
    monkeypatch.setattr(downloader, "_fetch_creality",
                        lambda url: hits.append("CREALITY:" + url) or {"ok": True})
    monkeypatch.setattr(downloader, "_fetch_generic",
                        lambda url: hits.append(url) or {"ok": True})
    r = downloader.fetch("https://makerworld.com/en/@someone/models/abc")
    assert r.get("ok") is True
    assert hits == ["https://makerworld.com/en/@someone/models/abc"]
    assert not any(h.startswith("CREALITY:") for h in hits)
