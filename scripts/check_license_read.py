"""GATE (licence read): prove the model-page licence reader decides correctly.

Offline. A headless Chromium is given model-page DOMs built from the REAL
strings (saved from live Creality pages), the pipeline's own LICENSE_JS is run
against them, and its result is classified with the pipeline's own
classify_license — so this checks the JavaScript AND the decision rule together,
including the bug the user reported: a licensed (CC) model refused as if it had
no licence, because the site-wide boilerplate was the text that got read.

It also pins the 2026-10-09 POLICY: an NC card must be READ correctly (code
by-nc-sa, not "Standard") and then REFUSED for being NonCommercial, while a
CC BY card still passes.

Exits non-zero on any wrong decision. No network, no login, no live site.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from downloader import LICENSE_JS, classify_license  # noqa: E402

# ── the real strings (verbatim from live Creality Cloud pages) ───────────────
SITE_BOILERPLATE = "This work is licensed under the Copyright License 4.0."
STANDARD_PARAGRAPH = (
    "3D Model files sold on Creality Cloud are available under a standard "
    "license, which has certain restrictions. In particular, these files cannot "
    "be used for any commercial use; it's for personal use only. 3D model or "
    "any portion of the model in a digital or physical format may not be "
    "shared, transmitted, redistributed, remixed, duplicated, or sold."
)
CC_PARAGRAPH = (
    "CC BY-NC-SA This license allows reusers to distribute, remix, adapt, and "
    "build upon the material in any medium or format for noncommercial purposes "
    "only, and only so long as attribution is given to the creator. If you "
    "remix, adapt, or build upon the material, you must license the modified "
    "material under identical terms."
)

# ── the page shapes ─────────────────────────────────────────────────────────
# Every model page carries the boilerplate line and the site-wide standard
# paragraph somewhere; what decides is the model's OWN licence area.
SITE_FOOTER = ('<div class="legal"><p>%s %s</p>'
               '<a href="https://www.crealitycloud.com/policy/copyright">'
               "Copyright Statement</a></div>" % (SITE_BOILERPLATE, STANDARD_PARAGRAPH))

CASES = {
    # the model's own card links the CC licence (the shape the live run read)
    "cc_card_with_link": (
        SITE_FOOTER +
        '<div class="model-content"><div class="license-card">'
        '<a href="https://creativecommons.org/licenses/by-nc-sa/4.0">'
        "creativecommons.org/licenses/by-nc-sa/4.0</a> "
        "<p>%s</p></div></div>" % CC_PARAGRAPH,
        # read correctly, then refused for being NonCommercial (the policy)
        {"ok": False, "code": "by-nc-sa", "restricted": False, "unknown": False,
         "note_mentions": "NonCommercial"}),
    # ...whereas a CC BY card — redistribution, remixing AND commercial use — is
    # the shape that is still published
    "cc_by_card": (
        SITE_FOOTER +
        '<div class="model-content"><div class="license-card">'
        '<a href="https://creativecommons.org/licenses/by/4.0">'
        "creativecommons.org/licenses/by/4.0</a></div></div>",
        {"ok": True, "code": "by", "restricted": False, "unknown": False}),
    # the licence is only DRAWN as icons; the site paragraph is the only text
    "cc_card_as_icons": (
        SITE_FOOTER +
        '<div class="model-content"><div class="license-card">'
        '<img src="/static/cc.svg"><imG src="/static/by.svg">'
        '<img src="/static/nc.svg"><img src="/static/sa.svg">'
        "<p>%s</p></div></div>" % SITE_BOILERPLATE,
        {"ok": False, "code": "by-nc-sa", "restricted": False, "unknown": False,
         "note_mentions": "NonCommercial"}),
    # a No-Derivatives source must still be refused
    "cc_nd_card": (
        SITE_FOOTER +
        '<div class="model-content"><div class="license-card">'
        '<a href="https://creativecommons.org/licenses/by-nd/4.0">'
        "creativecommons.org/licenses/by-nd/4.0</a></div></div>",
        {"ok": False, "code": "by-nd", "restricted": False, "unknown": False,
         "quote": ""}),
    # Creality's own Standard License: refused, with the sentence it read
    "creality_standard": (
        '<div class="model-content"><div class="license-card">'
        "<p>%s %s</p></div></div>" % (SITE_BOILERPLATE, STANDARD_PARAGRAPH),
        {"ok": False, "code": "", "restricted": True, "unknown": False,
         "quote_must_mention": "may not be shared"}),
    # nothing but the boilerplate line: UNREADABLE, never "not licensed"
    "boilerplate_only": (
        '<div class="model-content"><div class="license-card">'
        "<p>%s</p></div></div>" % SITE_BOILERPLATE,
        {"ok": False, "code": "", "restricted": False, "unknown": True}),
}

PAGE = """<!doctype html><html><head><title>Creality Cloud model</title></head>
<body><h1>Some model</h1>%s</body></html>"""


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("SKIP: playwright is not installed")
        return 0

    bad = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        for name, (html, want) in CASES.items():
            await page.set_content(PAGE % html)
            raw = await page.evaluate(LICENSE_JS)
            got = json.loads(raw) if isinstance(raw, str) else (raw or {})
            text = " ".join(x for x in [got.get("cc_url") or "",
                                        got.get("card") or ""] if x).strip()
            info = classify_license(text, got.get("icons"))
            ok = (info["ok_for_reupload"] is want["ok"]
                  and info["restricted"] is want["restricted"]
                  and info["unknown"] is want["unknown"]
                  and (not want.get("code") or info["code"].startswith(want["code"])))
            if want.get("quote_must_mention"):
                ok = ok and want["quote_must_mention"] in info["quote"]
            if want.get("note_mentions"):
                ok = ok and want["note_mentions"] in info["note"]
            bad += 0 if ok else 1
            print("%-20s %s  code=%-10s ok=%-5s restricted=%-5s unknown=%-5s icons=%s"
                  % (name, "PASS" if ok else "FAIL", info["code"] or "-",
                     info["ok_for_reupload"], info["restricted"], info["unknown"],
                     ",".join(got.get("icons") or []) or "-"))
            print("                     read: %s" % (text[:150] or "(nothing)"))
            if not ok:
                print("                     WANT: %s" % want)
                print("                     GOT : raw=%s note=%s quote=%s"
                      % (info["raw"][:120], info["note"][:160], info["quote"][:120]))
        await browser.close()
    if bad:
        print("FAIL: %d of %d licence reads decided wrongly" % (bad, len(CASES)))
        return 1
    print("PASS: %d of %d licence reads decided correctly" % (len(CASES), len(CASES)))
    return 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))
