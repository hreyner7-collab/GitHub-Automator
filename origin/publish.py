"""Publish an original model through the existing uploader.

An original listing is not a remix: the upload is called with no source URL,
so the form leaves its Model Source block on Original, and the licence is
CC BY 4.0. Three gates stand between a preview and a live listing:

  1. an owner approval record for THIS key, pinning the STL and contact-sheet
     hashes that were on screen when the owner said yes;
  2. a novelty check of the title against Creality's public search — if the
     search cannot answer, nothing is published;
  3. dry run unless `live=True` is passed deliberately.

Only one live publish may run at a time (an exclusive lock beside the model
folders). A submission the uploader cannot confirm is not recorded as published.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import re
import time
from pathlib import Path

from origin import names, printcheck, spec as specmod

LICENCE = "CC BY 4.0"
SEARCH_API = "https://www.crealitycloud.com/api/cxy/smart_search/v1/model"
APPROVAL = "APPROVED.json"
BLOCK_RATIO = 0.90
WARN_RATIO = 0.75


class PublishRefused(RuntimeError):
    pass


class PublishError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _public_search(title: str) -> list[dict]:
    import requests
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36",
    }
    body = {"page": 1, "pageSize": 20, "keyword": title, "sortType": 10,
            "printers": [], "promoType": 0, "isVip": 0, "trendType": 1,
            "multiMark": 0}
    r = requests.post(SEARCH_API, json=body, headers=headers, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError("search refused: %s" % j.get("msg"))
    rows = (j.get("result") or {}).get("list")
    if not isinstance(rows, list):
        raise RuntimeError("search answered without a list")
    return rows


def check_novelty(title: str, search_fn=None) -> dict:
    """Refuse an exact or near-copy title; warn on close ones."""
    search_fn = search_fn or _public_search
    try:
        rows = search_fn(title)
    except Exception as e:
        raise PublishRefused("could not check the name on Creality; refusing "
                             "to publish unverified (%s)" % e)
    conflicts, near = [], []
    for row in rows:
        other = (row.get("groupName") or row.get("name") or "").strip()
        if not other:
            continue
        ratio = _similar(title, other)
        rec = {"id": row.get("id") or "", "name": other,
               "ratio": round(ratio, 3)}
        if _norm(title) == _norm(other) or ratio >= BLOCK_RATIO:
            conflicts.append(rec)
        elif ratio >= WARN_RATIO:
            near.append(rec)
    return {"ok": not conflicts, "query": title, "checked": len(rows),
            "conflicts": conflicts, "near": near}


def approval_path(model_dir: Path) -> Path:
    return Path(model_dir) / APPROVAL


def write_approval(model_dir: Path, note: str = "", when: str | None = None) -> dict:
    """Record the owner's yes for this exact preview. Called only after the
    owner has seen the contact sheet and named this model."""
    model_dir = Path(model_dir)
    rec = _load_record(model_dir)
    stl = Path(rec["files"]["stl"])
    contact = Path(rec["contact"])
    if not stl.is_file() or not contact.is_file():
        raise PublishRefused("the STL or contact sheet is missing; nothing to approve")
    out = {
        "key": rec["spec"]["key"],
        "title": rec["spec"]["title"],
        "licence": rec["licence"],
        "stl_sha256": _sha(stl),
        "contact_sha256": _sha(contact),
        "photos_sha256": [_sha(p) for p in rec.get("photos_4x3") or []],
        "approved_by": "owner",
        "approved_on": when or time.strftime("%Y-%m-%d"),
        "note": note,
    }
    approval_path(model_dir).write_text(json.dumps(out, indent=1),
                                        encoding="utf-8")
    return out


def _load_record(model_dir: Path) -> dict:
    model_dir = Path(model_dir)
    hits = sorted(model_dir.glob("*_photos.json"))
    if len(hits) != 1:
        raise PublishRefused("expected one *_photos.json in %s, found %d"
                             % (model_dir, len(hits)))
    rec = json.loads(hits[0].read_text(encoding="utf-8"))
    s = rec.get("spec") or {}
    key = hits[0].name[:-len("_photos.json")]
    if s.get("key") and s["key"] != key:
        raise PublishRefused("the preview record does not match its folder")
    s["key"] = key
    rec["spec"] = s
    if rec.get("licence") != LICENCE:
        raise PublishRefused("an original listing must be %s, not %r"
                             % (LICENCE, rec.get("licence")))
    for need in ("stl", "contact"):
        p = rec.get("files", {}).get("stl") if need == "stl" else rec.get("contact")
        if not p or not Path(p).is_file():
            raise PublishRefused("the preview's %s file is missing" % need)
    return rec


def _rebuild(rec: dict):
    s = rec["spec"]
    try:
        built = specmod.make_spec(s["family"], int(s["variant"]))
    except Exception as e:
        raise PublishRefused("cannot rebuild the previewed spec: %s" % e)
    if built.key != s["key"] or built.seed != s.get("seed"):
        raise PublishRefused("the preview's seed does not rebuild to the same "
                             "model; re-render before publishing")
    from origin import build
    mesh = build.build(built)
    check = printcheck.check(mesh, built)
    if not check.ok:
        raise PublishRefused("the rebuilt mesh fails print checks: %s"
                             % "; ".join(check.issues))
    exported = hashlib.sha256(mesh.export(file_type="stl")).hexdigest()
    if exported != rec["files"]["stl_sha256"]:
        raise PublishRefused("the preview STL is not the mesh this spec builds "
                             "(rebuild hash differs)")
    return built, mesh, check


def _photos(rec: dict) -> list[Path]:
    photos = [Path(p) for p in rec.get("photos_4x3") or []]
    if len(photos) != 5:
        raise PublishRefused("expected 5 4:3 photos, found %d" % len(photos))
    missing = [str(p) for p in photos if not p.is_file() or p.stat().st_size < 5000]
    if missing:
        raise PublishRefused("photos missing or empty: %s" % ", ".join(missing))
    return photos


def check_approval(model_dir: Path, rec: dict) -> dict:
    path = approval_path(model_dir)
    if not path.is_file():
        raise PublishRefused("no owner approval for this model; showing previews "
                             "is not permission to publish")
    try:
        ap = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise PublishRefused("the approval record is unreadable: %s" % e)
    want = {
        "key": rec["spec"]["key"],
        "title": rec["spec"]["title"],
        "licence": LICENCE,
        "stl_sha256": _sha(rec["files"]["stl"]),
        "contact_sha256": _sha(rec["contact"]),
        "photos_sha256": [_sha(p) for p in rec.get("photos_4x3") or []],
    }
    for k, v in want.items():
        if ap.get(k) != v:
            raise PublishRefused("approval does not match this preview (%s); "
                                 "ask the owner again" % k)
    if not ap.get("approved_by") or not ap.get("approved_on"):
        raise PublishRefused("the approval record is not signed")
    return ap


class _LiveLock:
    def __init__(self, model_dir: Path):
        self.path = Path(model_dir).parent / ".publishing.lock"

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise PublishRefused("another publish is already running (%s)"
                                 % self.path)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def publish(model_dir: Path, *, live: bool = False, search_fn=None,
            upload_fn=None, record_fn=None) -> dict:
    """Rehearse by default; `live=True` publishes one already-approved model."""
    model_dir = Path(model_dir)
    rec = _load_record(model_dir)
    built, mesh, check = _rebuild(rec)
    photos = _photos(rec)
    dims = tuple(float(v) for v in (mesh.bounds[1] - mesh.bounds[0]))
    note = names.print_note_for(built.family, check.supports_needed)
    description = names.description_for(built, dims, note)
    novelty = check_novelty(built.title, search_fn=search_fn)
    if not novelty["ok"]:
        raise PublishRefused("the title already exists on Creality: %s"
                             % ", ".join(c["name"] for c in novelty["conflicts"]))

    approval = None
    if approval_path(model_dir).is_file():
        approval = check_approval(model_dir, rec)

    plan = {
        "key": built.key,
        "title": built.title,
        "description": description,
        "licence": LICENCE,
        "source_url": "",
        "remix": False,
        "stl": rec["files"]["stl"],
        "photos": [str(p) for p in photos],
        "novelty": novelty,
        "approval": approval,
        "live": bool(live),
    }
    if not live:
        plan.update(published=False, dry_run=True,
                    reason="dry run; pass live=True only after the owner approves")
        return plan

    if approval is None:
        raise PublishRefused("no owner approval for this model; showing previews "
                             "is not permission to publish")

    with _LiveLock(model_dir):
        if upload_fn is None:
            from uploader import upload as upload_fn
        result = upload_fn(built.title, description, plan["stl"],
                           plan["photos"], source_url="",
                           dry_run=False, license_raw=LICENCE)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        url = (result or {}).get("url") or ""
        if not result or not result.get("success") or \
                not url or "/create-model" in url:
            why = (result or {}).get("error") or result
            raise PublishError("Creality did not confirm the publish: %s" % why)
        if (result.get("verify") or {}).get("remix_source_declared"):
            raise PublishError("a source model was declared on an original "
                               "listing; review the form before retrying")
        plan.update(published=True, dry_run=False, url=url,
                    upload={k: v for k, v in result.items() if k != "verify"})
        if record_fn is None:
            import ledger
            record_fn = ledger.record
        try:
            record_fn(name=built.title, source_url="", author="",
                      stl_path=plan["stl"], published_url=url,
                      outcome="published",
                      note="original design, rendered from its own STL; CC BY 4.0")
            plan["ledger_recorded"] = True
        except Exception as e:
            # The listing is already live; a ledger failure must not look like
            # a publish failure and invite a duplicate retry.
            plan["ledger_recorded"] = False
            plan["ledger_error"] = str(e)
    return plan
