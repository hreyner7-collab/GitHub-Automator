"""GATE (licence read): prove the form's CC badge is read CORRECTLY, offline.

Runs the uploader's real LICENSE_BADGE_JS against a saved copy of Creality's
create-model licence card (debug/license_card.html, written by
scripts/dump_license_card.py) and parses the result with the uploader's own
parser. It also replays the OLD page-wide rule to show it reproduces the bug
that refused a correct CC BY-NC form ("badge reads 'CCBY-ND'").

Exits non-zero on any failure. No network, no browser session needed.
"""
import asyncio
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uploader import LICENSE_BADGE_JS, badge_license_name, expected_badge_token  # noqa: E402


def old_badge_license_name(text):
    """The pre-fix parser (unanchored first match, no enumeration guard)."""
    m = re.search(r"(CC\s?0|CC\s?BY(?:\s*[-/]\s*(?:NC|SA|ND)){0,2}|Public\s+Domain)",
                  text or "", re.I)
    if not m:
        return ""
    return re.sub(r"\s+", "", m.group(1)).upper()

CARD = os.path.join(ROOT, "debug", "license_card.html")

# The rule that shipped before the fix: shortest element on the PAGE whose text
# mentions a CC badge — which on the real form is the Model Source tip.
OLD_JS = r"""
() => {
    const ccish = [...document.querySelectorAll('div,span,p,section,a,td')]
        .filter(e => /CC\s?0|CC\s?BY|Public\s+Domain/i.test(e.innerText || ''))
        .sort((a, b) => ((a.innerText || '').length - (b.innerText || '').length));
    return ccish.length ? ccish[0].innerText : '';
}
"""


def die(msg):
    print("FAIL:", msg)
    sys.exit(1)


async def main():
    if not os.path.exists(CARD):
        die("no saved licence card at %s — run scripts/dump_license_card.py "
            "while the upload form is open" % CARD)
    html = open(CARD, encoding="utf-8").read()

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html)

        payload = await page.evaluate(LICENSE_BADGE_JS)
        import json
        cap = json.loads(payload)
        new = badge_license_name(cap.get("badge") or "")
        old_text = await page.evaluate(OLD_JS)
        old = badge_license_name(old_text or "")

        # the icons tier must also be able to name the same licence
        print("capture:", json.dumps({k: (v[:90] if isinstance(v, str) else v)
                                     for k, v in cap.items()}))
        print("NEW read  ->", new or "(nothing)", "| in_card:", cap.get("in_card"))
        print("OLD read  ->", old or "(nothing)", "| from:", (old_text or "")[:90])
        await browser.close()

    if not new:
        die("the scoped read found no badge in the real licence card")
    if new != "CCBY-NC":
        die("badge read %r, expected CCBYNC (the card is a CC BY-NC form)" % new)
    if expected_badge_token("by-nc") != "CC BY-NC":
        die("CC BY-NC does not map to the 'CC BY-NC' badge token")
    if old == new:
        die("the OLD page-wide capture was expected to grab the WRONG text "
            "(that is the bug); it did not, so this gate proves nothing")
    # the reported failure, reproduced exactly by the old capture+parser
    old_refusal = old_badge_license_name(old_text)
    print("OLD capture+parser would refuse with: badge reads %r but the source "
          "license needs %r" % (old_refusal, expected_badge_token("by-nc")))
    if old_refusal != "CCBY-ND":
        die("expected the old path to read CCBY-ND (the reported refusal), got %r"
            % old_refusal)
    # and the new parser refuses to invent a badge out of that same sentence
    if badge_license_name(old_text) != "":
        die("the enumeration guard did not reject the Model Source tip")
    print("PASS: scoped read -> %s on the real card; the old page-wide read "
          "(%r...) reproduced the reported 'CCBY-ND' refusal, and the new "
          "parser refuses that sentence outright"
          % (new, (old_text or "")[:60]))


asyncio.run(main())
