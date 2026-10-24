"""The pipeline's permanent memory of what it has already published.

The user's standing rule: NEVER publish the same model twice — not in a
later run, not after a restart, not under a different link.

Creality's own duplicate check is not enough: it only fires AFTER the whole
form has been filled and submitted, and it cannot see a model that is still
"under review". So the pipeline keeps its own ledger on disk and consults it
BEFORE anything is uploaded.

A model is recognised by any one of four fingerprints (each is checked):

  url    the source link, normalised          https://www.x.com/m/foo/?a=1
                                              and  x.com/m/foo  are the same
  slug   the listing's last path segment      a re-listed model with a new
                                              URL still carries its slug
  name   the model's name + its designer      "Toothbrush stand by IceMarine"
  file   sha256 of the model FILE's bytes     the same geometry re-listed
                                              under another name/URL

ONE MODEL HAS SEVERAL LINKS, and all of them count. A publish answers with an
opaque /model-detail/<id> that Creality 301s to the readable /model-detail/<slug>,
and the account's own model list uses that slug — so an entry keeps the source
URL, the published URL, their slugs and every alias ever learned, and a lookup
matches when ANY of them is equal. Skipping that is how one pallet jack ended up
as two rows (27 rows for 26 models, 2026-10-08): the restart's account seed saw
/model-detail/scale-model-pallet-jack-1-10, the entry only knew
/model-detail/scale-model-pallet-jack and .../6ac7e100f5ed98d3279fc843, and
nothing matched. `canonical_url()` follows that redirect (plain HTTP) and
`merge_duplicates()` folds any pair that slipped through into one row —
keeping the earliest published date, both names, every spelling and the file
digest, never deleting history.

Anything in here is refused for upload, with the date and the reason. Nothing
expires by itself: if you deliberately want to publish something again (you
deleted the listing, say), forget that entry in the dashboard panel.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent

LEDGER_NAME = "published_models.json"
VERSION = 1

# Read at least this much of a model file when fingerprinting it. Creality 3MFs
# are well under this; a huge STL is identified by its first 32 MiB + its size.
DIGEST_BYTES = 32 * 1024 * 1024

# A slug shorter than this is not a reliable identity (catalogue junk like "100",
# "gif" or "toilet" appears as a slug on unrelated models), so it is not used on
# its own — those models are still covered by url / name / file fingerprints.
MIN_SLUG = 8

# Same idea for a name when neither side knows the designer: a short, generic
# title ("Toilet", "GIF") must not stand in for a model on its own.
MIN_NAME = 8

_LOCK = threading.RLock()


# ── where the ledger lives ───────────────────────────────────────────────────

def ledger_path() -> Path:
    """artifacts/published_models.json, or $CREALITY_LEDGER when that is set
    (tests point it at a tmp dir)."""
    override = os.environ.get("CREALITY_LEDGER")
    if override:
        return Path(override)
    return BASE_DIR / "artifacts" / LEDGER_NAME


# ── fingerprints ─────────────────────────────────────────────────────────────

def normalise_url(url: str) -> str:
    """A link's identity: no scheme, no www., no query, no fragment, no
    trailing slash, lowercased host — so every spelling of one link matches."""
    u = (url or "").strip()
    if not u:
        return ""
    if "@" in u.split("/")[0]:                  # a URL never keeps a userinfo
        u = u.split("@", 1)[1]
    parts = urlsplit(u if "//" in u else "//" + u)
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parts.path or "").rstrip("/")
    # the site's locale prefixes drop out too /en/model-detail/x and
    # /fr/model-detail/x are THE SAME listing, so one published under one
    # spelling is never re-published via the other
    path = re.sub(r"^/(?:en|fr|de|es|it|pt|nl|ru|ja|ko|zh)(?=/)", "", path)
    return (host + path).lower()


def slug_of(url: str) -> str:
    """The listing's last path segment, e.g. …/model-detail/toothbrush-stand."""
    u = (url or "").strip()
    if not u:
        return ""
    path = urlsplit(u if "//" in u else "//" + u).path.rstrip("/")
    last = path.rsplit("/", 1)[-1] if path else ""
    return re.sub(r"[^a-z0-9]+", "-", last.lower()).strip("-")


