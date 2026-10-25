"""For every licence chip: fresh page load, one click, print the URL.
That maps each visible chip to the token MakerWorld actually sends.
Read-only, logged-out, headless."""
import asyncio
import json

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/120.0.0.0 Safari/537.36")
LISTING = "https://makerworld.com/en/3d-models?sort=trending"
CHIPS = ["Public Domain", "BY", "BY-SA", "BY-ND", "BY-NC", "BY-NC-SA",
         "BY-NC-ND", "Standard Digital File License"]

CLICK_JS = """
(args) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const want = args.text.toLowerCase();
  const cands = [...document.querySelectorAll('body *')].filter((e) => {
    if (e.closest('script,style') || e.childElementCount) return false;
    if (norm(e.innerText).toLowerCase() !== want) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  if (!cands.length) return {ok: false, why: 'absent'};
  const el = cands[0];
  (el.querySelector('input,button,[role=checkbox]') || el).click();
  return {ok: true};
}
"""

COUNT_JS = """
() => { const s = new Set();
  document.querySelectorAll("a[href*='/models/']").forEach((a) => {
    const m = (a.getAttribute('href') || '').match(/\\/models\\/(\\d+)/);
    if (m) s.add(m[1]); });
  return s.size; }
"""


async def one(browser, text):
    page = await browser.new_page()
    try:
        await page.goto(LISTING, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, 700)")
            await page.wait_for_timeout(1500)
        r = await page.evaluate(CLICK_JS, {"text": text})
        await page.wait_for_timeout(4000)
        return {"chip": text, "clicked": r.get("ok"), "url": page.url,
                "ids": await page.evaluate(COUNT_JS)}
    finally:
        await page.close()


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for text in CHIPS:
                print(json.dumps(await one(browser, text), ensure_ascii=False))
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
