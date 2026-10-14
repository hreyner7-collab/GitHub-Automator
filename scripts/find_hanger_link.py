"""Find the exact Creality Cloud link for the 'Auto-locking Hanger /
Gravity Towel Hook - Ribbed' model: who is signed in, their own designs,
the yeggi-indexed listing, and the public tag page."""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uploader import CDP_URL, ensure_browser  # noqa: E402

HANGER = re.compile(r"auto.?lock|hanger|towel", re.I)


async def main():
    from playwright.async_api import async_playwright

    ensure_browser()
    await asyncio.sleep(2)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "crealitycloud" in p.url), None) \
            or await ctx.new_page()

        # --- 1. Who is signed in? (localStorage user info) ---
        try:
            await page.goto("https://www.crealitycloud.com/",
                            wait_until="domcontentloaded", timeout=40000)
            await page.wait_for_timeout(3500)
        except Exception as e:
            print("home load:", e)
        info = await page.evaluate("""
            () => {
                const out = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    if (/user|nick|account|member|uid/i.test(k)) {
                        let v = localStorage.getItem(k) || '';
                        if (v.length > 300) v = v.slice(0, 300);
                        out[k] = v;
                    }
                }
                return out;
            }
        """)
        print("=== signed-in user hints (localStorage) ===")
        for k, v in info.items():
            print(f"  {k} = {v[:200]}")

        # --- 2. Yeggi search -> exact crealitycloud listing URLs ---
        try:
            await page.goto(
                "https://www.yeggi.com/q/auto+locking+hanger+towel/",
                wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(9000)
        except Exception as e:
            print("yeggi load:", e)
        yl = await page.evaluate("""
            () => [...document.querySelectorAll('a')]
                .map(a => ({t: (a.innerText||a.title||'').trim().slice(0,60),
                            h: a.href}))
                .filter(x => x.h && /crealitycloud/i.test(x.h))
        """)
        print("\n=== yeggi -> crealitycloud links ===")
        for l in yl:
            mark = " <<<" if HANGER.search(l["t"]) else ""
            print(f"  {l['t'][:55]:57s} {l['h'][:110]}{mark}")

        # --- 3. Public tag page: find the Ribbed listing ---
        try:
            await page.goto(
                "https://www.crealitycloud.com/model-tags/gravity%20hook",
                wait_until="domcontentloaded", timeout=40000)
            await page.wait_for_timeout(4000)
        except Exception as e:
            print("tag page load:", e)
        tl = await page.evaluate("""
            () => [...document.querySelectorAll('a')]
                .map(a => ({t: (a.innerText||'').trim().slice(0,80),
                            h: a.href}))
                .filter(x => x.h && /model-detail/.test(x.h))
        """)
        print("\n=== tag page 'gravity hook' model-detail links ===")
        for l in tl:
            if HANGER.search(l["t"]):
                print(f"  {l['t'][:70]:72s} {l['h'][:110]}")

        # --- 4. Open tabs already on a matching page? ---
        for p in ctx.pages:
            if HANGER.search(p.url):
                print("\n=== open tab ===\n  ", p.url[:150])


if __name__ == "__main__":
    asyncio.run(main())