def name_key(name: str) -> str:
    """A name's identity: case, spacing and punctuation dropped. Unicode-aware,
    so 'Домкрат для подъёма поддонов' keeps its own identity instead of
    collapsing to an empty string."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def file_digest(path: str) -> str:
    """"sha256:<hex>:<size>' for the model FILE — the strongest fingerprint:
    the same geometry re-listed under a different name and URL still matches."""
    if not path:
        return ""
    p = Path(path)
    try:
        if not p.is_file():
            return ""
        size = p.stat().st_size
        h = hashlib.sha256()
        read = 0
        with p.open("rb") as fh:
            while read < DIGEST_BYTES:
                chunk = fh.read(min(1024 * 1024, DIGEST_BYTES - read))
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
        return "sha256:%s:%d" % (h.hexdigest(), size)
    except OSError:
        return ""


def fingerprints(source_url: str = "", name: str = "", author: str = "",
                 slug: str = "", file_path: str = "") -> dict:
    """Every fingerprint we can compute for one candidate model."""
    return {
        "url": normalise_url(source_url),
        "slug": slug_of(slug or source_url),
        "name": name_key(name),
        "author": name_key(author),
        "file": file_digest(file_path),
    }


def _usable(kind: str, value: str, other: dict | None = None) -> bool:
    if not value:
        return False
    if kind == "slug":
        return len(value) >= MIN_SLUG
    if kind == "name":
        # A bare name is only identity together with its designer — two
        # different designers both making a "Soap dish" are different models.
        return bool(value) and bool((other or {}).get("author"))
    return bool(value)


# ── storage ──────────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {"version": VERSION, "entries": []}


def load() -> dict:
    """The ledger as a dict. A missing file is an empty ledger; an unreadable
    file is kept (renamed .corrupt) rather than silently thrown away."""
    path = ledger_path()
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            path.replace(path.with_suffix(".corrupt.json"))
        except OSError:
            pass
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return _empty()
    data.setdefault("version", VERSION)
    return data


def _save(data: dict, base: list | None = None) -> None:
    """Write the ledger, merging it into whatever is on disk right now.

    Every writer does load -> mutate -> save, and the only lock here is a
    threading lock, so two live processes (the dashboard and a run script) both
    read the same file and the later save would otherwise drop the earlier one's
    rows wholesale. Re-read the file at write time and fold our rows over it:
    a row this process never read (a concurrent add) survives, and a row it
    deliberately dropped (``forget`` / ``merge_duplicates``) stays dropped,
    because ``base`` tells us what we actually started from. The JSON carries no
    tombstones, so a row we cannot tell apart from one the OTHER process deleted
    is kept rather than removed — never lose another process's history.
    """
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ours = data.get("entries") or []
    known = list(base) if base is not None else list(ours)
    merged = list(ours)
    for entry in load().get("entries") or []:
        ident = _identity(entry)
        if not any(_same_model(ident, _identity(x)) for x in known) \
                and not any(_same_model(ident, _identity(x)) for x in merged):
            merged.append(entry)
    out = {"version": data.get("version", VERSION), "entries": merged}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)                       # atomic: never a half file


def entries() -> list[dict]:
    return load()["entries"]


def count() -> int:
    return len(entries())


# ── asking "have I published this already?" ─────────────────────────────────

MATCH_LABELS = {
    "url": "the same model link",
    "slug": "the same Creality listing",
    "file": "the same model file (identical geometry)",
    "name": "the same name and designer",
}


def _identity(entry: dict) -> dict:
    """Every identity one entry carries: each spelling of its link (source,
    published, and every learned alias) as normalised URLs AND as slugs, plus
    the name + designer and the model file's digest. Sets, because one model has
    several links — see the module docstring."""
    fp = entry.get("fingerprints") or {}
    urls: set = set()
    slugs: set = set()
    for u in (entry.get("source_url"), entry.get("published_url"),
              fp.get("url"), *(entry.get("aliases") or [])):
        n = normalise_url(u)
        if n:
            urls.add(n)
        s = slug_of(u)
        if s:
            slugs.add(s)
    s = (fp.get("slug") or "").strip()
    if s:
        slugs.add(s)
    # every name the model is known by, not just the first one: a merge keeps the
    # other title in `also_named`, and that title is still an identity
    names = {n for n in [fp.get("name") or ""]
             + [name_key(x) for x in (entry.get("also_named") or [])] if n}
    return {"urls": urls, "slugs": slugs, "names": names,
            "name": fp.get("name", ""), "author": fp.get("author", ""),
            "file": fp.get("file", "")}


# the old name for the same reader, kept so nothing outside this file changes
def _entry_values(entry: dict) -> dict:
    return _identity(entry)


def _candidate_values(source_url: str = "", name: str = "", author: str = "",
                      slug: str = "", file_path: str = "",
                      aliases=None) -> dict:
    """The same identity shape for a model we are ASKING about."""
    return _identity({"source_url": source_url, "published_url": "",
                      "aliases": list(aliases or []),
                      "fingerprints": fingerprints(source_url=source_url,
                                                   name=name, author=author,
                                                   slug=slug,
                                                   file_path=file_path)})


def _same_model(want: dict, have: dict) -> str:
    """Which fingerprint says these are the same model ('' = none does).

    A link counts only when the URLs are EQUAL once normalised, and a slug only
    when it is long enough to be an identity — a look-alike slug
    ("organizer-box" vs "xl-organizer-box") is a DIFFERENT model, and merging
    those would silently make a model un-publishable."""
    if want["urls"] & have["urls"]:
        return "url"
    if any(len(s) >= MIN_SLUG for s in (want["slugs"] & have["slugs"])):
        return "slug"
    if want["file"] and want["file"] == have["file"]:
        return "file"
    if _same_name_model(want, have):
        return "name"
    return ""


def _same_name_model(want: dict, have: dict) -> bool:
    """Same model by name: ANY name both sides know must match, and two KNOWN
    designers must agree (a model imported from your own account has no designer
    recorded, so there the name is what we go on — but only when it is
    distinctive enough)."""
    aw, bw = want.get("author", ""), have.get("author", "")
    if aw and bw and aw != bw:
        return False                    # different designers, same title
    shared = {n for n in (want.get("names") or set())
              & (have.get("names") or set()) if n}
    if not shared:
        return False
    if aw and bw:
        return True
    return any(len(n) >= MIN_NAME for n in shared)


def find(source_url: str = "", name: str = "", author: str = "",
         slug: str = "", file_path: str = "", aliases=None) -> dict:
    """Is this model already in the ledger?

    Returns {"found": bool, "matched_on": <url|slug|file|name|"">, "entry": {...}}
    — a hit means: do NOT upload it again. Any spelling of the model's link is
    enough: the source link, the published link, its slug, or a learned alias.
    """
    want = _candidate_values(source_url=source_url, name=name, author=author,
                             slug=slug, file_path=file_path, aliases=aliases)
    with _LOCK:
        for entry in entries():
            kind = _same_model(want, _identity(entry))
            if kind:
                return {"found": True, "matched_on": kind, "entry": entry}
    return {"found": False, "matched_on": "", "entry": {}}


def describe(hit: dict) -> str:
    """A sentence the dashboard can show verbatim."""
    entry = hit.get("entry") or {}
    why = MATCH_LABELS.get(hit.get("matched_on") or "", "an earlier publish")
    when = entry.get("published_at") or entry.get("recorded_at") or "earlier"
    label = entry.get("name") or entry.get("slug") or "a model"
    where = entry.get("published_url") or entry.get("source_url") or ""
    out = ("Already published on %s as “%s” — matched on %s, so it was NOT "
           "uploaded again." % (when, label, why))
    if where:
        out += " It is here: " + where
    if entry.get("outcome") and entry["outcome"] != "published":
        out += " (Creality reported it as a duplicate on %s.)" % when
    return out


def _spellings(*urls) -> list:
    """The distinct links, in the order first seen (blank ones dropped)."""
    out, seen = [], set()
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _merge_into(entry: dict, fp: dict, *, name: str = "", author: str = "",
                source_url: str = "", published_url: str = "",
                aliases=None, outcome: str = "", when: str = "") -> dict:
    """Fold one model's identities into an existing entry. Nothing is lost: a
    richer fingerprint never overwrites a known one, EVERY spelling of the link
    is kept (that is what stops a later seed from missing the model), both names
    are kept, and the EARLIEST published date survives — a seed that arrives
    after the fact must not re-date a model that was published days ago."""
    merged = dict(entry.get("fingerprints") or {})
    for k, v in (fp or {}).items():
        if v and not merged.get(k):
            merged[k] = v
    entry["fingerprints"] = merged
    entry["aliases"] = _spellings(*(entry.get("aliases") or []),
                                  entry.get("source_url"),
                                  entry.get("published_url"),
                                  source_url, published_url,
                                  *(aliases or []))
    if source_url and not entry.get("source_url"):
        entry["source_url"] = source_url
    if published_url and not entry.get("published_url"):
        entry["published_url"] = published_url
    if name and not entry.get("name"):
        entry["name"] = name
    elif name and name != entry.get("name"):
        # the account lists the model under its own title — keep that too
        also = entry.setdefault("also_named", [])
        if name not in also:
            also.append(name)
    if author and not entry.get("author"):
        entry["author"] = author
    if outcome == "published" or not entry.get("outcome"):
        entry["outcome"] = outcome or entry.get("outcome") or "published"
    stamps = [s for s in (entry.get("published_at"), when) if s]
    if stamps:
        entry["published_at"] = min(stamps)
    recorded = [s for s in (entry.get("recorded_at"), when) if s]
    if recorded:
        entry["recorded_at"] = min(recorded)
    if when:
        entry["last_seen"] = when
    return entry


def record(name: str = "", source_url: str = "", author: str = "",
           slug: str = "", stl_path: str = "", published_url: str = "",
           outcome: str = "published", note: str = "",
           aliases=None) -> dict:
    """Remember one model for good. Re-recording the same model — under any of
    its links — merges into the existing entry instead of adding a second row."""
    fp = fingerprints(source_url=source_url, name=name, author=author,
                      slug=slug, file_path=stl_path)
    want = _candidate_values(source_url=source_url, name=name, author=author,
                             slug=slug, file_path=stl_path, aliases=aliases)
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        data = load()
        base = list(data["entries"])            # what we read, before we changed it
        for entry in data["entries"]:
            if _same_model(want, _identity(entry)):
                _merge_into(entry, fp, name=name, author=author,
                            source_url=source_url, published_url=published_url,
                            aliases=aliases, outcome=outcome, when=when)
                _save(data, base=base)
                return entry
        entry = {"name": name, "author": author or "",
                 "slug": fp["slug"] or slug_of(source_url),
                 "source_url": source_url, "published_url": published_url,
                 "outcome": outcome, "note": note, "fingerprints": fp,
                 "aliases": _spellings(source_url, published_url,
                                       *(aliases or [])),
                 "published_at": when, "recorded_at": when, "last_seen": when}
        data["entries"].append(entry)
        _save(data, base=base)
        return entry


def learn_listing(source_url: str = "", names=(), author: str = "",
                  slug: str = "", file_path: str = "") -> dict:
    """A listing that turned out, only once its page was open, to be a model we
    already published. `find()` could only see that after the page was read, so
    remember the listing's link and the titles the card and the page show as
    spellings of that entry. The next run's card check then refuses the listing
    BEFORE its page is opened again. Returns the entry, or {} if none matches."""
    names = [n for n in dict.fromkeys(names or ()) if n]
    with _LOCK:
        data = load()
        base = list(data["entries"])            # what we read, before we changed it
        for entry in data["entries"]:
            have = _identity(entry)
            for n in (names or [""]):
                want = _candidate_values(source_url=source_url, name=n,
                                         author=author, slug=slug,
                                         file_path=file_path)
                if not _same_model(want, have):
                    continue
                fp = fingerprints(source_url=source_url, name=n, author=author,
                                  slug=slug, file_path=file_path)
                _merge_into(entry, fp, name=n, author=author,
                            source_url=source_url, aliases=[source_url],
                            outcome="", when="")
                for other in names:
                    if other != n:
                        _merge_into(entry, {}, name=other, outcome="", when="")
                _save(data, base=base)
                return entry
    return {}


def forget(match: str) -> int:
    """Deliberately drop entries (a listing you deleted and want to re-publish).
    Matches an entry's source link, published link, slug, name or file digest."""
    keys = set(filter(None, (normalise_url(match), slug_of(match),
                             name_key(match), (match or "").strip())))
    if not keys:
        return 0
    with _LOCK:
        data = load()
        base = list(data["entries"])            # what we read, before we dropped it
        keep, dropped = [], 0
        for entry in data["entries"]:
            ident = _identity(entry)
            have = set(filter(None, ident["urls"] | ident["slugs"]
                              | ident["names"]
                              | {ident["name"], ident["author"], ident["file"]}))
            have |= {(entry.get("source_url") or "").strip(),
                     (entry.get("published_url") or "").strip()}
            if keys & have:
                dropped += 1
                continue
            keep.append(entry)
        if dropped:
            data["entries"] = keep
            _save(data, base=base)              # dropped rows stay dropped
        return dropped


