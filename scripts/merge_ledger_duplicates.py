"""One row per model in artifacts/published_models.json.

Repairs the duplicate the 2026-10-08 dashboard restart created: a model published
under Creality's opaque /model-detail/<id> link was seeded back from the account
under its readable /model-detail/<slug> form and became a SECOND row, so the
ledger counted 27 rows for 26 models.

  1. snapshot — every row's identities (links, slugs, name, designer, file digest)
  2. resolve each still-opaque published link once (Creality's own 301) and
     remember the readable spelling as an alias of that row
  3. merge rows that are the same model, keeping the EARLIEST published date,
     every spelling, both names and the file digest — nothing is deleted
  4. prove it: every identity that existed before still marks a row, every row is
     still recognised, and the count fell by exactly the rows that were folded

Usage:
  .venv/Scripts/python.exe scripts/merge_ledger_duplicates.py [--dry-run]

--dry-run works on a COPY of the ledger, so it shows the real plan and touches
nothing. Exit 0 only when the merge is clean (or nothing needed merging).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import ledger  # noqa: E402

DRY = "--dry-run" in sys.argv[1:]
REAL = ledger.ledger_path()

if DRY:
    copy = Path(tempfile.mkdtemp(prefix="ledger-dry-")) / ledger.LEDGER_NAME
    shutil.copyfile(REAL, copy)
    os.environ["CREALITY_LEDGER"] = str(copy)
    print("dry run on a copy of the ledger:", copy)


def identities() -> dict:
    """Every distinct identity in the file, as flat sets, for the before/after
    comparison — links, slugs, name keys and file digests."""
    out = {"urls": set(), "slugs": set(), "names": set(), "files": set(),
           "rows": []}
    for e in ledger.entries():
        ident = ledger._identity(e)
        out["urls"] |= ident["urls"]
        out["slugs"] |= ident["slugs"]
        out["names"] |= ident["names"]        # every title the row is known by
        if ident["file"]:
            out["files"].add(ident["file"])
        out["rows"].append({"slug": e.get("slug") or "",
                            "name": e.get("name") or "",
                            "published_at": e.get("published_at") or "",
                            "urls": sorted(ident["urls"]),
                            "slugs": sorted(ident["slugs"]),
                            "aliases": list(e.get("aliases") or [])})
    return out


def digest() -> str:
    p = ledger.ledger_path()
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


before_sha, before = digest(), identities()
print("ledger     ", REAL if not DRY else "(copy)")
print("before     ", len(before["rows"]), "row(s) · sha256", before_sha[:16])

# ── 1) learn the readable spelling of every opaque published link ────────────
learned = ledger.learn_published_aliases()
for a in learned:
    print("  alias     %-26s -> %s"
          % (a["from"].rsplit("/", 1)[-1], a["to"].rsplit("/", 1)[-1]),
          "| " + a["entry"])

# ── 2) rows that are the same model ─────────────────────────────────────────
plan = ledger.merge_duplicates(dry_run=True)
print("would fold ", len(plan["merged"]), "duplicate row(s):")
for m in plan["merged"]:
    print("   keep %-32s (%s)  fold %-32s (%s)"
          % (m["kept"], m["published_at"], m["folded"], m["folded_at"]))

if not plan["merged"]:
    print("\nOK: already one row per model — nothing to merge")
    (BASE / "debug" / "ledger_merge_report.json").write_text(json.dumps(
        {"before_rows": len(before["rows"]), "after_rows": ledger.count(),
         "before_sha": before_sha, "learned_aliases": learned, "merged": [],
         "dry_run": DRY}, indent=1, ensure_ascii=False), encoding="utf-8")
    raise SystemExit(0)

# ── 3) merge (on the real file; on the copy when this is a dry run) ─────────
done = ledger.merge_duplicates()

after_sha, after = digest(), identities()
problems = []
for kind in ("urls", "slugs", "names", "files"):
    lost = before[kind] - after[kind]
    if lost:
        problems.append("identity %s lost: %s" % (kind, sorted(lost)[:4]))
for row in before["rows"]:
    hit = ledger.find(source_url=(row["urls"] or [""])[0],
                      aliases=row["urls"] or None)
    if not hit["found"]:
        problems.append("row no longer recognised: " + (row["slug"] or row["name"]))
expected = len(before["rows"]) - len(done["merged"])
if len(after["rows"]) != expected:
    problems.append("row count %d, expected %d" % (len(after["rows"]), expected))

report = {"dry_run": DRY, "before_rows": len(before["rows"]),
          "after_rows": len(after["rows"]), "before_sha": before_sha,
          "after_sha": after_sha, "learned_aliases": learned,
          "merged": done["merged"],
          "identities_before": {k: sorted(v) for k, v in before.items()
                                if isinstance(v, set)},
          "identities_after": {k: sorted(v) for k, v in after.items()
                               if isinstance(v, set)},
          "problems": problems}
(BASE / "debug" / "ledger_merge_report.json").write_text(
    json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

print("after      ", len(after["rows"]), "row(s) · sha256", after_sha[:16])
for m in done["merged"]:
    print("  folded    %s -> %s | published_at kept %s"
          % (m["folded"], m["kept"], m["published_at"]))
print("\nwrote debug/ledger_merge_report.json")
if problems:
    print("PROBLEMS:")
    for p in problems:
        print("  -", p)
    raise SystemExit(1)
print("OK: one row per model, every identity kept, %d duplicate row(s) folded%s"
      % (len(done["merged"]), "  (dry run — the real ledger was NOT written)"
         if DRY else ""))
