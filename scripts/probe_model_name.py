"""Inspector: where does the model's REAL name live on a Creality Cloud page?

`downloader.fetch` reads the name from `document.title`, which on a localised
page is an SEO string ("<name> Fichier STL et 3MF pour l'impression 3D | Autre")
— so the listing would be published under that name (found live 2026-10-09).
This probe opens the page in the user's logged-in debug Chrome and dumps every
candidate: the title, og:title, the h1s, and the classed "name"/"title"
elements, so the collector can prefer the page's own name element.

Writes debug/model_name_probe.json and prints a short table.

Usage:  .venv/Scripts/python.exe scripts/probe_model_name.py <model-url>
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
    const txt = (el) => (el ? (el.innerText || el.textContent || '').trim() : '');
    const meta = (sel) => { const m = document.querySelector(sel);
                            return m ? (m.content || '') : ''; };
    const cls = (el) => (el && el.className ? String(el.className) : '');
    const h1s = [...document.querySelectorAll('h1')].map(el => ({
        text: txt(el).slice(0, 160), cls: cls(el).slice(0, 90)
    }));
    const cands = [];
    const seen = new Set();
    for (const sel of ['[class*="model-name"]', '[class*="model-title"]',
                       '[class*="detail-title"]', '[class*="model-detail"] h2',
                       '[class*="title"]']) {
        for (const el of document.querySelectorAll(sel)) {
            const t = txt(el);
            if (!t || t.length > 160 || seen.has(sel + '|' + t)) continue;
            // only elements a human could be looking at
            const r = el.getBoundingClientRect();
            if (r.width < 40 || r.height < 8) continue;
            seen.add(sel + '|' + t);
            cands.push({sel, text: t.slice(0, 160), cls: cls(el).slice(0, 90),
                        y: Math.round(r.top), w: Math.round(r.width)});
            if (cands.length > 40) break;
        }
    }
    return JSON.stringify({
        url: location.href,
        title: document.title,
        og_title: meta('meta[property="og:title"]'),
        h1s, candidates: cands
    });
}
"""


def main(argv):
    url = argv[1] if len(argv) > 1 else \
        "https://www.crealitycloud.com/model-detail/dumpling-squishy-clicker"
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(9000)
            raw = page.evaluate(PROBE_JS)
        finally:
            page.close()
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    DEBUG.mkdir(exist_ok=True)
    (DEBUG / "model_name_probe.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("title   :", data.get("title", ""))
    print("og:title:", data.get("og_title", ""))
    print("h1s     :")
    for h in data.get("h1s", []):
        print("   ", repr(h["text"]), "|", h["cls"])
    print("candidates:")
    for c in data.get("candidates", [])[:25]:
        print("   %-26s y=%-5s w=%-5s %r | %s"
              % (c["sel"], c["y"], c["w"], c["text"][:90], c["cls"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