# ── one row per model: canonical links + merging what slipped through ──────

def canonical_url(url: str, timeout: float = 12.0, resolver=None) -> str:
    """The link Creality itself sends you to.

    A publish answers with an opaque /model-detail/<id>; Creality 301s that to
the readable /model-detail/<slug>, and the account's own model list uses the
slug. Following the redirect (plain HTTP, no browser) is what turns the two
spellings into ONE identity — the link the pallet jack went missing on.

    Returns the resolved URL, or the input unchanged when it cannot be resolved
    (offline, blocked, or already canonical). Never raises.
    """
    u = (url or "").strip()
    if not u or "model-detail" not in u:
        return u
    if resolver is not None:
        try:
            return (resolver(u) or "").strip() or u
        except Exception:
            return u
    try:
        import urllib.request
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.geturl() or "").strip() or u
    except Exception:
        return u


def looks_opaque(url: str) -> bool:
    """True for Creality's opaque publish URL (/model-detail/<hex id>) — the one
    whose readable /model-detail/<slug> form only a 301 reveals. A link that is
    already readable costs nothing to check, so only these are ever resolved."""
    last = slug_of(url).replace("-", "")
    return bool(last) and len(last) >= 12 and re.fullmatch(r"[0-9a-f]+", last) is not None


def absorb_published_redirect(url: str, resolver=None) -> dict:
    """Is this link the PUBLISHED form of a model already recorded?

    Used by the account seed: when a candidate link is not recognised, every
    entry whose published link is still opaque is resolved once (Creality's own
    301) and, if it lands on this candidate, the candidate becomes an alias of
    that entry and the readable slug becomes its published_url — one model, one
    row. Returns the matched entry, or {}.
    """
    u = (url or "").strip()
    if not u:
        return {}
    want = normalise_url(u)
    want_slug = slug_of(u)
    with _LOCK:
        data = load()
        base = list(data["entries"])            # what we read, before we changed it
        for entry in data["entries"]:
            published = (entry.get("published_url") or "").strip()
            if not published or not looks_opaque(published):
                continue
            resolved = canonical_url(published, resolver=resolver)
            if not resolved or resolved == published:
                continue
            if normalise_url(resolved) == want \
                    or (len(want_slug) >= MIN_SLUG
                        and slug_of(resolved) == want_slug):
                entry["published_url"] = resolved      # the readable slug wins
                _merge_into(entry, {}, published_url=resolved,
                            aliases=[published, u, resolved], when="")
                _save(data, base=base)
                return entry
    return {}


