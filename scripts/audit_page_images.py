"""Audit: every image under /crealityCloud/upload/ on a model page, labelled
'taken' (the model's own gallery) or skipped with the exact reason.

Usage: python scripts/audit_page_images.py <model-url> [...]
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL  # noqa: E402

JS = r"""
() => {
    const rows = [];
    for (const im of document.querySelectorAll('img')) {
        const s = im.currentSrc || im.src || '';
        if (s.indexOf('/crealityCloud/upload/') < 0) continue;
        const chain = [];
        let e = im;
        for (let i = 0; i < 6 && e; i++) {
            chain.push((e.className || '').toString());
            e = e.parentElement;
        }
        const inModel = chain.some(c => c.indexOf('model-content') >= 0);
        const adish = /avatar|comment|review|adv|related|recommend|sponsor/i.test(chain.join(' '));
        const tiny = /resize[,\/]+(?:h|w)_(?:[1-9]|[1-9][0-9]),/i.test(s);
        rows.push({src: s.split('?')[0], q: s.split('?')[1] || '',
                   nw: im.naturalWidth, nh: im.naturalHeight,
                   inModel, adish, tiny,
                   cls: chain[0].slice(0, 40),
                   section: chain.slice(1, 4).join(' > ').slice(0, 70)});
    }
    return rows;
}
"""

ensure_browser()
for url in sys.argv[1:]:
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP_URL)
        page = b.contexts[0].new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(9000)
        rows = page.evaluate(JS)
        page.close()
    taken, skipped = {}, {}
    for r in rows:
        key = r["src"].split("/upload/")[-1].split(".")[0]
        if r["inModel"] and not r["adish"] and not r["tiny"] and (r["nw"] or 0) >= 200:
            taken.setdefault(key, r)
        else:
            why = ("advert/avatar/comment rail" if r["adish"] else
                   "avatar-sized variant" if r["tiny"] else
                   "outside the model's gallery" if not r["inModel"] else
                   "too small")
            skipped.setdefault(key, (r, why))
    print("==", url.split("/")[-1])
    print("   TAKEN  (%d):" % len(taken))
    for k, r in taken.items():
        print("      %s  %dx%d  %s" % (k[:10], r["nw"], r["nh"], r["cls"]))
    print("   SKIPPED (%d):" % len(skipped))
    for k, (r, why) in skipped.items():
        print("      %s  %dx%d  [%s]  %s" % (k[:10], r["nw"], r["nh"], why,
                                             r["section"][:52]))
