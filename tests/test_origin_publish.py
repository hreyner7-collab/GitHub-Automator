"""Publishing an original is gated and browser-free under test.

A fake uploader stands in for uploader.upload: these tests prove the arguments
it would receive (no source, CC BY, this model's five photos) and that every
refusal happens before it is called.
"""
import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from origin import build, export, printcheck, publish, spec


def _dummy_jpg(path: Path, seed: int) -> Path:
    im = Image.new("RGB", (800, 600), (180, 150, 120))
    d = ImageDraw.Draw(im)
    d.rectangle([seed * 7 % 200, 40, 600, 500], fill=(90, 110, 90))
    im.save(path, "JPEG", quality=95)
    return path


def _model_dir(parent: Path, family="cable_clip", variant=1) -> tuple[Path, spec.ModelSpec]:
    s = spec.make_spec(family, variant)
    m = build.build(s)
    c = printcheck.check(m, s)
    assert c.ok
    root = parent / s.key
    root.mkdir(parents=True, exist_ok=True)
    files = export.export_model(m, root / "files", s.key, s.title)
    (root / "photos").mkdir(exist_ok=True)
    photos = [_dummy_jpg(root / "photos" / ("%s_%d.jpg" % (s.key, i)), i)
              for i in range(5)]
    contact = _dummy_jpg(root / ("%s_contact.jpg" % s.key), 3)
    rec = {
        "spec": s.as_dict(), "files": files, "render": {}, "licence": publish.LICENCE,
        "photos_4x3": [str(p) for p in photos],
        "photos_3x4": [str(p) for p in photos],
        "contact": str(contact), "brightness": {},
    }
    (root / ("%s_photos.json" % s.key)).write_text(json.dumps(rec), encoding="utf-8")
    return root, s


def _search(rows):
    return lambda title: rows


CLEAN = _search([{"id": "1", "groupName": "Garden Hose Wall Bracket"},
                 {"id": "2", "groupName": "Desk Drawer Divider"}])


class FakeUpload:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {
            "success": True, "verify": {},
            "url": "https://www.crealitycloud.com/model-detail/cove-cable-clip"}

    def __call__(self, *a, **kw):
        self.calls.append({"args": a, "kwargs": kw})
        return dict(self.result)


def test_dry_run_is_the_default_and_never_uploads(tmp_path):
    d, s = _model_dir(tmp_path)
    up = FakeUpload()
    plan = publish.publish(d, search_fn=CLEAN, upload_fn=up)
    assert plan["dry_run"] is True and plan["published"] is False
    assert plan["source_url"] == "" and plan["remix"] is False
    assert plan["licence"] == publish.LICENCE
    assert plan["approval"] is None
    assert len(plan["photos"]) == 5
    assert up.calls == []


def test_live_without_owner_approval_is_refused(tmp_path):
    d, _ = _model_dir(tmp_path)
    up = FakeUpload()
    with pytest.raises(publish.PublishRefused, match="approval"):
        publish.publish(d, live=True, search_fn=CLEAN, upload_fn=up)
    assert up.calls == []
    assert not (d.parent / ".publishing.lock").exists()


def test_live_publishes_original_work_with_no_source(tmp_path):
    d, s = _model_dir(tmp_path)
    ap = publish.write_approval(d, note="owner said yes in chat")
    assert ap["stl_sha256"] == json.loads(
        (d / ("%s_photos.json" % s.key)).read_text())["files"]["stl_sha256"]
    up = FakeUpload()
    recorded = []
    plan = publish.publish(
        d, live=True, search_fn=CLEAN, upload_fn=up,
        record_fn=lambda **kw: recorded.append(kw))
    assert plan["published"] is True
    assert plan["url"].endswith("cove-cable-clip")
    assert len(up.calls) == 1
    kw = up.calls[0]["kwargs"]
    assert kw["source_url"] == ""
    assert kw["license_raw"] == publish.LICENCE
    assert kw["dry_run"] is False
    title, description, stl, photos = up.calls[0]["args"]
    assert title == s.title
    assert "original" in description.lower()
    assert "CC BY 4.0" in description
    assert "not copied" in description
    assert stl == plan["stl"] and Path(stl).is_file()
    assert len(photos) == 5 and all(Path(p).is_file() for p in photos)
    assert recorded and recorded[0]["source_url"] == ""
    assert recorded[0]["published_url"] == plan["url"]
    assert not (d.parent / ".publishing.lock").exists()


def test_live_publish_awaits_the_async_uploader(tmp_path):
    """The real uploader is `async def`; publishing it unawaited returned a
    coroutine and crashed after approval was recorded, uploading nothing."""
    d, _ = _model_dir(tmp_path)
    publish.write_approval(d, note="async owner yes")
    calls = []
    recorded = []

    async def async_upload(*a, **kw):
        await asyncio.sleep(0)
        calls.append(kw)
        return {"success": True, "verify": {},
                "url": "https://www.crealitycloud.com/model-detail/async-clip"}

    plan = publish.publish(d, live=True, search_fn=CLEAN,
                           upload_fn=async_upload,
                           record_fn=lambda **kw: recorded.append(kw))
    assert plan["published"] is True
    assert plan["url"].endswith("/async-clip")
    assert len(calls) == 1 and calls[0]["dry_run"] is False
    assert len(recorded) == 1