def learn_published_aliases(resolver=None, limit: int = 50) -> list[dict]:
    """Resolve every still-opaque published link ONCE and remember the readable
    /model-detail/<slug> spelling as an alias of its own entry — the 301 Creality
    serves itself, so no guessing is involved. This is what a row recorded before
    2026-10-08 is missing: without it, a later account seed sees a link the entry
    has never heard of. Returns the aliases learned ([] when there are none)."""
    learned: list[dict] = []
    with _LOCK:
        data = load()
        base = list(data["entries"])            # what we read, before we changed it
        for entry in data["entries"]:
            if len(learned) >= limit:
                break
            published = (entry.get("published_url") or "").strip()
            if not published or not looks_opaque(published):
                continue
            resolved = canonical_url(published, resolver=resolver)
            if not resolved or resolved == published:
                continue
            entry["published_url"] = resolved
            _merge_into(entry, {}, published_url=resolved,
                        aliases=[published, resolved], when="")
            learned.append({"entry": entry.get("slug") or entry.get("name") or "",
                            "from": published, "to": resolved})
        if learned:
            _save(data, base=base)
    return learned


def merge_duplicates(dry_run: bool = False) -> dict:
    """One row per model — fold any pair that is the same model into ONE row.

    Two rows can only be the same model by the same rules `find()` uses (an equal
    link or slug, the same file digest, or the same name + designer), so a
    look-alike slug is never touched. The survivor is the EARLIER row and
    `_merge_into` keeps everything: both names, every spelling, the file digest
    and the earliest published date. Nothing is deleted.
    """
    with _LOCK:
        data = load()
        rows = data["entries"]
        base = list(rows)                       # what we read, before we folded it
        keep: list[dict] = []
        merged: list[dict] = []
        for entry in rows:
            target = None
            for i, k in enumerate(keep):
                if _same_model(_identity(entry), _identity(k)):
                    target = i
                    break
            if target is None:
                keep.append(entry)
                continue
            first, second = keep[target], entry
            if (second.get("published_at") or "") < (first.get("published_at") or ""):
                first, second = second, first        # the earliest row survives
            merged.append({"kept": first.get("slug") or first.get("name") or "",
                           "folded": second.get("slug") or second.get("name") or "",
                           "published_at": first.get("published_at") or "",
                           "folded_at": second.get("published_at") or ""})
            keep[target] = _merge_into(
                first, second.get("fingerprints") or {},
                name=second.get("name") or "", author=second.get("author") or "",
                source_url=second.get("source_url") or "",
                published_url=second.get("published_url") or "",
                aliases=second.get("aliases") or [],
                outcome=second.get("outcome") or "",
                when=second.get("published_at") or "")
        result = {"before": len(rows), "after": len(keep), "merged": merged,
                  "dry_run": bool(dry_run)}
        if merged and not dry_run:
            data["entries"] = keep
            _save(data, base=base)              # folded-away rows stay gone
        return result


