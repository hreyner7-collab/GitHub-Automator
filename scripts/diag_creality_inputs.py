"""Dump the create-model form's file inputs (accept attrs + neighbors' text)
so the uploader's locators can be matched to the real page structure."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uploader import CDP_URL, ensure_browser  # noqa: E402


async def main():
    from playwright.async_api import async_playwright
    ensure_browser()
    await asyncio.sleep(2)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "crealitycloud" in p.url), None) \
            or await ctx.new_page()
        await page.goto("https://www.crealitycloud.com/create-model-new?source=12",
                        wait_until="domcontentloaded", timeout=40000)
        for _ in range(30):
            if await page.locator("input[type=file]").count() > 0:
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(1500)
        info = await page.evaluate("""
            () => [...document.querySelectorAll('input[type=file]')].map((inp, i) => {
                const label = inp.closest('label, .el-upload, div');
                return {i, accept: inp.accept || '', multiple: inp.multiple,
                        id: inp.id || '', cls: (inp.className||'').slice(0,50),
                        labelText: label ? (label.innerText||'').slice(0,60).replace(/\\n/g,' | ') : ''};
            })
        """)
        for d in info:
            print(d)
        # what UI section headers exist (to identify the model-files slot)?
        heads = await page.evaluate("""
            () => [...document.querySelectorAll('h1,h2,h3,h4,.title,[class*=title]')]
                .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 50).slice(0, 20)
        """)
        print("\nsection titles:", heads)
        if page.url.count("create-model") and page != ctx.pages[0]:
            await page.close()
        else:
            await page.goto("about:blank")


if __name__ == "__main__":
    asyncio.run(main())
