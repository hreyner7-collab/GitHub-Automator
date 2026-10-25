"""MakerWorld's own 'already published' memory — a separate file from the
Creality ledger, on purpose (the two targets must never lock each other's
models: a model on Creality is not a model on MakerWorld).

Storage: artifacts/makerworld_published.json (override with MAKERWORLD_LEDGER).
An entry is recognised by ANY of: its source link, its MakerWorld listing (url
or numeric design id), its name + author, or the model file's sha256. A FAILED
publish stays retryable; only successes are locked.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time

import makerworld_flow as mw

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER_PATH = os.environ.get("MAKERWORLD_LEDGER") or os.path.join(
    BASE, "artifacts", "makerworld_published.json")

_LOCK = threading.Lock()


def _norm_url(u: str) -> str:
    u = (u or "").strip().lower()
    if not u:
        return ""
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.)?makerworld\.com", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = re.sub(r"^/(en|de|fr|es|it|pt|nl|pl|ru|ja|ko|zh|zh-cn|cs|tr|uk)/", "/", u)
    return u


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _identity(name="", author="", source_url="", published_url="",
              design_id="", file_path="") -> dict:
    did = str(design_id or "").strip() or mw.design_id_from_url(published_url)
    digest = mw.file_fingerprint(file_path) if file_path else ""
    return {
        "source_urls": [u for u in (_norm_url(source_url),) if u],
        "published_urls": [u for u in (_norm_url(published_url),) if u],
        "design_ids": [d for d in (did,) if d],
        "name_author": [n for n in (_name_key(f"{name} {author}"),) if n],
        "file_digests": [d for d in (digest,) if d],
    }


def _load() -> list[dict]:
    try:
        with open(LEDGER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LEDGER_PATH), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, LEDGER_PATH)


def _same(a: dict, b: dict) -> bool:
    for k in ("source_urls", "published_urls", "design_ids", "name_author", "file_digests"):
        if a.get(k) and b.get(k) and set(a[k]) & set(b[k]):
            return True
    return False


def record(*, name: str = "", author: str = "", source_url: str = "",
           published_url: str = "", design_id: str = "", file_path: str = "",
           outcome: str = "published", note: str = "", tags=None) -> dict:
    """Add (or update) one entry. 'published' locks the model; anything else
    (failed/stopped/rejected) stays retryable but is remembered as history."""
    ident = _identity(name=name, author=author, source_url=source_url,
                      published_url=published_url, design_id=design_id,
                      file_path=file_path)
    with _LOCK:
        entries = _load()
        for e in entries:
            if _same(e.get("identity") or {}, ident):
                e["outcome"] = outcome if outcome == "published" else e.get("outcome", outcome)
                e["identity"] = {k: sorted(set(e["identity"].get(k, []) + v))
                                 if k in e.get("identity", {}) else v
                                 for k, v in ident.items()}
                e["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if published_url:
                    e["published_url"] = _abs_url(published_url)
                if note:
                    e["note"] = note
                _save(entries)
                return e
        entry = {"name": name, "author": author, "source_url": source_url,
                 "published_url": _abs_url(published_url), "outcome": outcome,
                 "note": note, "tags": list(tags or []), "identity": ident,
                 "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        entries.append(entry)
        _save(entries)
        return entry


def _abs_url(u: str) -> str:
    u = (u or "").strip()
    if u and not u.startswith("http"):
        return mw.ORIGIN + (u if u.startswith("/") else "/" + u)
    return u


def find(*, name: str = "", author: str = "", source_url: str = "",
         published_url: str = "", design_id: str = "", file_path: str = "") -> dict:
    """{found, entry, how} — is this model already on MakerWorld?"""
    ident = _identity(name=name, author=author, source_url=source_url,
                      published_url=published_url, design_id=design_id,
                      file_path=file_path)
    for e in _load():
        ei = e.get("identity") or {}
        if e.get("outcome") != "published":
            continue
        for k in ("source_urls", "published_urls", "design_ids", "name_author", "file_digests"):
            if ident.get(k) and ei.get(k) and set(ident[k]) & set(ei[k]):
                return {"found": True, "entry": e, "how": k}
    return {"found": False, "entry": None, "how": ""}


def describe(hit: dict) -> str:
    e = hit.get("entry") or {}
    return (f"'{e.get('name') or '?'}' is already published on MakerWorld"
            + (f" at {e.get('published_url')}" if e.get("published_url") else "")
            + f" (matched by {hit.get('how') or 'identity'}"
            + (f", recorded {e.get('recorded_at')}" if e.get("recorded_at") else "") + ")")


def forget(*, match: str = "") -> dict:
    """Make one entry publishable again — by listing url, design id, source url
    or name. Deliberately not wired to the dashboard."""
    m = _norm_url(match) or _name_key(match)
    if not m:
        return {"removed": 0}
    with _LOCK:
        entries = _load()
        keep, removed = [], 0
        for e in entries:
            ei = e.get("identity") or {}
            flat = {*(ei.get("source_urls") or []), *(ei.get("published_urls") or []),
                    *(ei.get("design_ids") or []), *(ei.get("name_author") or [])}
            if m in flat or _name_key(match) in (ei.get("name_author") or []):
                removed += 1
            else:
                keep.append(e)
        if removed:
            _save(keep)
        return {"removed": removed}


def all_entries() -> list[dict]:
    return _load()
