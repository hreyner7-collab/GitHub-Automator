"""Probe v2: which 'Paste the URL' input is the real one, and does the
suggestion dropdown work after typing the slug? Saves debug/remix_probe.json.
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

ensure_browser()

out = {}

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    page = ctx.new_page()
    try:
        page.goto("https://www.crealitycloud.com/create-model-new?source=12",
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(8000)
        try:
            page.get_by_text("STL/CAD files or other types of 3MF file",
                             exact=False).first.click(timeout=4000)
            page.wait_for_timeout(1500)
        except Exception as e:
            out["stl_option_click"] = str(e)[:120]

        # click the el-radio wrapper via Playwright (proper mouse events)
        remix = page.locator("label.el-radio").filter(has_text="Remix Models").first
        out["remix_label_count"] = page.locator("label.el-radio").filter(
            has_text="Remix Models").count()
        remix.scroll_into_view_if_needed(timeout=6000)
        remix.click(timeout=6000)
        page.wait_for_timeout(2500)

        # enumerate matching inputs across the page AND its frames
        def scan(loc, tag):
            inputs = loc.locator("input[placeholder*='Paste the URL' i]")
            n = inputs.count()
            res = []
            for i in range(n):
                el = inputs.nth(i)
                try:
                    box = el.bounding_box()
                except Exception:
                    box = None
                res.append({"i": i, "box": box, "visible": el.is_visible()})
            return {"count": n, "items": res}

        out["main_page_inputs"] = scan(page, "main")
        for f in page.frames:
            if f is not page and "create-model" in (f.url or ""):
                out.setdefault("frames", {})[f.url[:80]] = scan(f, "frame")

        # type into the FIRST VISIBLE input wherever it lives
        target, owner = None, None
        cands = [("main", page)]
        for f in page.frames:
            if f is not page:
                cands.append(("frame:" + f.url[:50], f))
        for owner_name, loc in cands:
            inputs = loc.locator("input[placeholder*='Paste the URL' i]")
            for i in range(inputs.count()):
                el = inputs.nth(i)
                if el.is_visible():
                    target, owner = el, (owner_name, i)
                    break
            if target:
                break
        out["chosen"] = {"owner": owner}
        if target:
            target.click(timeout=5000)
            target.fill("under sink door organiser")
            page.wait_for_timeout(4000)
            cards = page.evaluate("""
                () => [...document.querySelectorAll('.origin-item')].map(e => ({
                    name: ((e.querySelector('.name')||{}).innerText||'').slice(0,80),
                    y: Math.round(e.getBoundingClientRect().y)
                }))
            """)
            out["cards"] = cards[:10]
            shot = os.path.join(BASE, "debug", "remix_probe.png")
            page.screenshot(path=shot)
    except Exception as e:
        out["error"] = repr(e)[:400]
    finally:
        try:
            page.close()
        except Exception:
            pass

with open(os.path.join(BASE, "debug", "remix_probe.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, indent=1)
print(json.dumps(out, indent=1)[:2000])
