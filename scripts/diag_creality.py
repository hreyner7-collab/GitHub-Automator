"""Diagnose why the Creality upload form isn't found.

Opens https://www.crealitycloud.com/create-model-new?source=12 in the user's
debug Chrome and reports: final URL (redirects), walls (login/Cloudflare),
every frame's URL, and which frames have <input type=file>. Read-only.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uploader import CDP_URL, ensure_browser  # noqa: E402

CREATE_URL = "https://www.crealitycloud.com/create-model-new?source=12"


async def main():
    from playwright.async_api import async_playwright
    ensure_browser()
    await asyncio.sleep(2)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=40000)
        # poll like the uploader does, but much longer, reporting progress
        seen = {}
        for i in range(24):  # up to 60s
            frames = [(f.url[:110],
                       await f.locator("input[type=file]").count()) for f in page.frames]
            seen[str(frames)] = i
            body = ""
            try:
                body = (await page.evaluate("() => document.body ? document.body.innerText.slice(0,400) : ''"))
            except Exception:
                pass
            low = body.lower()
            wall = ("just a moment" in low or "checking your browser" in low
                    or "sign in" in low or "log in" in low)
            print(f"[{i*2.5:4.1f}s] url={page.url[:90]}  frames={len(page.frames)}  wall={wall}")
            if any(c > 0 for _, c in frames):
                print("\n>>> FILE INPUT(S) FOUND")
                for u, c in frames:
                    print(f"    files={c}  {u}")
                break
            await page.wait_for_timeout(2500)
        else:
            print("\n>>> NO file input after 60s. Final state:")
        print("\nFINAL URL:", page.url)
        print("TITLE:", await page.title())
        print("\nFRAMES:")
        for f in page.frames:
            try:
                n = await f.locator("input[type=file]").count()
            except Exception:
                n = "?"
            print(f"  files={n}  {f.url[:120]}")
        print("\nBODY TEXT (first 600 chars):")
        try:
            print((await page.evaluate("() => document.body ? document.body.innerText.slice(0,600) : ''")))
        except Exception as e:
            print("(body read failed:", e, ")")
        await page.close()


if __name__ == "__main__":
    asyncio.run(main())
