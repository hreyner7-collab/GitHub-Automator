"""READ-ONLY: save the live form's licence card HTML + the Model Source tip.

The saved copy lets the badge read be verified offline, against the REAL
markup, on every run (scripts/check_license_capture.py) instead of only during
a live upload.
"""
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright  # noqa: E402

JS = r"""
() => {
  const Q1 = 'Allow adaptations of your work to be shared?';
  const CAP = 'This work is licensed under';
  const has = (e, s) => ((e.innerText || '').indexOf(s) !== -1);
  const cards = [...document.querySelectorAll('div,section,form,fieldset')]
      .filter(e => has(e, Q1) && has(e, CAP));
  cards.sort((a, b) => ((a.innerText || '').length - (b.innerText || '').length));
  const card = cards[0] || null;
  // the FULL Model Source tip — the sentence that names other licences
  const tipEl = [...document.querySelectorAll('p,div,span')]
      .filter(e => /No Derivatives/i.test(e.innerText || '')
                   && /such as|CC BY-ND/i.test(e.innerText || '')
                   && (e.innerText || '').length < 400)
      .sort((a, b) => (b.innerText || '').length - (a.innerText || '').length)[0];
  return JSON.stringify({
    card_html: card ? card.outerHTML : '',
    card_text: card ? card.innerText : '',
    tip_text: tipEl ? tipEl.innerText : '',
    selected_radios: card ? [...card.querySelectorAll('input[type=radio]')]
        .map(r => (r.checked ? 1 : 0)).join('') : '',
  });
}
"""


async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next((p for p in b.contexts[0].pages if "create-model" in p.url), None)
        if not page:
            print("no create-model tab")
            return
        work = page
        for f in page.frames:
            try:
                if await f.locator("input[type=file]").count() > 0:
                    work = f
                    break
            except Exception:
                pass
        data = json.loads(await work.evaluate(JS))
        out = os.path.join(ROOT, "debug", "license_card.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("<!-- LIVE COPY of Creality's create-model licence card -->\n")
            fh.write("<!-- radios (checked=1): %s -->\n" % data["selected_radios"])
            fh.write("<p id=\"model-source-tip\">%s</p>\n" % data["tip_text"])
            fh.write("<div id=\"licence-section\">%s</div>\n" % data["card_html"])
        print("written:", out, len(data["card_html"]), "bytes of card HTML")
        print("tip:", data["tip_text"][:120])
        print("radios:", data["selected_radios"])
        print("card text:", data["card_text"][:200])


asyncio.run(main())
