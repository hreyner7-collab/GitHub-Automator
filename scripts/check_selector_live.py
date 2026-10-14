"""Live proof of the household gate: run the real finder against the user's own
object families and print every candidate it kept and every one it turned down,
with the reason the gate gave.

Read-only in the strongest sense: `find_creality` searches Creality and reads
listing cards — it never downloads, never edits, and never touches the upload
form. Nothing is published by this script.

Exit status
  0  the live listing was harvested and every kept model passed the gate again
  1  the live check could not run (browser, or the licence filter did not verify)
  2  the gate was violated: a model the finder handed over is one the gate
     refuses — the invariant this whole change exists for

    .venv/Scripts/python.exe scripts/check_selector_live.py --count 3
    .venv/Scripts/python.exe scripts/check_selector_live.py --keywords "cable organizer"
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import selector  # noqa: E402
from discover import find_creality  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=3,
                    help="how many publishable models to look for (default 3)")
    ap.add_argument("--keywords", default="",
                    help="comma-separated searches; default = the user's families")
    ap.add_argument("--json", default=str(ROOT / "debug" / "selector_live.json"))
    args = ap.parse_args(argv)

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] \
        or list(selector.DEFAULT_KEYWORDS)
    print("household gate, live")
    print("  searched for: " + ", ".join(keywords))
    print("  families    : " + " · ".join(f["label"] for f in selector.policy()["families"]))
    print("  refused     : " + " · ".join(r["reason"] for r in selector.policy()["rejects"]))
    print()

    started = time.time()
    disc = find_creality(keywords, args.count)
    took = time.time() - started

    models = disc.get("models") or []
    rejected = disc.get("rejected") or []
    total_rejected = disc.get("rejected_total") or len(rejected)
    lic = disc.get("licence") or {}

    print("licence filter : " + (("verified on the live listing (ticked: %s)"
                                 % ", ".join(lic.get("ticked_on") or []))
                                if lic.get("verified") else
                                ("NOT verified: ticked on %s, off %s"
                                 % (", ".join(lic.get("ticked_on") or []) or "-",
                                    ", ".join(lic.get("ticked_off") or []) or "-"))))
    if disc.get("note"):
        print("note          : " + str(disc["note"]))
    print("took          : %.0fs" % took)
    print()

    print("KEPT — %d model(s) the run is allowed to publish:" % len(models))
    for m in models:
        again = selector.allow(m.get("name") or "", m.get("url") or "")
        mark = "ok " if again["allowed"] else "GATE VIOLATION"
        print("  [%s] %-44s %s" % (mark, (m.get("name") or "")[:44], again["label"]))
        print("        %s" % (m.get("url") or ""))
    print()

    print("TURNED DOWN — %d candidate(s) shown of %d judged (nothing here is ever "
          "fetched or published):" % (len(rejected), total_rejected))
    by_source = {}
    for r in rejected:
        by_source.setdefault(r.get("source", "?"), []).append(r)
    for source in sorted(by_source):
        print("  %s (%d):" % (source, len(by_source[source])))
        for r in by_source[source][:12]:
            print("    - %-42s %s" % ((r.get("name") or "")[:42], r.get("reason")))
        if len(by_source[source]) > 12:
            print("    … and %d more" % (len(by_source[source]) - 12))
        print()

    evidence = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "keywords": keywords, "requested": args.count,
                "kept": [{**m, "verdict": selector.allow(m.get("name") or "",
                                                          m.get("url") or "")}
                         for m in models],
                "rejected": rejected, "rejected_total": total_rejected,
                "licence": lic, "note": disc.get("note") or "",
                "took_seconds": round(took, 1)}
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    print("evidence: " + str(out))

    violations = [m for m in models
                  if not selector.allow(m.get("name") or "", m.get("url") or "")["allowed"]]
    if violations:
        print("\nGATE VIOLATION: %d model(s) the gate refuses were handed over by the "
              "finder: %s" % (len(violations),
                              ", ".join(m.get("name") or "?" for m in violations)))
        return 2
    if not lic.get("verified"):
        print("\ncould not verify against the live listing — nothing was harvested")
        return 1
    print("\nOK: %d publishable model(s), %d turned down, none kept that the gate "
          "refuses." % (len(models), total_rejected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
