"""Load the listing with a candidate licences= value and read back which chips
the site renders as selected, plus what licences the returned models actually
carry. One page load. Read-only, logged-out, headless."""
import asyncio
import json

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/120.0.0.0 Safari/537.36")
CANDIDATE = "https://makerworld.com/en/3d-models?sort=trending&licenses=CC0,BY,BY-SA"

READ_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const names = ['Public Domain','BY','BY-SA','BY-ND','BY-NC','BY-NC-SA','BY-NC-ND',
                 'Standard Digital File License'];
  const chips = [];
  [...document.querySelectorAll('body *')].forEach((e) => {
    if (e.childElementCount) return;
    const t = norm(e.innerText);
    if (!names.some((n) => n.toLowerCase() === t.toLowerCase())) return;
    const cs = getComputedStyle(e);
    const owner = e.closest('button,[role=checkbox],[class]');
    chips.push({t, bg: cs.backgroundColor, color: cs.color,
                aria: (owner && owner.getAttribute('aria-checked')) || '',
                cls: owner ? ('' + owner.className).slice(0, 60) : ''});
  });
  const cards = [];
  document.querySelectorAll("a[href*='/models/']").forEach((a) => {
    const m = (a.getAttribute('href') || '').match(/\\/models\\/(\\d+)/);
    if (m) cards.push(m[1]);
  });
  return {title: document.title, chips, ids: [...new Set(cards)].length};
}
"""


async def main():
    seen = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context(user_agent=UA,
                                                    viewport={"width": 1400, "height": 1000})).new_page()
            page.on("response", lambda r: "search-service" in r.url
                    and seen.append(r.url.split("?")[0].split("/api/v1/")[-1]))
            await page.goto(CANDIDATE, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(8000)
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, 700)")
                await page.wait_for_timeout(1500)
            out = await page.evaluate(READ_JS)
            print("TITLE:", out["title"])
            print("URL:", page.url)
            print("MODEL IDS ON PAGE:", out["ids"])
            print("API CALLS:", sorted(set(seen)))
            for c in out["chips"]:
                print("  chip", json.dumps(c, ensure_ascii=False))
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
