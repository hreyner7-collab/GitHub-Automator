"""Prove the dashboard works end to end through the browser UI.

1. every onclick handler exists (the generator section is gone by design)
2. the guard buttons give immediate feedback instead of doing nothing
3. Analytics toggle + range switch behave from their natural state
4. a REAL fetch (test model) renders photo tiles whose images actually load

Saves debug/buttons_check.json. Exit 0 only if everything passes.
"""
import json
import os
import sys

try:                      # button labels carry emoji; the cp1252 console
    sys.stdout.reconfigure(errors="replace")   # would otherwise crash the check
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

TEST_URL = ("https://www.crealitycloud.com/model-detail/"
            "under-sink-door-organiser-kitchen-cabinet")
ensure_browser()
out = {"handlers": [], "clicks": [], "console_errors": [], "tiles": {}}
fails = []
save_fetch = os.environ.get("SKIP_FETCH") != "1"

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    page = ctx.new_page()
    page.on("pageerror", lambda e: out["console_errors"].append("pageerror: " + str(e)[:150]))
    page.on("console", lambda m: out["console_errors"].append(
        "console: " + m.text[:150]) if m.type == "error" else None)
    try:
        page.goto("http://127.0.0.1:8000/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)

        # 1) handlers
        out["handlers"] = page.evaluate("""
            () => [...document.querySelectorAll('[onclick]')].map(e => {
                const fn = (e.getAttribute('onclick') || '').split('(')[0].trim();
                return {id: e.id || '', text: (e.innerText||'').trim().slice(0,38),
                        fn, defined: typeof window[fn] === 'function'};
            })
        """)
        for h in out["handlers"]:
            if not h["defined"]:
                fails.append("handler missing: %s (%r)" % (h["fn"], h["text"]))
        if page.evaluate("() => !!document.getElementById('generate-file')"):
            fails.append("the build/generate card is still on the page")
        for fn in ("generateFile", "acceptGenerated"):
            if page.evaluate("() => typeof window['%s']" % fn) == "function":
                fails.append("generator function still defined: %s" % fn)

        def feedback(sel, status_id, label):
            page.evaluate("""() => ['preview-section','upload-section']
                .forEach(id => { const el = document.getElementById(id);
                                 if (el) el.style.display = 'block'; })""")
            before = page.inner_text("#" + status_id).strip()
            page.click(sel, timeout=5000)
            page.wait_for_timeout(700)
            after = page.inner_text("#" + status_id).strip()
            ok = bool(after) and after != before
            out["clicks"].append({"label": label, "status": after[:140], "responded": ok})
            if not ok:
                fails.append("no feedback from %s" % label)

        feedback("#fetch-btn", "fetch-status", "Fetch (empty URL)")
        feedback("#regen-btn", "fetch-status", "Regenerate (no photos yet)")
        feedback("#upload-btn", "upload-status", "Upload (nothing loaded)")

        # 3) analytics from its natural state
        page.click("#analytics-btn", timeout=5000)
        page.wait_for_timeout(900)
        vis = page.evaluate("() => getComputedStyle(document.getElementById('analytics-section')).display")
        out["clicks"].append({"label": "Analytics toggle", "status": "display=" + vis,
                              "responded": vis == "block"})
        if vis != "block":
            fails.append("Analytics button did not open the panel (display=%s)" % vis)
        page.click('#an-range button[data-days="7"]', timeout=5000)
        page.wait_for_timeout(500)
        active = page.evaluate("""() => [...document.querySelectorAll('#an-range button')]
            .filter(b => b.classList.contains('active')).map(b => b.dataset.days)""")
        out["clicks"].append({"label": "Range 7D", "status": "active=" + str(active),
                              "responded": active == ["7"]})
        if active != ["7"]:
            fails.append("range button did not switch")

        # 4) REAL fetch through the UI, then check the tiles really show photos
        if save_fetch:
            page.fill("#url-input", TEST_URL)
            page.click("#fetch-btn", timeout=5000)
            for _ in range(60):                     # up to ~3 min
                page.wait_for_timeout(3000)
                sec = page.evaluate("() => getComputedStyle(document.getElementById('preview-section')).display")
                btn_disabled = page.evaluate("() => document.getElementById('fetch-btn').disabled")
                if sec == "block" and not btn_disabled:
                    break
            page.wait_for_timeout(2500)
            out["fetch_status"] = page.inner_text("#fetch-status").strip()[:200]
            out["tiles"] = page.evaluate("""
                () => {
                    const imgs = [...document.querySelectorAll('#image-container img')];
                    return {count: imgs.length,
                            loaded: imgs.filter(i => i.complete && i.naturalWidth > 0).length,
                            srcs: imgs.map(i => i.getAttribute('src')).slice(0, 3),
                            alts: imgs.map(i => i.getAttribute('alt')).slice(0, 3)};
                }
            """)
            if out["tiles"].get("count", 0) == 0:
                fails.append("fetch rendered no photo tiles")
            elif out["tiles"].get("loaded", 0) != out["tiles"]["count"]:
                fails.append("only %s of %s tile images actually loaded"
                             % (out["tiles"].get("loaded"), out["tiles"]["count"]))
            page.screenshot(path=os.path.join(BASE, "debug", "buttons_check.png"),
                            full_page=True)
    except Exception as e:
        fails.append("harness error: %r" % e)
    finally:
        try:
            page.close()
        except Exception:
            pass

out["fails"] = fails
with open(os.path.join(BASE, "debug", "buttons_check.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, indent=1, ensure_ascii=False)
for h in out["handlers"]:
    print("%-18s %-38s %s" % (h["fn"], h["text"], "ok" if h["defined"] else "MISSING"))
for c in out["clicks"]:
    print("click %-30s -> %s" % (c["label"], c["status"]))
print("tiles:", json.dumps(out.get("tiles", {}), ensure_ascii=False))
print("console errors:", out["console_errors"] or "none")
print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)
