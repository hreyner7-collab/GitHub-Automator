"""Read-only: what a candidate's own page claims (licence, author, photo set)."""
from __future__ import annotations
import json, re, sys
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from playwright.sync_api import sync_playwright   # noqa: E402
from downloader import LICENSE_JS, classify_license  # noqa: E402
from uploader import ensure_browser, CDP_URL      # noqa: E402
import ledger, hashlib                            # noqa: E402
sys.path.insert(0, str(BASE / "scripts"))
from find_multiphoto_model import COUNT_JS        # noqa: E402

ensure_browser()
out = {}
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp(CDP_URL)
    for url in sys.argv[1:]:
        p = b.contexts[0].new_page()
        p.set_viewport_size({"width": 1440, "height": 900})
        try:
            p.goto(url, wait_until="domcontentloaded", timeout=60000)
            p.wait_for_timeout(7000)
            p.evaluate("window.scrollTo(0,0)")
            p.wait_for_timeout(800)
            raw = p.evaluate(LICENSE_JS)
            lic = json.loads(raw) if isinstance(raw, str) else (raw or {})
            text = " ".join(x for x in [(lic.get("cc_url") or ""), (lic.get("card") or "")] if x).strip()
            probe = classify_license(text, lic.get("icons"))
            cnt = p.evaluate(COUNT_JS)
            body = p.evaluate("() => (document.body.innerText||'')")
            meta = p.evaluate("""() => ({
                title: document.title,
                og: (document.querySelector('meta[property="og:description"]')||{}).content || '',
                desc: (document.querySelector('meta[name="description"]')||{}).content || '',
                h1: (document.querySelector('h1')||{}).innerText || ''})""")
        finally:
            p.close()
        am = re.search(r"(?:^|\n)([^\n]{2,32})\n\s*Follow(?:\n|$)", body)
        hit = ledger.find(source_url=url)
        out[url] = {"meta": meta, "author_guess": am.group(1) if am else "",
                    "licence_raw": text[:400], "licence": probe,
                    "icons": lic.get("icons"), "photos": cnt,
                    "in_ledger": hit.get("found"), "ledger_hit": hit.get("entry", {}).get("name") if hit.get("found") else ""}
        print("=" * 78)
        print("URL       ", url)
        print("title     ", meta["title"][:100])
        print("h1        ", meta["h1"][:80], "| author:", out[url]["author_guess"])
        print("licence   ", probe.get("code"), "ok=", probe.get("ok_for_reupload"),
              "nd=", probe.get("nd"), "restricted=", probe.get("restricted"))
        print("evidence  ", probe.get("evidence"))
        print("raw       ", text[:220])
        print("photos    ", cnt)
        print("in_ledger ", hit.get("found"), out[url]["ledger_hit"])
        print("desc      ", (meta["og"] or "")[:200])
(Path(BASE / "debug" / "candidate_inspection.json")).write_text(
    json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
