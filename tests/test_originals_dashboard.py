"""The dashboard's originals path, exercised without a browser or an account.

Find & publish may only prepare previews; the one upload is the owner's
per-model Approve & publish click. These tests prove both halves, and that a
second click cannot publish the same model twice.
"""
import asyncio
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from origin import library as originals_lib

KEY = "desk_organiser-01-deadbeef"
TITLE = "Desk Organiser - 120 mm"
URL = "https://www.crealitycloud.com/model-detail/test-original"


def _plant(root: Path, key: str = KEY, title: str = TITLE,
           family: str = "desk_organiser") -> Path:
    """Write one finished preview exactly the way shots.make_photos does."""
    d = Path(root) / key
    photos = d / "photos"
    photos.mkdir(parents=True)
    stl = d / "model.stl"
    stl.write_bytes(b"solid test\nendsolid test\n")
    photos_4x3 = []
    for i in range(5):
        p = photos / ("shot-%d.jpg" % i)
        p.write_bytes(b"\xff\xd8\xff" + b"p" * 6000)
        photos_4x3.append(str(p))
    contact = d / ("%s_contact.jpg" % key)
    contact.write_bytes(b"\xff\xd8\xff" + b"c" * 6000)
    rec = {
        "spec": {"title": title, "family": family, "variant": 1},
        "files": {"stl": str(stl)},
        "contact": str(contact),
        "photos_4x3": photos_4x3,
        "render": {"wall_seconds": 97.5},
        "check": {"metrics": {"dims_mm": [120.0, 60.0, 40.0]}},
        "licence": "CC BY 4.0",
    }
    (d / ("%s_photos.json" % key)).write_text(json.dumps(rec), encoding="utf-8")
    return d


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    root = tmp_path / "originals"
    monkeypatch.setenv("CREALITY_ORIGINALS_DIR", str(root))
    return root


@pytest.fixture(autouse=True)
def _reset_originals_state():
    main.ORIGINALS.update({
        "state": "idle", "job_id": None, "items": [], "requested": 0,
        "note": "", "stop": False, "progress": {}, "publishing": "",
        "finished": None, "stale": [],
    })
    yield


def _drive_start(count: int, timeout: float = 10.0) -> dict:
    """Call the real start endpoint on one loop and wait for its worker.

    TestClient tears its event loop down after each request when it is not a
    context manager, which cancels the background task; driving the endpoint
    inside asyncio.run keeps the worker on a live loop without starting the
    app's startup hooks (those read the account).
    """
    async def run():
        started = await main.originals_start(main.OriginalsRequest(count=count))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (main.ORIGINALS.get("job_id") == started["job_id"]
                    and main.ORIGINALS.get("state") in ("done", "stopped", "error")):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("originals run did not finish: %r" % main.ORIGINALS)
        return started

    return asyncio.run(run())


def test_status_lists_a_finished_preview_and_serves_its_contact_sheet(shelf):
    _plant(shelf)
    client = TestClient(main.app)
    s = client.get("/originals/status").json()

    assert s["state"] == "idle"
    assert s["licence"] == "CC BY 4.0"
    assert s["counts"]["preview"] == 1 and s["counts"]["ready"] == 1
    card = s["shelf"][0]
    assert card["key"] == KEY and card["title"] == TITLE
    assert card["state"] == "preview"
    assert card["contact_url"] == "/originals/contact/" + KEY
    assert len(card["photo_urls"]) == 5
    assert card["dims_mm"] == [120.0, 60.0, 40.0]
    assert card["seconds"] == 97.5
    assert card["licence"] == "CC BY 4.0"

    r = client.get(card["contact_url"])
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8\xff")
    assert client.get("/originals/contact/..%2F..%2Fetc").status_code == 404


def test_find_and_publish_prepares_a_fresh_set_without_uploading(shelf, monkeypatch):
    old = _plant(shelf)
    made = []

    def fake_prepare(root, family, samples=28, res=1000):
        made.append(family)
        key = "%s-02-cafebabe" % family
        d = _plant(Path(root), key=key, title="%s - test" % family, family=family)
        return {"key": key, "family": family, "variant": 2,
                "title": "%s - test" % family, "seconds": 12.0,
                "contact": str(d / ("%s_contact.jpg" % key)),
                "photos": [], "check": {"ok": True}}

    def refuse_upload(*a, **k):
        raise AssertionError("Find & publish must never upload")

    monkeypatch.setattr(originals_lib, "prepare", fake_prepare)
    monkeypatch.setattr(main.originals_publish, "publish", refuse_upload)

    started = _drive_start(2)
    assert started["requested"] == 2
    assert main.ORIGINALS["state"] == "done", main.ORIGINALS

    s = TestClient(main.app).get("/originals/status").json()
    assert s["job_id"] == started["job_id"]
    assert len(s["shelf"]) == 2
    assert {p["state"] for p in s["shelf"]} == {"preview"}
    # both are new: the earlier preview is replaced, not reused
    assert made, "no model was prepared"
    assert len(made) == 2
    assert KEY not in {p["key"] for p in s["shelf"]}
    assert not old.exists()


