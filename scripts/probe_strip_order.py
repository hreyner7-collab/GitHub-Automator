"""Inspector: what the model's OWN gallery strip holds, in order.

Opens a Creality Cloud model page in the user's logged-in debug Chrome and
dumps:
  * every `.swiper-thumb img` in DOM order  -> the authoritative photo order
  * every `/comp/model/` (creator) image with the container it lives in, so
    avatars / promo rails can be told apart from the model's own photos
Writes debug/strip_order.json and prints a short table.

Usage:  .venv/Scripts/python.exe scripts/probe_strip_order.py <model-url>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright   # noqa: E402

from uploader import CDP_URL                       # noqa: E402

BASE = Path(__file__).resolve().parent.parent
DEBUG = BASE / "debug"

PROBE_JS = r"""
() => {
    const src = (im) => (im.currentSrc || im.src || im.getAttribute('data-src') || '');
    const chain = (el) => {
        const out = [];
        let e = el;
        for (let i = 0; i < 8 && e; i++) {
            out.push((e.tagName || '').toLowerCase() +
                     (e.className ? '.' + (e.className || '').toString()
                         .trim().split(/\s+/).slice(0, 3).join('.') : ''));
            e = e.parentElement;
        }
        return out.join(' < ');
    };
    const strip = document.querySelector('.swiper-thumb');
    const stripImgs = strip ? [...strip.querySelectorAll('img')] : [];
    const cover = document.querySelector('.swiper-slide-active img');
    const creators = [...document.querySelectorAll('img')].filter(im => {
        const s = src(im).split('?')[0];
        return s.indexOf('/comp/model/') >= 0 || s.indexOf('/crealityCloud/upload/') >= 0;
    });
    return JSON.stringify({
        url: location.href,
        strip_found: !!strip,
        strip_count: stripImgs.length,
        strip: stripImgs.map((im, i) => ({
            i: i, src: src(im).split('?')[0],
            w: im.naturalWidth || 0, h: im.naturalHeight || 0,
            box: (() => { const r = im.getBoundingClientRect();
                          return [Math.round(r.x), Math.round(r.y),
                                  Math.round(r.width), Math.round(r.height)]; })(),
            chain: chain(im),
        })),
        cover_src: cover ? src(cover).split('?')[0] : '',
        creators: creators.map(im => ({
            src: src(im).split('?')[0],
            w: im.naturalWidth || 0, h: im.naturalHeight || 0,
            chain: chain(im),
        })),
    });
}
"""


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://www.crealitycloud.com/model-detail/knitted-cactus"
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        # a real, normal-sized window: lazy loading depends on layout
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(4000)
        page.evaluate("window.scrollTo(0, 400)")
        page.wait_for_timeout(1500)
        data = json.loads(page.evaluate(PROBE_JS))
        page.close()

    DEBUG.mkdir(exist_ok=True)
    (DEBUG / "strip_order.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8")

    print(f"strip found: {data['strip_found']}  thumbs: {data['strip_count']}")
    print(f"active cover: {data['cover_src'].split('/')[-1]}")
    for t in data["strip"]:
        name = t["src"].split("/")[-1]
        print(f"  [{t['i']}] {name:<44} {t['w']}x{t['h']:<5} "
              f"box={t['box']}  {t['chain'][:60]}")
    print(f"creator images on page: {len(data['creators'])}")
    for c in data["creators"]:
        print(f"   {c['src'].split('/')[-1]:<44} {c['w']}x{c['h']:<5} {c['chain'][:60]}")
    print("wrote debug/strip_order.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
