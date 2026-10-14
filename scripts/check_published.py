"""Confirm the R5 publish landed: open the creator's model list in the
logged-in Chrome and look for 'Under Sink Door Organiser'.
Saves debug/publish_check.json + debug/publish_check.png.
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

ensure_browser()
NAME = "Under Sink Door Organiser"
out = {"found": False, "candidates": []}

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    page = ctx.new_page()
    try:
        # resolve the account id from the account-settings redirect
        # (same trick analytics uses: id.creality.com -> /account-settings/<id>)
        try:
            page.goto("https://id.creality.com/", wait_until="domcontentloaded",
                      timeout=30000)
            page.wait_for_timeout(5000)
            import re as _re
            m = _re.search(r"account-settings/(\w+)", page.url)
            account_id = m.group(1) if m else None
        except Exception:
            account_id = None
        out["account_id"] = account_id
        targets = []
        if account_id:
            targets.append("https://www.crealitycloud.com/user/%s/model"
                           % account_id)
        targets.append("https://www.crealitycloud.com/")
        for u in targets:
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(6000)
                for _ in range(6):
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(400)
                body = page.evaluate("() => document.body.innerText")
                if NAME.lower() in body.lower():
                    out["found"] = True
                    out["via"] = u
                links = page.evaluate("""
                    () => [...document.querySelectorAll('a[href*="model-detail"]')]
                        .map(a => ({href: a.href,
                                    t: (a.innerText||'').replace(/\\s+/g,' ').trim().slice(0,60)}))
                """)
                if links:
                    out["candidates"] = links[:25]
                    break
            except Exception as e:
                out.setdefault("errors", []).append(str(e)[:120])
        body = page.evaluate("() => (document.body.innerText||'').slice(0, 400)")
        out["page_head"] = body.replace("\\n", " | ")[:400]
        page.screenshot(path=os.path.join(BASE, "debug", "publish_check.png"),
                        full_page=False)
    except Exception as e:
        out["error"] = repr(e)[:300]
    finally:
        try:
            page.close()
        except Exception:
            pass

with open(os.path.join(BASE, "debug", "publish_check.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, indent=1)
print(json.dumps(out, indent=1)[:1800])
