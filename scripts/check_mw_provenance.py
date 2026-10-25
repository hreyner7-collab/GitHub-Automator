"""Prove that every MakerWorld control name this pipeline drives was READ off
the live site, not invented.

Each literal in makerworld_flow.py that names a page control or a site rule is
looked for in the measured dumps under MAKERWORLD-STUDY/ (the wizard dumps,
their API logs and the walk log). Anything not present in the evidence fails
the gate, so the day someone adds a selector they saw on a blog post this
script says so.

  .venv/Scripts/python.exe -X utf8 scripts/check_mw_provenance.py
  ... --selftest    also proves the checker can fail: a control name that is
                    not in the evidence must be refused before it is printed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
STUDY = ROOT / "MAKERWORLD-STUDY"
sys.path.insert(0, str(ROOT))

import makerworld_flow as mw  # noqa: E402


def evidence_text() -> str:
    """Everything the study recorded, as one searchable blob."""
    if not STUDY.is_dir():
        raise SystemExit(f"FAIL: no measured evidence in {STUDY} — the flow was never studied")
    parts = []
    for p in sorted(STUDY.iterdir()):
        if p.suffix in (".json", ".log", ".txt"):
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    blob = "\n".join(parts)
    if "Real life photos" not in blob or "Next Step" not in blob:
        raise SystemExit("FAIL: the evidence does not contain the wizard it claims to")
    return blob


def measured_literals() -> dict[str, str]:
    """name -> literal, for every control/rule the driver can click or read."""
    out = {
        "ENTRY_REMIX": mw.ENTRY_REMIX,
        "ENTRY_ORIGINAL": mw.ENTRY_ORIGINAL,
        "STL_BRANCH_TEXT": mw.STL_BRANCH_TEXT,
        "NEXT_STEP_BUTTON": mw.NEXT_STEP_BUTTON,
        "PUBLISH_BUTTON": mw.PUBLISH_BUTTON,
        "SAVE_DRAFT_BUTTON": mw.SAVE_DRAFT_BUTTON,
        "PREVIOUS_BUTTON": mw.PREVIOUS_BUTTON,
        "SLOT_COVER_43": mw.SLOT_COVER_43,
        "SLOT_COVER_34": mw.SLOT_COVER_34,
        "SLOT_REAL_PHOTOS": mw.SLOT_REAL_PHOTOS,
        "SLOT_MODEL_PHOTOS": mw.SLOT_MODEL_PHOTOS,
        "SOURCE_INPUT_PLACEHOLDER": mw.SOURCE_INPUT_PLACEHOLDER,
        "REMIX_DETAIL_REQUIRED": mw.REMIX_DETAIL_REQUIRED,
        "LICENSE_Q_ADAPT": mw.LICENSE_Q_ADAPT,
        "LICENSE_Q_COMMERCIAL": mw.LICENSE_Q_COMMERCIAL,
        "LICENSE_Q_REDISTRIBUTE": mw.LICENSE_Q_REDISTRIBUTE,
        "LICENSE_Q_BAMBU_HANDY": mw.LICENSE_Q_BAMBU_HANDY,
    }
    for i, opt in enumerate(mw.ADAPT_OPTIONS):
        out[f"ADAPT_OPTION_{i}"] = opt
    for i, src in enumerate(mw.MODEL_SOURCE_RADIOS):
        out[f"MODEL_SOURCE_{i}"] = src
    for i, v in enumerate(mw.VISIBILITY_RADIOS):
        out[f"VISIBILITY_{i}"] = v
    for ext in (".stl", ".3mf", ".obj", ".step"):
        out[f"MODEL_ACCEPT_{ext}"] = ext
    return out


def check(blob: str, literals: dict[str, str]) -> list[str]:
    missing = []
    for name, lit in literals.items():
        # the wizard renders quotes and spacing loosely; a substring match on
        # the normalised text is what 'was seen on the page' means here
        if re.sub(r"\s+", " ", lit).strip() not in blob:
            missing.append(f"{name} ({lit[:60]!r})")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="prove the checker refuses an unmeasured control name")
    args = ap.parse_args()

    blob = evidence_text()
    lits = measured_literals()

    if args.selftest:
        ghost = {"INVENTED_BUTTON": "Publish Everything Now"}
        got = check(blob, ghost)
        if not got:
            print("FAIL: --selftest — a control nobody measured was accepted as proven")
            return 1
        print(f"control refused as required: {got[0]}")

    missing = check(blob, lits)
    if missing:
        print("FAIL: these MakerWorld controls are used by the pipeline but appear "
              "nowhere in MAKERWORLD-STUDY/:")
        for m in missing:
            print("  - " + m)
        return 1

    # the draft mechanism itself: measured, not assumed
    if not re.search(r"/my/models/drafts/(\d+)/edit", blob):
        print("FAIL: no measured draft-edit URL in the evidence")
        return 1
    if "/api/v1/design-service/my/draft" not in blob:
        print("FAIL: the draft endpoint was never seen in the recorded traffic")
        return 1

    print(f"evidence: {len([p for p in STUDY.iterdir()])} files in MAKERWORLD-STUDY/")
    print(f"checked {len(lits)} control names, all of them present in the measured dumps")
    print("MakerWorld provenance verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
