"""One page load, two answers:
  1. what the listing API returns per model (does it carry the licence?), and
  2. the exact token each licence chip writes into the URL.
Read-only, logged-out, headless."""
import asyncio
import json

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/120.0.0.0 Safari/537.36")
LISTING = "https://makerworld.com/en/3d-models?sort=trending"

CLICK_JS = """
(args) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const want = args.toLowerCase();
  const leaf = [...document.querySelectorAll('body *')]
    .find((e) => !e.childElementCount && norm(e.innerText).toLowerCase() === want);
  if (!leaf) return {ok: false, why: 'absent'};
  const chip = leaf.closest('div,button,label');
  (chip || leaf).click();
  return {ok: true, cls: chip ? ('' + chip.className).slice(0, 50) : ''};
}
"""


def walk(obj, path="", out=None, depth=0):
    """Collect every key whose name smells like a licence, with its value."""
    if out is None:
        out = {}
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if any(s in k.lower() for s in ("licen", "cc", "copyright")):
                out.setdefault(p, str(v)[:120])
            walk(v, p, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:3]:
            walk(v, path + "[]", out, depth + 1)
    return out


async def main():
    bodies = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context(user_agent=UA,
                                                    viewport={"width": 1400, "height": 1000})).new_page()

            async def grab(resp):
                if "select/design2" in resp.url:
                    try:
                        bodies.append(await resp.json())
                    except Exception as exc:
                        print("body unreadable:", exc)

            page.on("response", lambda r: asyncio.ensure_future(grab(r)))
            await page.goto(LISTING, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(8000)
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, 700)")
                await page.wait_for_timeout(1500)

            for text in ["BY", "BY-SA", "BY-ND", "BY-NC"]:
                print("click", text, "->", json.dumps(await page.evaluate(CLICK_JS, text)))
                await page.wait_for_timeout(2500)
            print("URL after four clicks:", page.url)

            await page.wait_for_timeout(2000)
            if bodies:
                first = bodies[0]
                print("API TOP KEYS:", list(first)[:10] if isinstance(first, dict) else type(first))
                lic = walk(first)
                print("LICENCE-SHAPED FIELDS:")
                for k, v in list(lic.items())[:20]:
                    print("   ", k, "=", v)
            else:
                print("no design2 body captured")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
