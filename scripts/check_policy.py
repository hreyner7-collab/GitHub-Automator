"""Offline gate: Creality's published rules vs the REAL 2026-10-09 takedowns.

The user's run `artifacts/auto_run_20261009-170809.json` published five re-uploads.
Three were removed by Creality ("The model did not pass platform review") and two
survived — measured live on 2026-10-09, recorded in GATES-REUPLOAD.md R15:

    REMOVED : cable organiser                      source licence CC BY
    REMOVED : Modular Desk Organizer               source licence CC BY-NC
    REMOVED : Smartphone Stand with Cable Cutout   source licence CC BY-NC
    LIVE    : Organizer Storage Solution           source licence CC BY
    LIVE    : Hook                                 source licence CC BY-NC

This gate replays THOSE five records — the real name, source link and the real
licence string the run read — through `creality_rules.check()` and asserts:

  1. every one of them is now REFUSED, each by a named rule (never silently
     published): the NC ones by `noncommercial`, the CC BY one — which the
     licence alone cannot explain, and which is exactly why removals continued
     after the licence was hardened — by `source-not-authorized`;
  2. the only way any of them becomes publishable again is a source THIS account
     is authorised to re-publish (the user's own file), a publishable licence, a
     clean name and a real proof built from the model file;
  3. an authorised, CC BY / CC0 source with a proof and a clean name PASSES —
     the gate refuses for the stated rules, not "everything re-uploaded";
  4. the two survivors being live is NOT treated as evidence that CC BY-NC is
     safe: the rule refuses NC regardless.

No browser and no network: the licence strings are the ones the run recorded, and
the licence decision is `downloader.classify_license` (offline).

Usage: .venv\\Scripts\\python.exe scripts/check_policy.py
Exit 0 only when every assertion holds.
"""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from downloader import classify_license          # noqa: E402
import creality_rules                             # noqa: E402
import proof                                      # noqa: E402

RUN = BASE / "artifacts" / "auto_run_20261009-170809.json"

# live measurement, 2026-10-09 (GATES-REUPLOAD.md R15)
OUTCOMES = {
    "cable-storage-solution-3d": "removed",
    "modular-desk-organizer": "removed",
    "smartphone-stand-with-cable-cutout": "removed",
    "organizer-storage-solution": "live",
    "3d-print-hook-design": "live",
}

TINY_STL = b"""solid gate
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 10 0 0
  vertex 0 10 0
 endloop
endfacet
endsolid gate
"""


