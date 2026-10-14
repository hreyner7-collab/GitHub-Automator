"""Read a PUBLISHED Creality listing and count the photos it actually carries.

The user's complaint is photos going missing on the upload, so the only real
proof is the live listing: its cover image, the slides, and the thumbnail strip.
Read-only (its own page, closed afterwards); writes debug/published_listing.json.

Usage:  .venv/Scripts/python.exe scripts/read_published_listing.py <url> [url ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from playwright.sync_api import sync_playwright   # noqa: E402
from uploader import ensure_browser, CDP_URL      # noqa: E402

READ_JS = r"""
() => {
    const src = (im) => im.currentSrc || im.src || im.getAttribute('data-src') || '';
    const rel = (s) => (s || '').split('?')[0];
    const root = document.querySelector('div.model-gallery');
    const strip = document.querySelector('.swiper-thumb');
    const slides = [];
    if (root) {
        for (const im of root.querySelectorAll('img')) {
            const k = rel(src(im));
            if (k && slides.indexOf(k) < 0) slides.push(k);
        }
    }
    const stripImgs = [];
    if (strip) for (const im of strip.querySelectorAll('img')) {
        const k = rel(src(im));
        if (k && stripImgs.indexOf(k) < 0) stripImgs.push(k);
    }
    const covers = [];
    for (const im of document.querySelectorAll(
            '.model-cover img, .swiper-cover img, .cover img')) {
        const k = rel(src(im));
        if (k && covers.indexOf(k) < 0) covers.push(k);
    }
    const og = (document.querySelector('meta[property="og:image"]') || {}).content || '';
    return {
        title: document.title,
        url: location.href,
        gallery_root: !!root,
        slides: slides,
        strip: stripImgs,
        strip_thumbs: strip ? strip.querySelectorAll('img').length : 0,
        covers: covers,
        og_image: og,
        // the counts a person reads on the page
        page_text_counts: ((document.body.innerText || '')
            .match(/\d+\s*(?:images?|photos?)/gi) || []).slice(0, 6),
        all_imgs: document.querySelectorAll('img').length,
    };
}
"""

ensure_browser()
out = {}
with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    for url in sys.argv[1:]:
        page = browser.contexts[0].new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(7000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1200)
            info = page.evaluate(READ_JS)
        finally:
            page.close()
        out[url] = info
        print("=" * 78)
        print("url        ", info["url"])
        print("title      ", info["title"][:100])
        print("gallery    ", "div.model-gallery present" if info["gallery_root"] else "NO gallery block")
        print("slides     ", len(info["slides"]), "distinct")
        print("strip      ", len(info["strip"]), "distinct /", info["strip_thumbs"], "thumbnails")
        print("covers     ", len(info["covers"]))
        print("og:image   ", info["og_image"][:110])
        print("page counts", info["page_text_counts"])
        for s in info["slides"][:12]:
            print("     slide:", s.replace("https://pic2-cdn.creality.com", "")[:96])
        for s in info["strip"][:12]:
            print("     thumb:", s.replace("https://pic2-cdn.creality.com", "")[:96])

dest = BASE / "debug" / "published_listing.json"
dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print("\nwrote", dest)
