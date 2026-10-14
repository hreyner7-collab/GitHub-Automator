"""One-off: does Creality's create-model form have an English variant we can
force? Opens both /en/ and plain URL over CDP and reports the language the
form really came up in (radio labels + placeholders), never submits."""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from playwright.sync_api import sync_playwright

sys.path.insert(0, ".")
from uploader import CDP_URL  # noqa: E402

LANG_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const radios = [...document.querySelectorAll('.el-radio')]
        .map(e => clean(e.innerText).slice(0, 40)).filter(Boolean).slice(0, 6);
    const phs = [...new Set([...document.querySelectorAll('input,textarea')]
        .map(e => (e.getAttribute('placeholder') || '').slice(0, 50)).filter(Boolean))].slice(0, 6);
    const nextBtn = clean((document.querySelector('.step-one-btn.submit, .step-one-btn') || {}).innerText || '');
    return JSON.stringify({url: location.href, radios, phs, nextBtn, path: location.pathname});
}
"""


def main() -> int:
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = b.contexts[0]
        for url in ("https://www.crealitycloud.com/en/create-model-new?source=12",
                    "https://www.crealitycloud.com/create-model-new?source=12"):
            p = ctx.new_page()
            try:
                p.goto(url, wait_until="domcontentloaded", timeout=90000)
                p.wait_for_timeout(6000)
                st = json.loads(p.evaluate(LANG_JS))
                fr = any("collez" in (x or "").lower() or "modifiés" in (x or "")
                         or "modifiés" in (x or "") for x in st["radios"] + st["phs"] + [st["nextBtn"]])
                st["FRENCH"] = fr
                st["ENGLISH"] = any("stl" in (x or "").lower() or "next" in st["nextBtn"].lower()
                                    or "model name" in (x or "").lower() for x in st["radios"] + st["phs"])
                print("URL:", url)
                print(json.dumps(st, indent=1, ensure_ascii=False)[:600])
            except Exception as e:
                print("URL:", url, "-> ERR", str(e)[:120])
            finally:
                try:
                    p.close()
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
