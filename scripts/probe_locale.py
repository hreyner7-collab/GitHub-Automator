"""Inspector: what decides the site's language, and where a /fr/ sneaks in.

Opens the user's model URL both bare and with /fr/ in the debug Chrome, then
prints the FINAL url each time (redirect vs not), the page's <html lang>,
and every cookie / localStorage key whose name or value mentions a language
('fr', 'lang', 'locale', 'i18n').

Usage:  .venv/Scripts/python.exe scripts/probe_locale.py <model-url>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright          # noqa: E402
from uploader import CDP_URL, ensure_browser              # noqa: E402

STORAGE_JS = r"""
() => {
    const langish = (s) => /(^|[^a-z])(fr|lang|locale|i18n|language|lng)($|[^a-z])/i.test(s);
    const ls = {};
    for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        const v = (localStorage.getItem(k) || '').slice(0, 80);
        if (langish(k) || langish(v)) ls[k] = v;
    }
    return JSON.stringify({
        href: location.href,
        lang: document.documentElement.getAttribute('lang') || '',
        acceptLanguage: navigator.language,
        localStorage: ls,
    });
}
"""


def probe(pw, url: str) -> dict:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    cookies_pre = {c["name"]: (c.get("value") or "")[:40] for c in ctx.cookies()
                   if any(t in c["name"].lower()
                          for t in ("lang", "locale", "i18n", "lng", "territory", "region"))}
    page = ctx.new_page()
    out = {"requested": url}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        data = json.loads(page.evaluate(STORAGE_JS) or "{}")
        out.update(data)
        out["cookie_matches_pre_navigate"] = cookies_pre
        # after the visit, read ALL cookies again — a lang cookie may be SET by
        # the visit itself
        cookies_post = {c["name"]: (c.get("value") or "")[:40] for c in ctx.cookies()
                        if any(t in c["name"].lower()
                               for t in ("lang", "locale", "i18n", "lng", "territory", "region"))}
        out["cookie_matches_post_navigate"] = cookies_post
        out["diff"] = {k: v for k, v in cookies_post.items()
                       if cookies_pre.get(k) != v}
    except Exception as e:
        out["error"] = str(e)[:200]
    finally:
        try:
            page.close()
        except Exception:
            pass
    return out


def main():
    model_url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://www.crealitycloud.com/model-detail/ribbed-organizer-dysk-moka-design"
    if not ensure_browser():
        print("no debug Chrome on port 9222 — start it and log in first")
        return 1
    with sync_playwright() as pw:
        bare = probe(pw, model_url)
        fr = probe(pw, model_url.replace("crealitycloud.com",
                                         "crealitycloud.com/fr"))
    print(json.dumps({"bare": bare, "fr": fr}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
