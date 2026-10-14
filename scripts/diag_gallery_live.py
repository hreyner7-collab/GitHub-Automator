"""Diagnostic: WHY did the fetch come back with 1 photo when the strip shows 7?

Read-only. Opens the model page in the logged-in debug Chrome and prints, per
candidate image, which of the fetcher's rules accepted or rejected it — the
same predicates downloader._fetch_creality uses (isGalleryPhoto / isNonPhoto /
isTinyVariant / naturalWidth / bad-container), plus what the gallery root is and
which images live inside it.

Usage: .venv/Scripts/python.exe scripts/diag_gallery_live.py <model-url>
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

JS = r"""
() => {
    const src = (im) => im.currentSrc || im.src || im.getAttribute('data-src') || '';
    const isCreator = (s) => {
        const t = (s || '').split('?')[0];
        return t.indexOf('/crealityCloud/upload/') >= 0 || t.indexOf('/comp/model/') >= 0;
    };
    const isGalleryPhoto = (s) => isCreator(s) ||
        (s || '').split('?')[0].indexOf('/crealityCloud/common/') >= 0;
    const isNonPhoto = (s) => {
        const t = (s || '').split('?')[0];
        return t.indexOf('/faces/') >= 0 || t.indexOf('/crealityEmoji/') >= 0 ||
               t.indexOf('/crealityWebStaticImg/') >= 0 ||
               t.indexOf('no-folder') >= 0 || t.toLowerCase().indexOf('/logo') >= 0;
    };
    const tiny = (s) => /resize[,\/]+(?:h|w)_(?:[1-9]|[1-9][0-9]),/i.test(s);
    const REJECT = ['avatar', 'comment', 'review', 'adv', 'related', 'recommend',
                    'sponsor', 'creality-model-cover', 'print-config', 'list-3mf'];
    const badWhy = (el) => {
        let e = el;
        for (let i = 0; i < 10 && e; i++) {
            const c = (e.className || '').toString();
            const hit = REJECT.find(r => c.indexOf(r) >= 0);
            if (hit) return (e.tagName + '.' + c).slice(0, 60) + ' [' + hit + ']';
            e = e.parentElement;
        }
        return '';
    };
    const desc = (im) => {
        const s = src(im);
        return {src: s.slice(0, 120), creator: isCreator(s),
                gallery_photo: isGalleryPhoto(s), non_photo: isNonPhoto(s),
                tiny: tiny(s), natural: im.naturalWidth || 0,
                bad: badWhy(im), cls: ((im.closest('div') || {}).className || '').toString().slice(0, 40)};
    };
    const strip = document.querySelector('.swiper-thumb');
    const stripImgs = strip ? [...strip.querySelectorAll('img')] : [];
    const root = document.querySelector('div.model-gallery');
    const content = document.querySelector('.model-content');
    return {
        url: location.href,
        strip_found: !!strip,
        strip_cls: strip ? (strip.className || '').toString().slice(0, 60) : '',
        strip_imgs: stripImgs.map(desc),
        model_gallery_found: !!root,
        model_content_found: !!content,
        gallery_imgs: root ? [...root.querySelectorAll('img')].map(desc) : [],
        all_imgs_count: document.querySelectorAll('img').length,
        all_gallery_imgs: [...document.querySelectorAll('img')].map(desc)
            .filter(d => d.gallery_photo && !d.non_photo)
    };
}
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ensure_browser()
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        page = browser.contexts[0].new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        try:
            page.goto(sys.argv[1], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(9000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1200)
            out = page.evaluate(JS)
        finally:
            page.close()
    print(json.dumps(out, ensure_ascii=False, indent=1)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
