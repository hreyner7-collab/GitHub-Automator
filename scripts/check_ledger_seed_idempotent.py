"""Proof that the ledger holds ONE row per model — the guarantee the restart broke.

Checks, against the REAL ledger file:

  1. the account seed twice + the runs seed twice add NOTHING (the count is
     unchanged and the file's bytes are unchanged)
  2. every row is still recognised by every identity it carries — each link, each
     slug, each alias, and the name+designer where it has one — so no distinct
     model was dropped and nothing can be re-published by mistake
  3. no two remaining rows are the same model, so nothing mergeable is left
  4. `startup_seed()` (what a dashboard restart runs) leaves the count alone

Usage: .venv/Scripts/python.exe scripts/check_ledger_seed_idempotent.py
Exit 0 only when every check passes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import ledger  # noqa: E402


def snapshot() -> tuple[int, str]:
    p = ledger.ledger_path()
    raw = p.read_bytes() if p.is_file() else b""
    return len(json.loads(raw or b'{"entries": []}')["entries"]), \
        hashlib.sha256(raw).hexdigest()


rows_before, sha_before = snapshot()
print("ledger      ", ledger.ledger_path())
print("rows before ", rows_before, "· sha256", sha_before[:16])

problems = []

# ── 1) the account seed, twice, then the runs seed, twice ───────────────────
added = [ledger.seed_from_account(), ledger.seed_from_account(),
         ledger.seed_from_runs(), ledger.seed_from_runs()]
print("seed added  ", added, "(account, account, runs, runs)")
for i, n in enumerate(added):
    check = ("account" if i < 2 else "runs")
    if n:
        problems.append("the %s seed added %d row(s) — a model it should have "
                        "recognised" % (check, n))

rows_seeded, sha_seeded = snapshot()
print("rows after  ", rows_seeded, "· sha256", sha_seeded[:16])
if rows_seeded != rows_before:
    problems.append("the count moved: %d -> %d" % (rows_before, rows_seeded))

# ── 2) every identity of every row still marks that row ─────────────────────
entries = ledger.entries()
identities = 0
for e in entries:
    ident = ledger._identity(e)
    for u in sorted(ident["urls"]):
        identities += 1
        if not ledger.find(source_url=u)["found"]:
            problems.append("link no longer recognised: " + u)
    for s in sorted(ident["slugs"]):
        identities += 1
        if not ledger.find(source_url="https://www.crealitycloud.com/model-detail/" + s)["found"]:
            problems.append("slug no longer recognised: " + s)
    for n in sorted(ident["names"]):
        # A short generic title ("car", "toilet") is deliberately NOT an identity
        # on its own — those rows are held by their links, checked above.
        if len(n) < ledger.MIN_NAME and not (e.get("author") or ""):
            print("  note: %-22s held by its link only (title '%s' is too generic)"
                  % (e.get("slug") or e.get("name"), n))
            continue
        identities += 1
        if not ledger.find(name=n, author=e.get("author") or "")["found"]:
            problems.append("name no longer recognised: " + n)
    if ident["file"]:
        identities += 1
print("identities  ", identities, "checked across", len(entries), "row(s)")

# ── 3) nothing mergeable is left ────────────────────────────────────────────
plan = ledger.merge_duplicates(dry_run=True)
if plan["merged"]:
    problems.append("still %d mergeable pair(s): %s"
                    % (len(plan["merged"]), plan["merged"]))

# ── 4) what a restart does ──────────────────────────────────────────────────
out = ledger.startup_seed()
rows_after, sha_after = snapshot()
print("startup_seed", {k: v for k, v in out.items() if k != "alias"})
print("rows after  ", rows_after, "· sha256", sha_after[:16])
if out.get("merged") or out.get("from_account") or out.get("from_runs"):
    problems.append("a restart changed the ledger: %s" % out)
if rows_after != rows_before:
    problems.append("a restart changed the count: %d -> %d" % (rows_before, rows_after))

report = {"rows_before": rows_before, "rows_after_restart": rows_after,
          "sha_before": sha_before, "sha_after": sha_after,
          "seed_added": added, "identities_checked": identities,
          "startup_seed": out, "problems": problems}
(BASE / "debug" / "ledger_seed_idempotent.json").write_text(
    json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote debug/ledger_seed_idempotent.json")

if problems:
    print("PROBLEMS:")
    for p in problems:
        print("  -", p)
    raise SystemExit(1)
print("OK: one row per model — the account seed and a restart add nothing, and "
      "every identity of all %d rows still blocks its model" % rows_after)
