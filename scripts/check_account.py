"""Find the 'Auto-locking Hanger' model on the user's Creality Cloud account.

Strategy:
  1. Connect to the real Chrome over CDP (logged-in profile).
  2. Dump every nav/profile link from crealitycloud.com to find the user id.
  3. Visit the user's designer/profile space and list their models.
  4. Try known 'my designs' / draft URL patterns.
  5. Site-search for the hanger slug.
Prints candidate model-detail URLs for the hanger.
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uploader import CDP_URL, ensure_browser  # noqa: E402

HANGER_RE = re.compile(r"auto.?lock|hanger|towel", re.I)


async def dump_links(page, label):
    links = await page.evaluate(
        """() => [...document.querySelectorAll('a')]
              .map(a => ({t: (a.innerText||'').trim().slice(0,50), h: a.href}))
              .filter(x => x.h && x.h.startsWith('http') && !/javascript:/.test(x.h))"""
    )
    print(f"--- links on {label} ({len(links)}) ---")
    seen = set()
    for l in links:
        key = l["h"].split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        print(f"  {l['t'][:40]:42s} {l['h'][:120]}")
    return links


async def get_body(page):
    return await page.evaluate(
        "() => document.body ? document.body.innerText : ''"
    )


async def main():
    from playwright.async_api import async_playwright

    if not ensure_browser():
        print("!! could not start Chrome with debug port")
        return
    await asyncio.sleep(2)
    found = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "crealitycloud" in p.url), None)
        if page is None:
            page = await ctx.new_page()
        await page.bring_to_front()

        # 1. Home: find user id / profile links
        try:
            await page.goto("https://www.crealitycloud.com/",
                            wait_until="domcontentloaded", timeout=40000)
            await page.wait_for_timeout(4000)
        except Exception as e:
            print("home load failed:", e)
        links = await dump_links(page, "home")
        print("BODY head:\n", (await get_body(page))[:800], "\n")

        user_ids = set()
        for l in links:
            m = re.search(r"(?:user|uid|designer)[=/](\d{3,})", l["h"])
            if m:
                user_ids.add(m.group(1))
        print("user ids found:", user_ids or "none")

        # 2. Candidate pages: profile space, my designs, drafts
        candidates = [
            "https://www.crealitycloud.com/designer-space",
            "https://www.crealitycloud.com/my-designs",
            "https://www.crealitycloud.com/user-center",
            "https://www.crealitycloud.com/personal-center",
        ]
        for uid in list(user_ids)[:3]:
            candidates += [
                f"https://www.crealitycloud.com/designer-space?uid={uid}",
                f"https://www.crealitycloud.com/u/{uid}",
                f"https://www.crealitycloud.com/user/{uid}",
            ]
        for url in candidates:
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=30000)
                await page.wait_for_timeout(3500)
            except Exception as e:
                print(f"!! {url} -> {type(e).__name__}")
                continue
            final = page.url
            body = await get_body(page)
            print(f"\n=== {url}\n    -> final: {final[:120]}")
            print("    body head:", body[:400].replace("\n", " | "))
            for l in await dump_links(page, final[:60]):
                if l["h"] not in found and HANGER_RE.search(
                        l["t"] + " " + l["h"]):
                    found.add(l["h"])

        # 3. Site search for the hanger
        for q in ["auto-locking%20hanger", "towel%20hook"]:
            url = (f"https://www.crealitycloud.com/search"
                   f"?keyword={q}&type=model")
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=30000)
                await page.wait_for_timeout(4000)
            except Exception as e:
                print(f"!! search {q} -> {type(e).__name__}")
                continue
            print(f"\n=== search {q} -> {page.url[:120]}")
            for l in await dump_links(page, f"search {q}"):
                if HANGER_RE.search(l["t"] + " " + l["h"]):
                    found.add(l["h"])
            body = await get_body(page)
            i = body.lower().find("hanger")
            if i >= 0:
                print("   body context:", body[max(0, i-120):i+200]
                      .replace("\n", " | "))

        # 4. Any open creality tabs already showing the model?
        for p in ctx.pages:
            if re.search(r"model-detail|/model/|design", p.url) \
                    and HANGER_RE.search(p.url):
                found.add(p.url)

    print("\n\n===== HANGER-RELATED URLS FOUND =====")
    if found:
        for u in sorted(found):
            print("  ", u)
    else:
        print("  (none — model may be a draft not exposed by these pages)")


if __name__ == "__main__":
    asyncio.run(main())
