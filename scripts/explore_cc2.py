"""Explore pass 2: the REAL test model (paper-towel-holder-kaso).

Focus:
  1. Extract full metadata: name, author, description, LICENSE (badge + text).
  2. Open the 'View STL/CAD Files' dropdown properly and click 'Download 3MF'.
  3. Capture the downloaded file into downloads/.
Saves debug/cc_page2.json.
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

OUT = os.path.join(BASE, "debug", "cc_page2.json")
DL = os.path.join(BASE, "downloads")
MODEL_URL = (sys.argv[1] if len(sys.argv) > 1 else
             "https://www.crealitycloud.com/model-detail/under-sink-door-organiser-kitchen-cabinet?profileId=6a9ec72126536a7fceeab91d")

ensure_browser()

findings = {"page": {}, "download": {}}

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    page = ctx.new_page()
    dl_events = []
    page.on("download", lambda d: dl_events.append(d))
    try:
        page.goto(MODEL_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        for _ in range(3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(800)

        data = page.evaluate("""
            () => {
                const body = document.body.innerText || '';
                // ---- license: heading 'License' + everything until the next
                // heading-ish section; also grab CC badge <img> alt/src
                let license_text = '';
                let license_badges = [];
                const all = [...document.querySelectorAll('h1,h2,h3,h4,div,span,p,img')];
                const licHead = all.find(e => (e.innerText||'').trim() === 'License');
                if (licHead) {
                    let el = licHead.parentElement;
                    const stop = /Print Settings|Comments|Reviews|Boost|Remix/i;
                    for (let i = 0; i < 12 && el; i++) {
                        el = el.nextElementSibling || (el.parentElement ? el.parentElement.nextElementSibling : null);
                        if (!el) break;
                        const t = (el.innerText||'').trim();
                        if (!t) continue;
                        if (stop.test(t) && t.length > 40) break;
                        license_text += (license_text ? '\\n' : '') + t.slice(0, 500);
                        if (license_text.length > 700) break;
                    }
                }
                for (const img of all) {
                    const a = (img.alt||'') + ' ' + (img.src||'');
                    if (/creativecommons|license|\\bCC\\b|by-sa|by-nc|by-nd|cc0/i.test(a))
                        license_badges.push({alt: img.alt||'', src: (img.src||'').slice(0,160)});
                }
                license_badges = license_badges.slice(0, 8);
                // ---- description: og meta is the reliable one
                const desc = (document.querySelector('meta[property="og:description"]')||{}).content || '';
                // ---- gallery images (model photos only, large)
                const imgs = [...new Set(Array.from(document.querySelectorAll('img'))
                    .map(i => i.currentSrc || i.src || '')
                    .filter(s => s.startsWith('http') && !/avatar|logo|icon|emoji/i.test(s)))];
                // ---- author
                const author = (body.match(/\\n([A-Za-z0-9_\\-\\.]{2,24})\\n\\s*(?:Follow|Message)/) || [])[1] || '';
                // ---- download button candidates with rects
                const btns = [...document.querySelectorAll('button,div,a,span,[role=button]')]
                    .filter(e => /View STL\\/CAD Files|Download 3MF/i.test((e.innerText||'').trim()) && (e.innerText||'').trim().length < 40)
                    .map(e => { const r = e.getBoundingClientRect();
                        return {text: (e.innerText||'').trim().slice(0,40), tag: e.tagName, cls: (e.className||'').toString().slice(0,80),
                                x: r.x, y: r.y, w: r.width, h: r.height}; });
                return {title: document.title, url: location.href, desc, author,
                        license_text: license_text.slice(0, 800), license_badges,
                        imgs: imgs.slice(0, 25), btns: btns.slice(0, 10)};
            }
        """)
        findings["page"] = data
        print("TITLE:", data["title"][:90])
        print("AUTHOR:", data["author"])
        print("DESC:", (data["desc"] or "")[:120])
        print("LICENSE TEXT:", (data["license_text"] or "(none)")[:300].replace("\n", " | "))
        print("BADGES:", json.dumps(data["license_badges"])[:300])
        print("IMAGES:", len(data["imgs"]))
        print("BUTTONS:")
        for b in data["btns"]:
            print("   [%s] %r at (%d,%d) %dx%d" % (b["tag"], b["text"], b["x"], b["y"], b["w"], b["h"]))
        if not data["btns"]:
            # diagnostics: what IS on the page?
            diag = page.evaluate("""
                () => ({
                    iframes: [...document.querySelectorAll('iframe')].map(f => f.src).slice(0,8),
                    body_head: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0, 800),
                    dl_texts: [...document.querySelectorAll('button,div,a,span,[role=button]')]
                        .map(e => (e.innerText||'').trim())
                        .filter(t => /STL|3MF|Download|License/i.test(t) && t.length < 60)
                        .slice(0, 25),
                })
            """)
            print("DIAG iframes:", diag["iframes"])
            print("DIAG body_head:", diag["body_head"][:400])
            print("DIAG dl_texts:", json.dumps(diag["dl_texts"], indent=0)[:800])
            try:
                page.screenshot(path=os.path.join(BASE, "debug", "cc_model_page.png"), full_page=False)
                print("screenshot -> debug/cc_model_page.png")
            except Exception:
                pass

        # ---- click the split button's MAIN part (left of the chevron) ------
        btn = next((b for b in data["btns"] if b["w"] > 80 and b["h"] > 20), None)
        if btn:
            page.mouse.click(btn["x"] + btn["w"] * 0.4, btn["y"] + btn["h"] / 2)
            page.wait_for_timeout(1500)
            menu = page.evaluate("""
                () => {
                    // dropdown menus render in a floating portal; dump any
                    // element whose visible text is exactly a menu item
                    const want = ['Download 3MF', 'View STL/CAD Files', 'Open in Creality Print'];
                    const out = [];
                    for (const el of document.querySelectorAll('li,div,span,a,button,p')) {
                        const t = (el.innerText||'').trim();
                        if (want.includes(t)) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0 && r.width < 400)
                                out.push({text: t, tag: el.tagName, x: r.x + r.width/2, y: r.y + r.height/2});
                        }
                    }
                    return out;
                }
            """)
            print("MENU AFTER CLICK:", json.dumps(menu)[:400])
            dl_item = next((m for m in menu if m["text"] == "Download 3MF"), None)
            if dl_item:
                page.mouse.click(dl_item["x"], dl_item["y"])
                print("clicked Download 3MF; waiting for download...")
                for _ in range(45):
                    if dl_events:
                        break
                    page.wait_for_timeout(1000)
                if dl_events:
                    d = dl_events[0]
                    dest = os.path.join(DL, (d.suggested_filename or "cc_model.3mf").replace("/", "_"))
                    d.save_as(dest)
                    findings["download"] = {
                        "suggested_name": d.suggested_filename, "saved_to": dest,
                        "size_bytes": os.path.getsize(dest) if os.path.exists(dest) else 0,
                    }
                    print("SAVED:", dest, findings["download"]["size_bytes"], "bytes")
                else:
                    print("NO download event fired")
            else:
                print("no 'Download 3MF' item appeared")
    except Exception as e:
        print("exploration failed:", e)
    finally:
        try:
            page.close()
        except Exception:
            pass

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(findings, f, indent=2)
print("saved ->", OUT)
