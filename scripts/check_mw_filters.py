"""Open MakerWorld's model listing, click the licence chips the user showed me,
and print the URL + search request the site produces. Read-only, logged-out,
headless. Nothing is uploaded or published."""
import asyncio
import json

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/120.0.0.0 Safari/537.36")
LISTING = "https://makerworld.com/en/3d-models?sort=trending"
CHIPS = ["Public Domain", "BY", "BY-SA"]

CLICK_JS = """
(args) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const want = args.text.toLowerCase();
  const cands = [...document.querySelectorAll('body *')].filter((e) => {
    if (e.closest('script,style')) return false;
    if (norm(e.innerText).toLowerCase() !== want) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.width < 260;
  });
  if (!cands.length) return {ok: false, why: 'nothing on the page reads exactly ' + args.text};
  const box = (e) => { const r = e.getBoundingClientRect(); return r.width * r.height; };
  cands.sort((a, b) => box(a) - box(b));
  const el = cands[0];
  (el.querySelector('input,button,[role=checkbox]') || el).click();
  return {ok: true, tag: el.tagName.toLowerCase(), cls: ('' + el.className).slice(0, 90)};
}
"""

STATE_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const ids = new Set();
  document.querySelectorAll("a[href*='/models/']").forEach((a) => {
    const m = (a.getAttribute('href') || '').match(/\\/models\\/(\\d+)/);
    if (m) ids.add(m[1]);
  });
  const names = /^(public domain|by|by-sa|by-nd|by-nc|by-nc-sa|by-nc-nd|standard digital file license)$/i;
  const chips = [...document.querySelectorAll('body *')]
    .filter((e) => !e.childElementCount && names.test(norm(e.innerText)))
    .map((e) => { const r = e.getBoundingClientRect();
                  return {t: norm(e.innerText), on: r.width > 0 && r.height > 0}; });
  return {ids: [...ids], chips: chips.slice(0, 24)};
}
"""


async def main():
    hits = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(user_agent=UA, viewport={"width": 1400, "height": 1000})
            page = await ctx.new_page()
            page.on("request", lambda r: r.resource_type in ("xhr", "fetch") and "search" in r.url
                    and hits.append({"url": r.url[:500], "post": (r.post_data or "")[:1200]}))

            await page.goto(LISTING, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            for _ in range(2):
                await page.evaluate("window.scrollTo(0, 700)")
                await page.wait_for_timeout(1500)

            base = await page.evaluate(STATE_JS)
            print("URL:", page.url)
            print("IDS:", len(base["ids"]))
            print("CHIPS:", json.dumps(base["chips"], ensure_ascii=False)[:800])

            for text in CHIPS:
                r = await page.evaluate(CLICK_JS, {"text": text})
                print("click", text, "->", json.dumps(r, ensure_ascii=False))
                await page.wait_for_timeout(3500)

            after = await page.evaluate(STATE_JS)
            print("FILTERED URL:", page.url)
            print("FILTERED IDS:", len(after["ids"]), after["ids"][:12])
            print("SEARCH CALLS:", json.dumps(hits[-4:], ensure_ascii=False)[:2500])
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