def summary(limit: int = 200) -> dict:
    """What the dashboard shows: how many models it will never upload again."""
    rows = entries()
    newest = sorted(rows, key=lambda e: e.get("published_at") or "",
                    reverse=True)[:limit]
    return {"count": len(rows),
            "published": sum(1 for e in rows
                             if e.get("outcome", "published") == "published"),
            "blocked_by_creality": sum(1 for e in rows
                                       if e.get("outcome") == "creality-duplicate"),
            "path": str(ledger_path()),
            "entries": newest}


# ── seeding: learn what is already on the account ───────────────────────────

def seed_from_account(snaps: list | None = None, resolver=None) -> int:
    """Every model on your Creality account has already been published — import
    them all. Reads the analytics snapshots (the app's own record of your
    account's model list), so this needs no browser.

    A model already recorded MUST NOT gain a second row just because the account
    lists it under its published slug (that is how the pallet jack was counted
twice). So a candidate that is not recognised outright is checked against every
    entry's published link — Creality's own 301 — before anything is added.
    """
    added = 0
    if snaps is None:
        try:
            import analytics
            snaps = analytics.load_snapshots()
        except Exception:
            snaps = []
    seen = {(e.get("published_url") or "").split("?")[0] for e in entries()}
    for s in snaps or []:
        name = getattr(s, "name", None) or (s.get("name") if isinstance(s, dict) else "")
        url = getattr(s, "url", None) or (s.get("url") if isinstance(s, dict) else "")
        url = (url or "").split("?")[0]
        if not url or url in seen:
            continue
        if find(source_url=url, name=name)["found"]:
            continue
        if absorb_published_redirect(url, resolver=resolver):
            seen.add(url)
            continue                     # the same model, already recorded
        record(name=name, source_url=url, published_url=url, outcome="published",
               note="already on your Creality Cloud account")
        seen.add(url)
        added += 1
    return added


