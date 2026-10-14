"""Reproduce the auto-run's exact Remix-reveal path against the LIVE form and
report what the uploader sees at each step — no submit. Saves
debug/repro_remix.json + debug/repro_remix.png."""
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import CDP_URL  # noqa: E402

FORM = "https://www.crealitycloud.com/en/create-model-new?source=12"
OUTJ = os.path.join(ROOT, "debug", "repro_remix.json")
OUTP = os.path.join(ROOT, "debug", "repro_remix.png")

STATE_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const vis = (e) => !!(e.offsetWidth || e.offsetHeight);
    const src = document.querySelector("input[placeholder*='Paste the URL' i]");
    const radios = [...document.querySelectorAll('label.el-radio')].map(e => ({
        t: clean(e.innerText).slice(0, 30), vis: vis(e),
        checked: !!e.querySelector('input:checked')}));
    const step2 = document.querySelector('section.second-step') ||
                  document.querySelector('.second-step');
    const files = [...document.querySelectorAll('input[type=file]')].map((e, i) => ({
        i, vis: vis(e), accept: (e.getAttribute('accept') || '').slice(0, 30),
        files: (e.files || []).length}));
    return JSON.stringify({
        step2_exists: !!step2, step2_visible: !!step2 && step2.offsetParent !== null,
        src_exists: !!src, src_visible: !!src && vis(src),
        src_ph: src ? (src.getAttribute('placeholder') || '') : '',
        radios: radios, files: files.slice(0, 10),
        bodyHead: clean(document.body.innerText).slice(0, 200)
    });
}
"""


def main() -> int:
    files = sorted(
        [p for p in (os.path.join(ROOT, "downloads", f)
                     for f in os.listdir(os.path.join(ROOT, "downloads"))
                     if f.lower().endswith(".stl"))],
        key=os.path.getsize, reverse=True)
    if not files:
        print("SKIP: no .stl in downloads/")
        return 1
    model = files[0]
    print("model:", os.path.basename(model), os.path.getsize(model), "bytes")
    rep = {}
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = b.contexts[0]
        page = ctx.new_page()
        page.goto(FORM, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)
        page.bring_to_front()
        for _ in range(30):
            if page.locator("input[type=file]").count() > 0:
                break
            page.wait_for_timeout(1000)

        # step 1: attach a file, click Next — the same as the auto run
        try:
            inp = page.locator("input[type=file]").first
            inp.set_input_files(model)
            rep["attached"] = True
        except Exception as e:
            rep["attached"] = str(e)[:200]
        page.wait_for_timeout(4000)
        rep["after_attach"] = json.loads(page.evaluate(STATE_JS))
        try:
            page.locator(".step-one-btn.submit").first.click(timeout=5000)
            rep["next"] = "clicked"
        except Exception as e:
            rep["next"] = str(e)[:150]
        page.wait_for_timeout(9000)
        rep["after_next"] = json.loads(page.evaluate(STATE_JS))

        # the uploader's exact reveal loop, reproduced
        src = page.locator("input[placeholder*='Paste the URL' i]").first
        rep["src_count"] = page.locator("input[placeholder*='Paste the URL' i]").count()
        for rnd in range(4):
            try:
                if rnd % 2 == 0:
                    page.get_by_text(re.compile(r"Remix Models", re.I),
                                     exact=False).first.click(timeout=5000)
                else:
                    radio = page.locator("label.el-radio").filter(
                        has_text=re.compile(r"Remix Models", re.I))
                    radio.first.scroll_into_view_if_needed(timeout=5000)
                    radio.first.click(timeout=5000)
            except Exception as e:
                rep[f"click{rnd}"] = str(e)[:150]
            page.wait_for_timeout(1500)
            try:
                rep[f"visible_r{rnd}"] = src.is_visible()
            except Exception as e:
                rep[f"visible_r{rnd}"] = str(e)[:120]
            if rep.get(f"visible_r{rnd}") is True:
                break
        # what does the DOM actually say about the input?
        rep["dom_state"] = page.evaluate(r"""
() => {
    const src = document.querySelector("input[placeholder*='Paste the URL' i]");
    if (!src) return {gone: true};
    let chain = [], e = src;
    for (let i = 0; i < 8 && e; i++) {
        const cs = getComputedStyle(e);
        const r = e.getBoundingClientRect();
        chain.push({tag: e.tagName, cls: (e.className || '').toString().slice(0, 40),
                    disp: cs.display, visb: cs.visibility, op: cs.opacity,
                    w: r.width, h: r.height});
        e = e.parentElement;
    }
    return {gone: false, chain};
}
""")
        page.screenshot(path=OUTP)
        try:
            page.close()
        except Exception:
            pass
    json.dump(rep, open(OUTJ, "w", encoding="utf-8"), indent=1)
    for k, v in rep.items():
        print(k, "=>", str(v)[:260])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
