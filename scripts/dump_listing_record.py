"""Dump the published listing's own stored record (the Nuxt `model-info-*` entry).

Usage: .venv/Scripts/python.exe scripts/dump_listing_record.py <published-url>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from playwright.sync_api import sync_playwright   # noqa: E402
from uploader import ensure_browser, CDP_URL      # noqa: E402

JS = r"""
() => {
    const n = window.__NUXT__;
    const data = (typeof n === 'function') ? n() : n;
    if (!data || !data.data) return {};
    const out = {};
    for (const k of Object.keys(data.data)) {
        if (/^model-info/.test(k)) out[k] = data.data[k];
    }
    return out;
}
"""

ensure_browser()
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp(CDP_URL)
    p = b.contexts[0].new_page()
    try:
        p.goto(sys.argv[1], wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(9000)
        rec = p.evaluate(JS)
    finally:
        p.close()

dest = BASE / "debug" / "published_listing_record.json"
dest.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
print("saved", dest, len(json.dumps(rec)), "chars")

CDN = "https://pic2-cdn.creality.com"


def show(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            show(v, path + "." + k if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            show(v, "%s[%d]" % (path, i))
    elif isinstance(node, str) and ("crealityCloud" in node or "http" in node):
        leaf = path.split(".")[-1].split("[")[0]
        if leaf.lower() in ("cover", "covers", "pccovers", "appcovers", "editcovers",
                            "pic", "pics", "image", "images", "gallery", "url",
                            "pictureurl", "coverurl", "appcover", "mode", "path"):
            print("  %-58s %s" % (path[-58:], node.replace(CDN, "")[:88]))


for key in rec:
    print("=" * 78)
    print("record:", key)
    show(rec[key])
