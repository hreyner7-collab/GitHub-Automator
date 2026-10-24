"""The originals shelf: finished previews, and how a run picks the next models.

Nothing here opens a browser, reads an account or uploads anything. A model is
built from its own seeded spec and photographed from the exact STL written
beside it, so the shelf can only ever show work that is ours.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from origin import build, printcheck, shots, spec

KEY_RE = re.compile(r"^([a-z_]+)-(\d{1,3})-([0-9a-f]{8})$")
RES = 1000


class PrepareError(RuntimeError):
    pass


def read_record(model_dir: Path) -> dict | None:
    hits = sorted(Path(model_dir).glob("*_photos.json"))
    if len(hits) != 1:
        return None
    try:
        rec = json.loads(hits[0].read_text(encoding="utf-8"))
    except Exception:
        return None
    return rec if isinstance(rec, dict) else None


def is_complete(model_dir: Path) -> bool:
    """A preview is complete only when every file the publisher needs is there.

    `shots.make_photos` writes the record last, so an interrupted run leaves a
    folder that must never be shown as ready.
    """
    rec = read_record(model_dir)
    if not rec:
        return False
    contact = Path(rec.get("contact") or "")
    photos = [Path(p) for p in rec.get("photos_4x3") or []]
    if len(photos) != 5 or not contact.is_file():
        return False
    return all(p.is_file() and p.stat().st_size >= 5000 for p in photos)


def complete_previews(root: Path) -> list[dict]:
    root = Path(root)
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        if not d.is_dir() or not KEY_RE.match(d.name):
            continue
        rec = read_record(d)
        if not rec or not is_complete(d):
            continue
        s = rec.get("spec") or {}
        match = KEY_RE.match(d.name)
        metrics = ((rec.get("check") or {}).get("metrics") or {})
        out.append({
            "key": d.name,
            "family": s.get("family") or match.group(1),
            "variant": int(s.get("variant") or match.group(2)),
            "title": s.get("title") or d.name,
            "contact": str(Path(rec.get("contact") or "")),
            "photos": [str(Path(p)) for p in rec.get("photos_4x3") or []],
            "dims_mm": metrics.get("dims_mm") or [],
            "seconds": (rec.get("render") or {}).get("wall_seconds"),
            "licence": rec.get("licence") or "",
            "stl": (rec.get("files") or {}).get("stl") or "",
            "approved": (d / "APPROVED.json").is_file(),
            "mtime": d.stat().st_mtime,
        })
    out.sort(key=lambda p: (p["mtime"], p["key"]))
    return out


def published_marker(model_dir: Path) -> dict:
    path = Path(model_dir) / "PUBLISHED.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_published(model_dir: Path, url: str, title: str, when: str) -> dict:
    rec = {"key": Path(model_dir).name, "title": title, "url": url,
           "at": when, "licence": "CC BY 4.0"}
    (Path(model_dir) / "PUBLISHED.json").write_text(
        json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def discard(model_dir: Path) -> bool:
    """Remove one preview folder; a published original is never removed."""
    d = Path(model_dir)
    if published_marker(d):
        return False
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return True


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _existing_variants(root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    root = Path(root)
    if not root.is_dir():
        return out
    for d in root.iterdir():
        match = KEY_RE.match(d.name)
        if match:
            family, variant = match.group(1), int(match.group(2))
            out[family] = max(out.get(family, 0), variant)
    return out


def families_for(count: int, root: Path) -> list[str]:
    """Round-robin the families, rotating the start with the shelf so a new run
    does not open with the same object type as the last one."""
    names = list(spec.FAMILIES)
    if count <= 0 or not names:
        return []
    start = len(complete_previews(root)) % len(names)
    return [names[(start + i) % len(names)] for i in range(count)]


def prepare(root: Path, family: str, samples: int | None = None,
            res: int = RES, engine: str = shots.DEFAULT_ENGINE,
            tries: int = 16) -> dict:
    """Build the next printable variant of `family` and photograph it.

    A variant whose title is already on the shelf is skipped: two listings with
    one name is a duplicate the novelty check would refuse later. A folder left
    by an interrupted run is removed before rendering, so a half-model is never
    shown as ready.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    taken = {_title_key(p["title"]) for p in complete_previews(root)}
    start = _existing_variants(root).get(family, 0) + 1
    problems = []
    for variant in range(start, start + tries):
        s = spec.make_spec(family, variant)
        if _title_key(s.title) in taken:
            problems.append("%s: title already on the shelf" % s.key)
            continue
        try:
            mesh = build.build(s)
            check = printcheck.check(mesh, s)
        except Exception as e:
            problems.append("%s: %s" % (s.key, str(e)[:120]))
            continue
        if not check.ok:
            problems.append("%s: %s" % (s.key, "; ".join(check.issues)))
            continue
        out = root / s.key
        if out.exists() and not is_complete(out):
            shutil.rmtree(out)
        dims = [round(float(v), 1) for v in (mesh.bounds[1] - mesh.bounds[0])]
        report = shots.make_photos(mesh, s, check, dims, out,
                                   samples=samples, res=res, engine=engine)
        return {"key": s.key, "family": family, "variant": variant,
                "title": s.title, "contact": report["contact"],
                "photos": report["photos_4x3"],
                "seconds": (report.get("render") or {}).get("wall_seconds"),
                "check": check.as_dict()}
    raise PrepareError("no printable %s variant in %d tries (%s)"
                       % (family, tries,
                          "; ".join(problems[-3:]) or "all titles taken"))