def test_title_already_on_the_site_blocks_even_with_approval(tmp_path):
    d, s = _model_dir(tmp_path)
    publish.write_approval(d)
    up = FakeUpload()
    clash = _search([{"id": "9", "groupName": s.title}])
    with pytest.raises(publish.PublishRefused, match="already exists"):
        publish.publish(d, live=True, search_fn=clash, upload_fn=up)
    assert up.calls == []


def test_near_title_is_reported_but_not_blocked(tmp_path):
    d, s = _model_dir(tmp_path)
    plan = publish.publish(d, search_fn=_search(
        [{"id": "9", "groupName": s.title.rsplit(" - ", 1)[0]}]))
    assert plan["novelty"]["ok"] is True
    assert plan["novelty"]["near"]


def test_unverifiable_name_refuses_to_publish(tmp_path):
    d, _ = _model_dir(tmp_path)
    publish.write_approval(d)
    up = FakeUpload()
    def broken(title):
        raise TimeoutError("search timed out")
    with pytest.raises(publish.PublishRefused, match="could not check the name"):
        publish.publish(d, live=True, search_fn=broken, upload_fn=up)
    assert up.calls == []


def test_a_changed_stl_invalidates_the_approval(tmp_path):
    d, s = _model_dir(tmp_path)
    publish.write_approval(d)
    stl = Path(json.loads((d / ("%s_photos.json" % s.key)).read_text())["files"]["stl"])
    stl.write_bytes(stl.read_bytes() + b"\0")
    up = FakeUpload()
    with pytest.raises(publish.PublishRefused, match="approval does not match"):
        publish.publish(d, live=True, search_fn=CLEAN, upload_fn=up)
    assert up.calls == []


def test_an_approval_for_another_title_is_refused(tmp_path):
    d, s = _model_dir(tmp_path)
    publish.write_approval(d)
    ap = json.loads((d / publish.APPROVAL).read_text())
    ap["title"] = "Something Else Entirely"
    (d / publish.APPROVAL).write_text(json.dumps(ap))
    up = FakeUpload()
    with pytest.raises(publish.PublishRefused, match="title"):
        publish.publish(d, live=True, search_fn=CLEAN, upload_fn=up)
    assert up.calls == []


def test_non_cc_by_preview_is_refused(tmp_path):
    d, s = _model_dir(tmp_path)
    rec_path = d / ("%s_photos.json" % s.key)
    rec = json.loads(rec_path.read_text())
    rec["licence"] = "CC BY-NC"
    rec_path.write_text(json.dumps(rec))
    with pytest.raises(publish.PublishRefused, match="CC BY 4.0"):
        publish.publish(d, search_fn=CLEAN)


def test_an_unconfirmed_submission_is_not_recorded(tmp_path):
    d, s = _model_dir(tmp_path)
    publish.write_approval(d)
    # uploader.upload reports success=True when the form is filled but Submit
    # was never clicked; the URL is still the create-model form. Not published.
    up = FakeUpload({"success": True, "url":
                     "https://www.crealitycloud.com/en/create-model-new?source=12"})
    recorded = []
    with pytest.raises(publish.PublishError):
        publish.publish(d, live=True, search_fn=CLEAN, upload_fn=up,
                        record_fn=lambda **kw: recorded.append(kw))
    assert recorded == []


def test_an_unexpected_remix_source_is_not_recorded(tmp_path):
    d, _ = _model_dir(tmp_path)
    publish.write_approval(d)
    up = FakeUpload({"success": True, "verify": {"remix_source_declared": True},
                     "url": "https://www.crealitycloud.com/model-detail/x"})
    recorded = []
    with pytest.raises(publish.PublishError, match="source model"):
        publish.publish(d, live=True, search_fn=CLEAN, upload_fn=up,
                        record_fn=lambda **kw: recorded.append(kw))
    assert recorded == []
    assert not (d.parent / ".publishing.lock").exists()


def test_a_changed_photo_invalidates_the_approval(tmp_path):
    d, s = _model_dir(tmp_path)
    publish.write_approval(d)
    rec = json.loads((d / ("%s_photos.json" % s.key)).read_text())
    photo = Path(rec["photos_4x3"][0])
    photo.write_bytes(photo.read_bytes() + b"\0")
    up = FakeUpload()
    with pytest.raises(publish.PublishRefused, match="photos_sha256"):
        publish.publish(d, live=True, search_fn=CLEAN, upload_fn=up)
    assert up.calls == []


def test_a_ledger_failure_does_not_report_the_live_listing_as_failed(tmp_path):
    d, _ = _model_dir(tmp_path)
    publish.write_approval(d)
    def broken_ledger(**kw):
        raise OSError("disk full")
    plan = publish.publish(d, live=True, search_fn=CLEAN,
                           upload_fn=FakeUpload(), record_fn=broken_ledger)
    assert plan["published"] is True
    assert plan["ledger_recorded"] is False
    assert "disk full" in plan["ledger_error"]


def test_second_publish_is_refused_while_one_is_locked(tmp_path):
    d, _ = _model_dir(tmp_path)
    publish.write_approval(d)
    lock = d.parent / ".publishing.lock"
    lock.write_text("1234")
    up = FakeUpload()
    with pytest.raises(publish.PublishRefused, match="already running"):
        publish.publish(d, live=True, search_fn=CLEAN, upload_fn=up)
    assert up.calls == []
    lock.unlink()
