"""Gate R9: "find N models" ticks the licence filter the user circled, reads it
back, and only then harvests — most-downloaded first.

Runs the real discovery against the live listing (through the logged-in debug
Chrome) and fails loudly if the ticked set is not exactly

    ON  : CC0, CC BY, CC BY-SA          (the only licences this account may
                                         re-publish from — redistribution,
                                         remixing AND commercial use allowed)
    OFF : CC BY-NC, CC BY-NC-SA, CC BY-ND, CC BY-NC-ND, Standard License

or if fewer than N distinct model URLs come back. A run that cannot verify the
filter must never hand models to the publisher, so this is the gate for that.

Usage: .venv/Scripts/python.exe scripts/check_discovery.py [count] [keyword ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discover import (CREALITY_LICENCE_ALLOW, CREALITY_LICENCE_DENY,  # noqa: E402
                      find_creality)


def _safe(text: str) -> str:
    """Model names carry emoji/CJK; the Windows console is cp1252 and would
    otherwise crash the gate (hiding its real result)."""
    return (text or "").encode("ascii", "replace").decode("ascii")


def main() -> int:
    try:                                        # keep the gate printable
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    count = int(args[0]) if args and args[0].isdigit() else 4
    keywords = args[1:] if len(args) > 1 else ["cable organizer"]

    print(f"finding {count} model(s) for {keywords} …")
    out = find_creality(keywords, count)
    lic = out.get("licence") or {}
    models = out.get("models") or []

    print("filters opened :", lic.get("opened"))
    print("applied with   :", lic.get("applied_with"))
    print("ticked ON      :", lic.get("ticked_on"))
    print("ticked OFF     :", lic.get("ticked_off"))
    print("note           :", out.get("note") or "(none)")
    print("models found   :", len(models))
    for m in models:
        print("   ", _safe((m.get("name") or "")[:52]).ljust(52), m.get("url", ""))

    problems = []
    if not lic.get("opened"):
        problems.append("Filters panel never opened")
    if not lic.get("verified"):
        problems.append("licence filter did not read back as the allow-list")
    for want in CREALITY_LICENCE_ALLOW:
        if not (lic.get("state") or {}).get(want):
            problems.append("allow-listed licence not ticked: " + want)
    for deny in CREALITY_LICENCE_DENY:
        if (lic.get("state") or {}).get(deny):
            problems.append("deny-listed licence was ticked: " + deny)
    if len(models) < count:
        problems.append("only %d model(s) returned for count=%d" % (len(models), count))
    urls = [m.get("url", "") for m in models]
    if len(set(urls)) != len(urls):
        problems.append("duplicate model URLs in the queue")
    if any("/model-detail/" not in u for u in urls):
        problems.append("a harvested URL is not a model page")

    if problems:
        print("\nFAIL R9: " + "; ".join(problems))
        return 1
    print("\nPASS R9: licence filter verified (3 ticked / 5 unticked) and "
          "%d model(s) harvested, most-downloaded first" % len(models))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
