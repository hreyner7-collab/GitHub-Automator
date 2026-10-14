"""Where each photo of a PUBLISHED listing sits (slide / thumbnail / cover)."""
from __future__ import annotations
import json, sys
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from playwright.sync_api import sync_playwright   # noqa: E402
from uploader import ensure_browser, CDP_URL      # noqa: E402

JS = r"""
() => {
    const rel = (s) => (s || '').split('?')[0].replace('https://pic2-cdn.creality.com','');
    const chain = (el) => {
        const out = []; let e = el;
        for (let i = 0; i < 7 && e; i++) {
            out.push((e.tagName||'').toLowerCase() + (e.className ? '.' +
                (e.className||'').toString().trim().split(/\s+/).slice(0,2).join('.') : ''));
            e = e.parentElement;
        }
        return out.join(' < ');
    };
    const src = (im) => im.currentSrc || im.src || '';
    const rows = [];
    for (const im of document.querySelectorAll('div.model-gallery img, .swiper-thumb img, .model-cover img')) {
        rows.push({src: rel(src(im)), w: im.naturalWidth, h: im.naturalHeight,
                   in_slide: !!im.closest('div.cover'), in_thumb: !!im.closest('div.thumb'),
                   alt: (im.alt||'').slice(0,40), chain: chain(im)});
    }
    const blocks = [...document.querySelectorAll('div,section')]
        .filter(e => /Model Images|model-image|cover/i.test((e.className||'').toString()))
        .map(e => ((e.className||'').toString().slice(0,60))).slice(0, 12);
    return {rows: rows, blocks: blocks, og: (document.querySelector('meta[property="og:image"]')||{}).content || ''};
}
"""
ensure_browser()
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp(CDP_URL)
    url = sys.argv[1]
    p = b.contexts[0].new_page(); p.set_viewport_size({"width": 1440, "height": 900})
    try:
        p.goto(url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(7000); p.evaluate("window.scrollTo(0,0)"); p.wait_for_timeout(1000)
        info = p.evaluate(JS)
    finally:
        p.close()
print("og:image:", info["og"].replace("https://pic2-cdn.creality.com", ""))
print("image blocks seen:", info["blocks"][:6])
print("%-4s %-58s %-10s %-6s %-6s %s" % ("#", "image", "size", "slide", "thumb", "container"))
for i, r in enumerate(info["rows"]):
    print("%-4d %-58s %-10s %-6s %-6s %s" % (i, r["src"][:58], "%dx%d" % (r["w"], r["h"]),
          r["in_slide"], r["in_thumb"], r["chain"][:70]))
Path(BASE / "debug" / "published_listing_structure.json").write_text(
    json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
