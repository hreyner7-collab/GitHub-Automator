"""Find Creality Cloud models that are (a) licence-clean for a re-upload and
(b) carry SEVERAL photos — the case the single-photo pallet jack never exercised.

Read-only: it opens pages in the logged-in debug Chrome, reads each model's own
licence card (the pipeline's own LICENSE_JS) and counts the photos in the model's
own gallery (the strip + the slides, same rules as downloader._fetch_creality),
then reports which candidates are not already in the ledger.

Usage:  .venv/Scripts/python.exe scripts/find_multiphoto_model.py [min_photos] [max_checks]

Writes debug/multiphoto_candidates.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from playwright.sync_api import sync_playwright      # noqa: E402

import ledger                                        # noqa: E402
from downloader import LICENSE_JS, classify_license  # noqa: E402
from uploader import ensure_browser, CDP_URL         # noqa: E402

MIN_PHOTOS = 4
MAX_CHECKS = 24
WANT = 3                      # stop once this many good candidates are found

LISTING = "https://www.crealitycloud.com/model-category/3d-print-all"

# Same counting rules the fetcher applies: creator photos only, nothing from
# the plate previews / related rails / avatars.
COUNT_JS = r"""
() => {
    const src = (im) => im.currentSrc || im.src || im.getAttribute('data-src') || '';
    const isCreator = (s) => {
        const t = (s || '').split('?')[0];
        return t.indexOf('/crealityCloud/upload/') >= 0 ||
               t.indexOf('/comp/model/') >= 0;
    };
    // /crealityCloud/common/ holds real gallery photos too (measured
    // 2026-10-09), so it is NOT junk — only these folders never hold one.
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
    const strip = document.querySelector('.swiper-thumb') ||
        [...document.querySelectorAll('[class*=thumb]')]
            .find(e => e.querySelectorAll('img').length >= 2) || null;
    const seen = [];
    const add = (s) => {
        const k = (s || '').split('?')[0];
        if (k && seen.indexOf(k) < 0) seen.push(k);
    };
    let stripThumbs = 0;
    if (strip) {
        const imgs = [...strip.querySelectorAll('img')];
        stripThumbs = imgs.length;
        for (const im of imgs)
            if (isGalleryPhoto(src(im)) && !isNonPhoto(src(im)) && !bad(im))
                add(src(im));
    }
    const root = document.querySelector('div.model-gallery') ||
                 document.querySelector('.model-content');
    if (root) {
        for (const im of root.querySelectorAll('img')) {
            const s = src(im);
            if (!isGalleryPhoto(s) || isNonPhoto(s)) continue;
            if ((im.naturalWidth || 0) < 200 || bad(im)) continue;
            add(s);
        }
    }
    return {photos: seen.length, strip_thumbs: stripThumbs,
            title: (document.title || '').slice(0, 120)};
}
"""


def harvest(page, limit: int = 40) -> list[str]:
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2500)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
    page.wait_for_timeout(1500)
    return page.evaluate(
        """(limit) => [...new Set([...document.querySelectorAll(
               'a[href*="/model-detail/"]')].map(a => a.href.split('?')[0]))]
               .slice(0, limit)""", limit)


def main() -> None:
    global MIN_PHOTOS, MAX_CHECKS
    MIN_PHOTOS = int(sys.argv[1]) if len(sys.argv) > 1 else MIN_PHOTOS
    MAX_CHECKS = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_CHECKS
    ensure_browser()
    candidates, checked, good, skipped = [], 0, [], []

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]

        # 1) harvest model links from the listing
        page = ctx.new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        try:
            page.goto(LISTING, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(9000)
            links = harvest(page)
        finally:
            page.close()
        seen = set()
        candidates = [u for u in links if not (u in seen or seen.add(u))]
        print("listing gave %d candidate link(s)" % len(candidates))

        # 2) inspect one by one
        for url in candidates:
            if checked >= MAX_CHECKS or len(good) >= WANT:
                break
            hit = ledger.find(source_url=url)
            if hit.get("found"):
                print("  skipped (already in the ledger):", url.rsplit("/", 1)[-1])
                skipped.append({"url": url, "slug": url.rsplit("/", 1)[-1],
                                "entry": (hit.get("entry") or {}).get("name", "")})
                continue
            checked += 1
            p = ctx.new_page()
            p.set_viewport_size({"width": 1440, "height": 900})
            try:
                p.goto(url, wait_until="domcontentloaded", timeout=60000)
                p.wait_for_timeout(6500)
                p.evaluate("window.scrollTo(0,0)")
                p.wait_for_timeout(700)
                raw = p.evaluate(LICENSE_JS)
                lic = json.loads(raw) if isinstance(raw, str) else (raw or {})
                text = " ".join(x for x in [(lic.get("cc_url") or ""),
                                            (lic.get("card") or "")] if x).strip()
                probe = classify_license(text, lic.get("icons"))
                cnt = p.evaluate(COUNT_JS)
            except Exception as e:
                print("  FAILED", url, repr(e)[:120])
                p.close()
                continue
            p.close()
            slug = url.rsplit("/", 1)[-1]
            row = {"url": url, "slug": slug, "photos": cnt["photos"],
                   "strip_thumbs": cnt["strip_thumbs"], "code": probe.get("code") or "",
                   "nd": probe.get("nd"), "restricted": probe.get("restricted"),
                   "ok": probe.get("ok_for_reupload"), "title": cnt.get("title", ""),
                   "evidence": probe.get("evidence")}
            print("  %-34s photos=%-3d strip=%-3d licence=%-10s ok=%s"
                  % (slug[:34], row["photos"], row["strip_thumbs"],
                     row["code"] or ("RESTRICTED" if row["restricted"] else "?"),
                     row["ok"]))
            if row["ok"] and row["photos"] >= MIN_PHOTOS:
                good.append(row)

    out = {"min_photos": MIN_PHOTOS, "checked": checked,
           "candidates": candidates, "good": good,
           "skipped_already_published": skipped,
           "min_photos_used": MIN_PHOTOS,
           "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    dest = BASE / "debug" / "multiphoto_candidates.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\nchecked %d page(s); %d licence-clean with >= %d photos"
          % (checked, len(good), MIN_PHOTOS))
    for r in good:
        print("  %s  photos=%d strip=%d licence=%s  %s"
              % (r["slug"], r["photos"], r["strip_thumbs"], r["code"],
                 r["title"][:60]))
    print("wrote", dest)


if __name__ == "__main__":
    main()
