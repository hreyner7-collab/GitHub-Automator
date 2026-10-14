"""Explore a Creality Cloud model page with the LOGGED-IN debug Chrome.

Goals (evidence for the re-upload feature):
  1. Find the test model (Paper Towel Holder by Hasan__) via site search.
  2. Dump its metadata: title, og:description, author, license text, images.
  3. Map the download UI: 'View STL/CAD Files' button -> 'Download 3MF' item.
  4. Click Download 3MF once, capture what URL/requests fire, save the file.

Everything learned is printed and saved to debug/cc_page.json.
Usage:  .venv/Scripts/python.exe scripts/explore_cc.py [model-detail-slug-or-url]
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

OUT = os.path.join(BASE, "debug", "cc_page.json")
DL = os.path.join(BASE, "downloads")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
os.makedirs(DL, exist_ok=True)

QUERY = "paper towel holder"

ensure_browser()

findings = {"search_links": [], "page": {}, "download": {}}

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]

    # ── 1) find the model via the site's own search ─────────────────────────
    page = ctx.new_page()
    api_hits = []
    page.on("request", lambda r: api_hits.append(r.url)
            if any(k in r.url.lower() for k in ("api", "model-detail", "search"))
            else None)
    try:
        page.goto("https://www.crealitycloud.com/", wait_until="domcontentloaded",
                  timeout=45000)
        page.wait_for_timeout(4000)
        box = page.locator("input[placeholder*='Search' i]").first
        box.click(timeout=8000)
        box.fill(QUERY)
        page.keyboard.press("Enter")
        page.wait_for_timeout(6000)
        # collect model-detail links from the results page
        links = page.evaluate("""
            () => [...document.querySelectorAll('a[href*=\\'model-detail\\'], a[href*="model-detail"]')]
                    .map(a => ({href: a.href, text: (a.innerText||'').replace(/\\s+/g,' ').trim().slice(0,80)}))
                    .filter(x => x.href)
        """)
        seen = set()
        for l in links:
            if l["href"] not in seen:
                seen.add(l["href"])
                findings["search_links"].append(l)
        print("SEARCH LINKS (%d):" % len(findings["search_links"]))
        for l in findings["search_links"][:15]:
            print("  ", l["href"], "|", l["text"])
        page.close()
    except Exception as e:
        print("search failed:", e)
        try:
            page.close()
        except Exception:
            pass

    # ── 2) open the model page (from search hit, else from argv) ────────────
    target = sys.argv[1] if len(sys.argv) > 1 else (
        findings["search_links"][0]["href"] if findings["search_links"] else None)
    if not target:
        print("NO MODEL LINK FOUND — pass the model-detail URL as argv[1]")
        json.dump(findings, open(OUT, "w", encoding="utf-8"), indent=2)
        sys.exit(2)
    if not target.startswith("http"):
        target = "https://www.crealitycloud.com/model-detail/" + target
    print("OPENING:", target)

    page = ctx.new_page()
    dl_events = []
    page.on("download", lambda d: dl_events.append(d))
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        for _ in range(2):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1200)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(600)

        data = page.evaluate("""
            () => {
                const meta = (sel) => { const m = document.querySelector(sel); return m ? m.content : ''; };
                const body = document.body.innerText || '';
                // license: find the 'License' heading and grab the text after it
                let license = '';
                const heads = [...document.querySelectorAll('h1,h2,h3,div,span')]
                    .filter(e => (e.innerText||'').trim() === 'License');
                if (heads.length) {
                    let el = heads[0];
                    for (let i = 0; i < 6 && el; i++) {
                        el = el.nextElementSibling || el.parentElement && el.parentElement.nextElementSibling;
                        const t = el ? (el.innerText||'').trim() : '';
                        if (t && t !== 'License') { license = t.slice(0, 400); break; }
                    }
                }
                if (!license) {
                    const m = body.match(/License[\\s\\S]{0,60}(CC[- ]?(?:BY|ZERO|0)[^\\n]{0,80})/i);
                    license = m ? m[1] : '';
                }
                // download UI candidates
                const btns = [...document.querySelectorAll('button,div,a,span,[role=button]')]
                    .map(e => (e.innerText||'').trim())
                    .filter(t => /Download 3MF|View STL\\/CAD|Open in Creality Print/.test(t) && t.length < 40);
                // author: first avatar-ish block near the top
                const author = (body.match(/\\n([A-Za-z0-9_\\-\\.]{2,24})\\n\\s*(?:Follow|Message)/) || [])[1] || '';
                const imgs = Array.from(document.querySelectorAll('img')).map(i => ({
                    src: i.currentSrc || i.src || '', w: i.naturalWidth||0, h: i.naturalHeight||0
                })).filter(i => i.src.startsWith('http') && i.w >= 300);
                return {
                    title: document.title,
                    og_desc: meta('meta[property="og:description"]') || meta('meta[name="description"]'),
                    author, license, btns: [...new Set(btns)].slice(0, 12),
                    url: location.href,
                    imgs: imgs.slice(0, 20),
                    body_head: body.replace(/\\s+/g,' ').slice(0, 600),
                };
            }
        """)
        findings["page"] = data
        print("TITLE:", data["title"])
        print("AUTHOR:", data["author"])
        print("LICENSE TEXT:", (data["license"] or "(none found)")[:200])
        print("BUTTONS:", data["btns"])
        print("IMAGES:", len(data["imgs"]))
        print("API/XHR HITS:", len(api_hits))

        # ── 3) click Download 3MF and capture the transfer ──────────────────
        info = page.evaluate("""
            () => {
                const els = [...document.querySelectorAll('button,div,a,span,[role=button]')]
                    .filter(e => /^View STL\\/CAD Files/i.test((e.innerText||'').trim()));
                for (const el of els) {
                    el.scrollIntoView({block:'center'});
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.y > 0 && r.y < window.innerHeight)
                        return {x: r.x + r.width/2, y: r.y + r.height/2, label: (el.innerText||'').trim()};
                }
                return null;
            }
        """)
        if info:
            print("CLICK:", info["label"])
            page.mouse.click(info["x"], info["y"])
            page.wait_for_timeout(2000)
            menu = page.evaluate("""
                () => {
                    const items = [...document.querySelectorAll('li,div,span,a,button')]
                        .map(e => (e.innerText||'').trim())
                        .filter(t => /^(Download 3MF|View STL\\/CAD Files|Open in Creality Print)$/.test(t));
                    return [...new Set(items)];
                }
            """)
            print("MENU ITEMS:", menu)
            item = page.evaluate("""
                () => {
                    const els = [...document.querySelectorAll('li,div,span,a,button')]
                        .filter(e => (e.innerText||'').trim() === 'Download 3MF');
                    for (const el of els) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.y >= 0)
                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                    }
                    return null;
                }
            """)
            if item:
                page.mouse.click(item["x"], item["y"])
                print("clicked Download 3MF; waiting for download...")
                for _ in range(40):
                    if dl_events:
                        break
                    page.wait_for_timeout(1000)
                if dl_events:
                    d = dl_events[0]
                    dest = os.path.join(DL, (d.suggested_filename or "cc_model.3mf").replace("/", "_"))
                    d.save_as(dest)
                    findings["download"] = {
                        "suggested_name": d.suggested_filename,
                        "saved_to": dest,
                        "size_bytes": os.path.getsize(dest) if os.path.exists(dest) else 0,
                    }
                    print("SAVED:", dest, findings["download"]["size_bytes"], "bytes")
                else:
                    print("no download event fired within 40s")
        else:
            print("no 'View STL/CAD Files' button found")
    except Exception as e:
        print("page exploration failed:", e)
    finally:
        findings["api_hits"] = api_hits[-60:]
        try:
            page.close()
        except Exception:
            pass

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(findings, f, indent=2)
print("saved findings ->", OUT)
