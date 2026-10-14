"""Inspector: read the SOURCE model's licence text the way a human would.

Opens the model page in the user's debug Chrome and dumps the licence block
(the sentence that starts "This work is licensed under ..."), any licence
links, and the licence fields in the page payload, then runs them through the
same `classify_license` the pipeline uses. This is the per-link check: every
model URL gets its licence read from the page before anything is published.

Usage:  .venv/Scripts/python.exe scripts/probe_license_text.py <model-url>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright      # noqa: E402

from downloader import LICENSE_JS, classify_license  # noqa: E402
from uploader import CDP_URL, expected_badge_token, license_answer_indexes  # noqa: E402

# The pipeline and this inspector read the licence through the SAME code
# (downloader.LICENSE_JS), so a link can never be classified two ways.
JS = LICENSE_JS

LINKS_JS = r"""
() => JSON.stringify([...document.querySelectorAll('a')].map(a => a.href)
    .filter(h => /licen|creativecommons|copyright/i.test(h)).slice(0, 10))
"""


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not url:
        print("usage: probe_license_text.py <model-url>")
        return 2
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        page = browser.contexts[0].new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(4500)
        out = json.loads(page.evaluate(JS))
        data = {"sentence": out.get("card", ""),
                "cc_url": out.get("cc_url", ""),
                "icons": out.get("icons", []),
                "links": json.loads(page.evaluate(LINKS_JS))}
        page.close()

    print("--- page licence card ---")
    print("   cc url:", data["cc_url"] or "(none)")
    print("   icons :", ", ".join(data["icons"]) or "(no licence icons drawn)")
    print("   card  :", data["sentence"][:400] or "(no 'This work is licensed' text)")
    print("--- licence links ---")
    for l in data["links"]:
        print("   ", l[:120])
    print("--- pipeline classification ---")
    info = classify_license(" ".join(x for x in [data["cc_url"], data["sentence"]] if x),
                            data["icons"])
    print("   ", json.dumps(info, ensure_ascii=False))
    code, idx = license_answer_indexes(info["raw"])
    print(f"    form answers would be {idx} -> badge "
          f"{expected_badge_token(code)!r} (None = no badge expected)")
    out = ROOT / "debug" / "license_text.json"
    out.write_text(json.dumps({**data, "classification": info,
                               "answers": idx,
                               "expected_badge": expected_badge_token(code)},
                              indent=2), encoding="utf-8")
    print("wrote", out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
