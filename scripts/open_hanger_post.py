"""Open the 'Gravity towel hook' post page in the logged-in browser,
dump its links (looking for the embedded model-detail URL and author),
and screenshot it for visual confirmation."""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uploader import CDP_URL, ensure_browser, PROFILE_DIR  # noqa: E402

POST = "https://www.crealitycloud.com/post-detail/691a475dad9806c8e4596bed"


async def main():
    from playwright.async_api import async_playwright

    ensure_browser()
    await asyncio.sleep(2)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "crealitycloud" in p.url), None) \
            or await ctx.new_page()
        await page.bring_to_front()
        await page.goto(POST, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(6000)

        # author + embedded model links
        links = await page.evaluate("""
            () => [...document.querySelectorAll('a')]
                .map(a => ({t: (a.innerText||'').trim().slice(0,60), h: a.href}))
                .filter(x => x.h && /model-detail|user|design|download/i.test(x.h+x.t))
        """)
        print("=== post page links ===")
        seen = set()
        for l in links:
            k = l["h"].split("?")[0]
            if k in seen:
                continue
            seen.add(k)
            print(f"  {l['t'][:45]:47s} {l['h'][:110]}")

        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        print("\n=== body (first 1200) ===")
        print(body[:1200])

        # avatar/author element -> profile link
        author = await page.evaluate("""
            () => {
                const el = document.querySelector('a[href*=\"/user/\"]');
                return el ? el.href : null;
            }
        """)
        print("\nauthor profile link:", author)

        # cookies: any uid-ish value
        cookies = await ctx.cookies("https://www.crealitycloud.com")
        for c in cookies:
            if re.search(r"uid|user|id", c["name"], re.I) and len(str(c["value"])) < 40:
                print("cookie:", c["name"], "=", c["value"])

        await page.screenshot(path=os.path.join("downloads", "_post_hanger.png"),
                              full_page=False)
        print("\nscreenshot: downloads/_post_hanger.png")


if __name__ == "__main__":
    asyncio.run(main())