def test_a_fresh_run_keeps_published_models(shelf, monkeypatch):
    old = _plant(shelf)
    kept_key = "wall_hook-01-aaaaaaaa"
    kept = _plant(shelf, key=kept_key, title="Wall Hook - 54.7 mm Reach",
                  family="wall_hook")
    (kept / "PUBLISHED.json").write_text(json.dumps({
        "key": kept_key, "title": "Wall Hook - 54.7 mm Reach", "url": URL,
        "at": "2026-10-24 12:00:00", "licence": "CC BY 4.0",
    }), encoding="utf-8")

    def fake_prepare(root, family, samples=28, res=1000):
        key = "%s-02-cafebabe" % family
        d = _plant(Path(root), key=key, title="%s - fresh" % family, family=family)
        return {"key": key, "family": family, "variant": 2,
                "title": "%s - fresh" % family, "seconds": 12.0,
                "contact": str(d / ("%s_contact.jpg" % key)),
                "photos": [], "check": {"ok": True}}

    monkeypatch.setattr(originals_lib, "prepare", fake_prepare)
    _drive_start(1)

    s = TestClient(main.app).get("/originals/status").json()
    keys = {p["key"] for p in s["shelf"]}
    assert not old.exists()
    assert kept.exists() and kept_key in keys
    assert len(s["shelf"]) == 2


def test_start_validates_the_typed_count(shelf):
    client = TestClient(main.app)
    for bad in (0, -3, 51):
        r = client.post("/originals/start", json={"count": bad})
        assert r.status_code == 400, (bad, r.text)


def test_approve_and_publish_uploads_once_then_refuses_a_second_click(
        shelf, monkeypatch):
    model_dir = _plant(shelf)
    uploads = []
    approvals = []

    def fake_publish(model_dir, *, live=False, **kw):
        uploads.append((Path(model_dir).name, live))
        return {"url": URL, "novelty": {"ok": True}, "ledger_recorded": True}

    def fake_approval(model_dir, note):
        approvals.append((Path(model_dir).name, note))
        return {"note": note, "hashes": {"stl": "stub"}}

    monkeypatch.setattr(main.originals_publish, "publish", fake_publish)
    monkeypatch.setattr(main.originals_publish, "write_approval", fake_approval)

    client = TestClient(main.app)
    r = client.post("/originals/publish", json={"key": KEY, "note": "my yes"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["published"] is True and body["url"] == URL
    assert uploads == [(KEY, True)]
    assert approvals == [(KEY, "my yes")]

    marker = json.loads((model_dir / "PUBLISHED.json").read_text(encoding="utf-8"))
    assert marker["url"] == URL and marker["title"] == TITLE
    s = client.get("/originals/status").json()
    assert s["shelf"][0]["state"] == "published"
    assert s["shelf"][0]["published_url"] == URL

    second = client.post("/originals/publish", json={"key": KEY})
    assert second.status_code == 409
    assert len(uploads) == 1


def test_an_unexpected_publish_error_is_reported_and_unsticks(shelf, monkeypatch):
    _plant(shelf)
    monkeypatch.setattr(main.originals_publish, "write_approval",
                        lambda d, n: {"note": n})

    def boom(*a, **k):
        raise ValueError("uploader exploded")

    monkeypatch.setattr(main.originals_publish, "publish", boom)
    client = TestClient(main.app)
    r = client.post("/originals/publish", json={"key": KEY})
    assert r.status_code == 502
    assert "uploader exploded" in r.text
    s = client.get("/originals/status").json()
    assert s["publishing"] == ""


def test_publish_refuses_unknown_and_invalid_keys(shelf):
    client = TestClient(main.app)
    for key, code in (("../../etc/passwd", 400), ("nope", 400),
                      ("desk_organiser-01-00000000", 404)):
        r = client.post("/originals/publish", json={"key": key})
        assert r.status_code == code, (key, r.text)


def test_the_dashboard_card_is_the_originals_path():
    html = (Path(__file__).resolve().parents[1] / "creality" / "index.html"
            ).read_text(encoding="utf-8")
    # The version marker must exist and agree with itself. It is deliberately
    # not pinned to a number here: AGENTS.md bumps it on every update, so a
    # literal "V16" in this test fails the next build for the wrong reason.
    m = re.search(r"<title>Creality Pipeline (V\d+)</title>", html)
    assert m, "the dashboard title carries no version marker"
    assert f'id="ui-version" title="Dashboard version: bumped on every update">{m.group(1)}<' in html
    assert "/originals/start" in html and "/originals/publish" in html
    assert "Approve &amp; publish" in html
    assert "no designer's photos are edited or re-uploaded" in html
    assert "/auto/start" not in html
    assert "auto-keywords" not in html