def seed_from_runs(runs_dir: str | Path | None = None) -> int:
    """Second source of truth: the run logs already on disk. Any model a past
    run reports as published is remembered (this covers an account the user has
    never refreshed analytics for)."""
    folder = Path(runs_dir) if runs_dir else (BASE_DIR / "artifacts")
    if not folder.is_dir():
        return 0
    added = 0
    for path in sorted(folder.glob("auto_run_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in data.get("items") or []:
            if (it.get("state") or "") != "published":
                continue
            url = it.get("url") or ""
            name = it.get("name") or ""
            if not url and not name:
                continue
            if find(source_url=url, name=name)["found"]:
                continue
            record(name=name, source_url=url, outcome="published",
                   note="logged by run " + path.name)
            added += 1
    return added


def startup_seed() -> dict:
    """Called once when the dashboard starts: the ledger knows your account and
    your past runs before the first upload of the session — and any two rows that
    are really one model are folded back into one BEFORE the account is read, so
    a restart can never leave the count claiming a model twice."""
    try:
        aliased = learn_published_aliases()
        folded = merge_duplicates()
        return {"aliases": aliased, "merged": folded.get("merged") or [],
                "from_account": seed_from_account(),
                "from_runs": seed_from_runs(),
                "count": count()}
    except Exception as e:                  # never keep the dashboard from booting
        return {"error": str(e)[:200], "count": count()}
