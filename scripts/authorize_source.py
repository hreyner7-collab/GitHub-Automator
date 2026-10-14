"""Authorise a source this account may re-publish — the file creality_rules reads.

Creality's Commercial Creator Program page lists "Re-uploaded models" and
"Unauthorized remixes or derivative works" as NOT ELIGIBLE, and the help centre
asks for proof the work "is your original creation". So a source must be either

  * your own design (then just leave it out — an upload with no source link is
    original work and needs no authorisation), or
  * a model whose creator gave you permission to redistribute it — name it here.

Anything else is refused by name, with the rule quoted, before a browser or the
form is ever touched.

Usage:
  .venv\\Scripts\\python.exe scripts/authorize_source.py <model-url> ["why it is allowed"]
  .venv\\Scripts\\python.exe scripts/authorize_source.py --list
  .venv\\Scripts\\python.exe scripts/authorize_source.py --remove <model-url>
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import creality_rules  # noqa: E402


def _read():
    try:
        return json.loads(creality_rules.AUTHORIZED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"authorized": []}


def _write(data) -> None:
    creality_rules.AUTHORIZED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = creality_rules.AUTHORIZED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(creality_rules.AUTHORIZED_FILE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="model-detail URL (or slug) to allow")
    ap.add_argument("note", nargs="?", default="", help="why it is allowed")
    ap.add_argument("--list", action="store_true", help="show what is authorised")
    ap.add_argument("--remove", metavar="URL", help="remove one source")
    args = ap.parse_args(argv)

    if args.list or (not args.source and not args.remove):
        have = creality_rules.load_authorized()
        print("authorisation file:", creality_rules.AUTHORIZED_FILE)
        if not have:
            print("  (nothing authorised — every re-upload of another listing is refused)")
        for key, note in sorted(have.items()):
            print("  %s%s" % (key, ("  — " + note) if note else ""))
        return 0

    if args.remove:
        key = creality_rules.normalise(args.remove)
        data = _read()
        keep = [e for e in data.get("authorized", [])
                if creality_rules.normalise(e if isinstance(e, str)
                                            else e.get("source_url", "")) != key]
        removed = len(data.get("authorized", [])) - len(keep)
        data["authorized"] = keep
        _write(data)
        print("removed:", removed, "entry(ies) for", key)
        return 0

    if not creality_rules.is_reupload_source(args.source):
        print("That is not another Creality Cloud listing (%s).\n"
              "An upload with no source link is YOUR OWN work and is allowed "
              "already — nothing to authorise." % args.source)
        return 1
    entry = creality_rules.add_authorized(args.source, args.note)
    print("authorised:", entry["source_url"],
          ("— " + entry["note"]) if entry["note"] else "")
    print("file:", creality_rules.AUTHORIZED_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
