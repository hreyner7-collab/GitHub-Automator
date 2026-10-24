"""The ledger is written by more than one live process (the dashboard and a run),
and `ledger._save` runs OUTSIDE any cross-process lock. A save must fold its own
rows into whatever is already on disk instead of overwriting it — otherwise the
later writer silently drops the earlier writer's records."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ledger  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_ledger(monkeypatch, tmp_path):
    """Own tmp ledger, and no socket is ever opened by canonical_url."""
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    real = ledger.canonical_url
    monkeypatch.setattr(
        ledger, "canonical_url",
        lambda url, timeout=12.0, resolver=None:
        real(url, timeout=timeout, resolver=resolver or (lambda u: u)))


def _row(name: str, url: str) -> dict:
    fp = ledger.fingerprints(source_url=url, name=name)
    return {"name": name, "author": "", "slug": fp["slug"], "source_url": url,
            "published_url": "", "outcome": "published", "note": "",
            "fingerprints": fp, "aliases": [url],
            "published_at": "2026-01-01 00:00:00",
            "recorded_at": "2026-01-01 00:00:00",
            "last_seen": "2026-01-01 00:00:00"}


def test_a_save_keeps_a_record_another_process_committed_first():
    # this process read an empty ledger and prepared one new row, unsaved
    data = ledger.load()
    base = list(data["entries"])
    data["entries"].append(_row("Model A", "https://x/model-detail/model-a-1"))
    # meanwhile another live process committed its own row to disk
    ledger.record(name="Model B",
                  source_url="https://x/model-detail/model-b-2")
    # saving our stale copy must keep BOTH — the old code dropped Model B
    ledger._save(data, base=base)
    urls = {e.get("source_url", "") for e in ledger.entries()}
    assert any("model-a-1" in u for u in urls)
    assert any("model-b-2" in u for u in urls)
    assert ledger.find(source_url="https://x/model-detail/model-a-1")["found"]
    assert ledger.find(source_url="https://x/model-detail/model-b-2")["found"]


def test_a_deliberate_forget_stays_dropped_after_the_merge():
    """The merge folds the on-disk ledger in, so a row this process removed on
    purpose must not be resurrected from that disk copy."""
    ledger.record(name="Gone", source_url="https://x/model-detail/gone-model")
    ledger.record(name="Keep", source_url="https://x/model-detail/keep-model")
    assert ledger.forget("https://x/model-detail/gone-model") == 1
    assert not ledger.find(source_url="https://x/model-detail/gone-model")["found"]
    assert ledger.find(source_url="https://x/model-detail/keep-model")["found"]
    assert ledger.count() == 1
