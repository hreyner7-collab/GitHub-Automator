"""Probe v3: where is the License picker in the create-model flow?

Dump: all section-ish headers, any 'License'/'Allow ...' texts, the green
'Fill in the information' control, and clicking Next-ish controls to see if
the License card appears. Saves debug/license_probe.json.
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
        out["flow"] = "create-model-new"

        headers = page.evaluate("""
            () => {
                const texts = [...document.querySelectorAll('h1,h2,h3,h4,div,span,p,label,b')]
                    .map(e => (e.innerText||'').trim())
                    .filter(t => t && t.length > 2 && t.length < 60);
                const uniq = [...new Set(texts)];
                return uniq.filter(t =>
                    /license|original|remix|non-original|visibility|price|allow|adaptations|commercial|sharing|redistribut|model info|parameters|cover|description|tag|category/i.test(t));
            }
        """)
        out["headers"] = headers[:40]

        # does the license question exist anywhere right now?
        out["has_license_q"] = page.evaluate(
            "() => /Allow adaptations/i.test(document.body.innerText)")
        out["has_license_word"] = page.evaluate(
            "() => (document.body.innerText.match(/License/g)||[]).length")

        # the green banner's second step chip
        chips = page.evaluate("""
            () => [...document.querySelectorAll('div,span')].filter(e => {
                const t = (e.innerText||'').trim();
                return t && t.length < 60 && /fill in the information|section \\d|step/i.test(t);
            }).map(e => ({t: (e.innerText||'').trim().slice(0,60),
                          cls: (e.className||'').toString().slice(0,60),
                          y: Math.round(e.getBoundingClientRect().y)}))
        """)
        out["chips"] = chips[:10]

        # fill remix + URL quickly so step 2's gate may open
        try:
            page.get_by_text("STL/CAD files or other types of 3MF file",
                             exact=False).first.click(timeout=4000)
            page.wait_for_timeout(1000)
        except Exception as e:
            out["stl_click"] = str(e)[:100]
        try:
            page.get_by_text("Remix Models", exact=False).first.click(timeout=4000)
            page.wait_for_timeout(1200)
            inp = page.locator("input[placeholder*='Paste the URL' i]").first
            inp.fill("https://www.crealitycloud.com/model-detail/"
                     "under-sink-door-organiser-kitchen-cabinet")
            page.wait_for_timeout(2500)
            card = page.locator(".origin-item").filter(
                has_text="Organiser").first
            if card.count():
                card.click(timeout=4000)
                page.wait_for_timeout(1500)
            out["remix_done"] = True
        except Exception as e:
            out["remix_err"] = repr(e)[:200]

        # NEXT-step controls: any 'Next'/'Continue'/arrow button, plus the Second dot
        nexts = page.evaluate("""
            () => [...document.querySelectorAll('button,div[role=button],.el-button,span,a')]
                .filter(e => /^(Next|Continue|Save|Confirm)$/i.test((e.innerText||'').trim()))
                .map(e => ({t: (e.innerText||'').trim(),
                            cls: (e.className||'').toString().slice(0,60),
                            y: Math.round(e.getBoundingClientRect().y)}))
        """)
        out["next_like"] = nexts[:10]

        # after any Next, does the license question appear?
        for n in nexts[:2]:
            try:
                el = page.locator(f"text={n['t']}").first
                el.click(timeout=3000)
                page.wait_for_timeout(2000)
                out["after_click_" + n["t"]] = page.evaluate(
                    "() => /Allow adaptations/i.test(document.body.innerText)")
                break
            except Exception:
                pass

        out["final_has_q"] = page.evaluate(
            "() => /Allow adaptations/i.test(document.body.innerText)")
        out["final_heads"] = page.evaluate("""
            () => [...new Set([...document.querySelectorAll('h1,h2,h3,h4,div,span,label')]
                .map(e => (e.innerText||'').trim())
                .filter(t => t && t.length > 3 && t.length < 70 &&
                             /license|allow|adaptations|commercial|sharing|redistribut|visibility/i.test(t)))]
        """)[:30]
        page.screenshot(path=os.path.join(BASE, "debug", "license_probe.png"),
                        full_page=True)
    except Exception as e:
        out["error"] = repr(e)[:300]
    finally:
        try:
            page.close()
        except Exception:
            pass

with open(os.path.join(BASE, "debug", "license_probe.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, indent=1)
print(json.dumps(out, indent=1)[:2200])
