"""Search Creality Cloud's own search and list model-detail URLs.

Usage: python scripts/find_model.py "crochet cactus" "soap dish"
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

queries = sys.argv[1:] or ["soap dish"]
ensure_browser()

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    page = ctx.new_page()
    try:
        page.goto("https://www.crealitycloud.com/", wait_until="domcontentloaded",
                  timeout=60000)
        page.wait_for_timeout(7000)
        for q in queries:
            try:
                box = page.locator("input[placeholder*='earch'], input[type=search]").first
                box.click(timeout=5000)
                box.fill("")
                box.type(q, delay=60)
                page.wait_for_timeout(1500)
                page.keyboard.press("Enter")
                page.wait_for_timeout(7000)
                for _ in range(2):
                    page.mouse.wheel(0, 800)
                    page.wait_for_timeout(800)
                links = page.evaluate("""
                    () => [...new Set([...document.querySelectorAll('a[href*="model-detail"]')]
                        .map(a => a.href.split('?')[0]))].slice(0, 8)
                """)
                print("== %r -> %d" % (q, len(links)))
                for l in links:
                    print("   ", l)
                # back to the search box for the next query
                page.goto("https://www.crealitycloud.com/", wait_until="domcontentloaded",
                          timeout=45000)
                page.wait_for_timeout(4000)
            except Exception as e:
                print("== %r FAILED: %s" % (q, repr(e)[:160]))
    finally:
        try:
            page.close()
        except Exception:
            pass