def _records():
    """The five real published records: name, source link, the licence string the
    run actually read, and the live outcome."""
    data = json.loads(RUN.read_text(encoding="utf-8"))
    out = []
    for it in data.get("items", []):
        if it.get("state") != "published":
            continue
        slug = (it.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        out.append({"name": it.get("name") or "", "url": it.get("url") or "",
                    "licence_raw": it.get("licence") or "", "slug": slug,
                    "outcome": OUTCOMES.get(slug, "unknown")})
    return out


def main() -> int:
    fails = []

    def check(ok, label, detail=""):
        print("   %s %s%s" % ("PASS" if ok else "FAIL", label,
                              ("  :: " + detail) if detail else ""))
        if not ok:
            fails.append(label)

    if not RUN.is_file():
        print("FAIL the real run log is missing:", RUN)
        return 1
    records = _records()
    print("REPLAY: %d published record(s) from %s" % (len(records), RUN.name))
    check(len(records) == 5, "the real run's five published records are replayed",
          ", ".join(sorted(r["slug"] for r in records)))

    with tempfile.TemporaryDirectory() as tmp:
        model = Path(tmp) / "replayed_model.stl"
        model.write_bytes(TINY_STL)
        proof_info = proof.build(model, name="Replayed model", out_dir=tmp)
        check(proof_info["ok"], "a proof of creation really builds from a model file",
              "%d triangles" % proof_info["params"].get("triangles", 0))
        unauth = creality_rules.load_authorized()

        for rec in records:
            lic = classify_license(rec["licence_raw"])
            verdict = creality_rules.check(name=rec["name"], source_url=rec["url"],
                                           licence=lic, proof=proof_info)
            flags = {c["check"]: c for c in verdict["checks"]}
            print("\n  %s  [%s]  licence=%s on the page=%s"
                  % (rec["slug"], rec["outcome"].upper(),
                     (lic.get("code") or "?").upper(), lic.get("ok_for_reupload")))
            check(verdict["allowed"] is False,
                  "refused as a re-upload (%s)" % rec["slug"], verdict["rule"])
            if rec["outcome"] == "removed":
                check(verdict["rule"] in ("noncommercial", "source-not-authorized"),
                      "a REMOVED record is refused by its rule",
                      "%s — %s" % (verdict["rule"], verdict["reason"][:120]))
                check(flags["source"]["allowed"] is False,
                      "the source rule refuses it independently of the licence")
            if lic.get("nc"):
                check(flags["licence"]["allowed"] is False
                      and flags["licence"]["rule"] == "noncommercial",
                      "an NC source is refused for the NonCommercial reason, "
                      "not for the live/removed outcome")
            if lic.get("code") == "by":
                check(flags["licence"]["allowed"] is True,
                      "a CC BY licence passes the licence rule (so the refusal "
                      "above comes from the source rule — the measured reason a "
                      "CC BY re-upload was still removed)")
            # authorisation is the only way a re-upload may go out, and these
            # records are NOT authorised in the user's file
            check(rec["url"].rstrip("/").lower() not in unauth,
                  "not silently authorised by the user's file (%s)" % rec["slug"])

        # ── the other direction: an authorised, allowed source does publish ──
        print("\n  ALLOWED-SIDE CONTROL")
        auth = creality_rules.add_authorized(
            "https://www.crealitycloud.com/model-detail/gate-control-source",
            "own design — gate control")
        try:
            good = creality_rules.check(
                name="Cable organiser (our own design)",
                source_url="https://www.crealitycloud.com/model-detail/gate-control-source",
                licence=classify_license("https://creativecommons.org/licenses/by/4.0"),
                proof=proof_info)
            check(good["allowed"] is True,
                  "an authorised source with a publishable licence and a proof PASSES",
                  good["rule"])
            bad = creality_rules.check(
                name="Cable organiser (our own design)",
                source_url="https://www.crealitycloud.com/model-detail/gate-control-source",
                licence=classify_license("https://creativecommons.org/licenses/by-nc/4.0"),
                proof=proof_info)
            check(bad["allowed"] is False
                  and bad["rule"] == "noncommercial",
                  "authorisation never overrides a NonCommercial licence",
                  bad["rule"])
            nop = creality_rules.check(
                name="Cable organiser (our own design)",
                source_url="https://www.crealitycloud.com/model-detail/gate-control-source",
                licence=classify_license("https://creativecommons.org/licenses/by/4.0"),
                proof={"ok": False, "why": "no model file to build a proof from"})
            check(nop["allowed"] is False and nop["rule"] == "no-proof"
                  or nop["rule"] == "proof-failed",
                  "a missing proof of creation refuses the upload", nop["rule"])
            toy = creality_rules.check(
                name="LEGO Star Wars X-Wing",
                source_url="https://www.crealitycloud.com/model-detail/gate-control-source",
                licence=classify_license("https://creativecommons.org/licenses/by/4.0"),
                proof=proof_info)
            check(toy["allowed"] is False and toy["rule"].startswith("not-eligible"),
                  "trademark / franchise content is refused before any page opens",
                  toy["rule"])
        finally:
            _remove(auth)          # the user's file is left exactly as it was

        print("\n  TOTAL: %d record(s) refused, %s"
              % (len(records), "no fabricated pass"))
    print("\n%s" % ("ALL POLICY CHECKS PASSED" if not fails
                    else "%d POLICY CHECK(S) FAILED: %s" % (len(fails), "; ".join(fails))))
    return 1 if fails else 0


def _remove(entry):
    """Undo add_authorized() so the gate leaves the user's file as it found it."""
    try:
        data = json.loads(creality_rules.AUTHORIZED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    key = creality_rules.normalise(entry["source_url"])
    data["authorized"] = [e for e in data.get("authorized", [])
                          if creality_rules.normalise(e if isinstance(e, str)
                                                      else e.get("source_url", "")) != key]
    creality_rules.AUTHORIZED_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
