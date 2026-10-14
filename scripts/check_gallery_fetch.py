"""Oracle: the photos the fetcher takes ARE the model's own gallery, in order.

For each URL:
  * read the model's own gallery from the live DOM — the thumbnail strip
    (`.swiper-thumb`, the inspector's view) first, then the gallery slides
    inside `div.model-gallery` — and ignore everything else on the page (the
    slicer PLATE previews in `.list-3mf`, related-model cards, avatars, ads)
  * run the real fetcher (`downloader.fetch`) on the same URL
  * assert: same set (nothing taken from outside the gallery, nothing missed),
    same count, and the fetched order starts with the strip's order

Usage: python scripts/check_gallery_fetch.py <url> [url ...]
Exit 0 only if every URL matches.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402
import downloader  # noqa: E402

# Same rules the pipeline applies (downloader._fetch_creality -> gallery_js):
#  * model photos live under /crealityCloud/upload/, /comp/model/ OR
#    /crealityCloud/common/ (measured 2026-10-09)
#  * reject promos/junk paths, plate previews, related rails, avatars, ads
#  * a photo sits in a slide (div.cover) or a strip thumbnail (div.thumb)
COUNT_JS = r"""
() => {
    const rel = (s) => (s || '').split('?')[0]
        .replace('https://pic2-cdn.creality.com', '').slice(0, 80);
    const isCreator = (s) => {
        const t = (s || '').split('?')[0];
        return t.indexOf('/crealityCloud/upload/') >= 0 ||
               t.indexOf('/comp/model/') >= 0;
    };
    // /crealityCloud/common/ holds REAL gallery photos too — re-measured
    // 2026-10-09: six of the dumpling page's seven photos live there, which is
    // exactly why a fetch came back with one image. Only these folders never
    // hold a model photo.
    const isGalleryPhoto = (s) => isCreator(s) ||
        (s || '').split('?')[0].indexOf('/crealityCloud/common/') >= 0;
    const isNonPhoto = (s) => {
        const t = (s || '').split('?')[0];
        return t.indexOf('/faces/') >= 0 || t.indexOf('/crealityEmoji/') >= 0 ||
               t.indexOf('/crealityWebStaticImg/') >= 0 ||
               t.indexOf('no-folder') >= 0 || t.toLowerCase().indexOf('/logo') >= 0;
    };
    const REJECT = ['avatar', 'comment', 'review', 'adv', 'related', 'recommend',
                    'sponsor', 'creality-model-cover', 'print-config', 'list-3mf'];
    const bad = (el) => {
        let e = el;
        for (let i = 0; i < 10 && e; i++) {
            const c = (e.className || '').toString();
            if (REJECT.some(r => c.indexOf(r) >= 0)) return true;
            e = e.parentElement;
        }
        return false;
    };
    const src = (im) => im.currentSrc || im.src || im.getAttribute('data-src') || '';
    const strip = document.querySelector('.swiper-thumb');
    const strip_paths = strip ? [...strip.querySelectorAll('img')]
        .filter(im => isGalleryPhoto(src(im)) && !isNonPhoto(src(im)) && !bad(im))
        .map(im => rel(src(im))) : [];
    const root = document.querySelector('div.model-gallery');
    const ordered = [];
    for (const s of strip_paths) if (ordered.indexOf(s) < 0) ordered.push(s);
    if (root) {
        for (const im of root.querySelectorAll('img')) {
            const s = src(im);
            if (!isGalleryPhoto(s) || isNonPhoto(s)) continue;
            if ((im.naturalWidth || 0) < 200 || bad(im)) continue;
            if (!im.closest('div.cover, div.thumb')) continue;
            const k = rel(s);
            if (ordered.indexOf(k) < 0) ordered.push(k);
        }
    }
    return {strip: strip_paths,
            strip_thumbs: strip ? strip.querySelectorAll('img').length : 0,
            ordered: ordered};
}
"""

ensure_browser()
failures = 0
for url in sys.argv[1:]:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        page = browser.contexts[0].new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            page.evaluate("window.scrollTo(0,0)")
            page.wait_for_timeout(800)
            on_page = page.evaluate(COUNT_JS)
        finally:
            page.close()

    res = downloader.fetch(url)
    got = res.get("gallery", {})
    fetched = [p for p in got.get("paths", [])][:12]
    strip = on_page["strip"]
    ordered = on_page["ordered"]

    print("==", url)
    print("   strip thumbnails:", on_page["strip_thumbs"],
          "| gallery photos on page:", len(ordered),
          "| fetched:", len(fetched))
    checks = [
        ("nothing taken from outside the model's own gallery",
         set(fetched) <= set(ordered)),
        ("every gallery photo collected", set(ordered) <= set(fetched)),
        ("fetched count == gallery count", len(fetched) == len(ordered)),
        ("fetched order starts with the strip's order",
         fetched[:len(strip)] == strip),
        ("gallery scope recorded by the fetcher",
         bool(got.get("all_creator"))),
    ]
    for name, ok in checks:
        print("   %s %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            failures += 1
            print("        page:", ordered)
            print("        fetch:", fetched)
    for p in fetched:
        print("      file:", p[:78])

print("\n%s" % ("ALL GALLERY CHECKS PASSED" if not failures
                else "%d GALLERY CHECK(S) FAILED" % failures))
sys.exit(1 if failures else 0)
