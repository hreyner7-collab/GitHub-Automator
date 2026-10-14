"""Watch the user's manual session in the debug Chrome's create-model tab(s)
over CDP and record every state change — clicks, typed text, checked radios,
picker rows, screenshots — so the uploader can be taught the exact sequence.

Read-only observation: nothing here clicks or types for the user. The transcript
goes to debug/watch_session.jsonl; a screenshot debug/watch_*.png is saved at
each meaningful change so the visual state is on disk too.

Run in the background while the user drives the form by hand. SIGINT/abort ends
it and the transcript stays on disk.
"""
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import CDP_URL  # noqa: E402

OUTJ = os.path.join(ROOT, "debug", "watch_session.jsonl")
SHOT_TMPL = os.path.join(ROOT, "debug", "watch_%03d.png")

STATE_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const vis = (e) => !!(e.offsetWidth || e.offsetHeight);
    const active = document.activeElement;
    const radios = [...document.querySelectorAll('label.el-radio, .el-radio')]
        .filter(e => vis(e))
        .slice(0, 16)
        .map(e => ({t: clean(e.innerText).slice(0, 40),
                    checked: e.classList.contains('is-checked')}));
    const inputs = [...document.querySelectorAll('input[type=text], input:not([type]), textarea')]
        .filter(vis)
        .slice(0, 10)
        .map(e => ({ph: (e.getAttribute('placeholder') || '').slice(0, 40),
                    value: (e.value || '').slice(0, 60),
                    focused: e === active}));
    const srcRows = [...document.querySelectorAll('.origin-item')]
        .slice(0, 12)
        .map(e => ({name: clean((e.querySelector('.name') || e).innerText).slice(0, 50),
                    disabled: /disabled-item/.test((e.className || '').toString())}));
    const marks = [...document.querySelectorAll('.origin-choose-mark')]
        .slice(0, 3)
        .map(e => ({text: clean(e.innerText).slice(0, 80),
                    href: clean((e.querySelector('a[href*="/model-detail/"]') || {}).getAttribute
                                ? ((e.querySelector('a[href*="/model-detail/"]') || {}).href || '')
                                : '')}));
    const files = [...document.querySelectorAll('input[type=file]')]
        .slice(0, 10)
        .map((e, i) => ({i, n: (e.files || []).length,
                         accept: (e.getAttribute('accept') || '').slice(0, 24)}));
    const toasts = [...document.querySelectorAll('.el-message, [class*=toast], [class*=notice]')]
        .filter(vis).slice(0, 3).map(e => clean(e.innerText).slice(0, 90));
    return JSON.stringify({
        url: location.pathname,
        radios, inputs, srcRows, marks, files, toasts,
        bodySample: clean(document.body.innerText).slice(0, 300)
    });
}
"""


def log(ev: dict):
    ev["t"] = time.strftime("%H:%M:%S")
    with open(OUTJ, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def main() -> int:
    print("=== watching the create-model tab(s). Do it by hand now. ===", flush=True)
    seen = None
    shot_n = 0
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP_URL)
        pages = [p for p in b.contexts[0].pages if "crealitycloud" in p.url]
        form_pages = [p for p in pages if "create-model" in p.url]
        if not form_pages:
            form_pages = pages
        print("watching %d tab(s)" % len(form_pages))
        while True:
            for p in form_pages:
                try:
                    st = json.loads(p.evaluate(STATE_JS))
                except Exception as e:
                    log({"page": p.url[:60], "err": str(e)[:80]})
                    continue
                sig = json.dumps(st, ensure_ascii=False, sort_keys=True)
                if sig != seen:
                    seen = sig
                    shot_n += 1
                    try:
                        p.screenshot(path=SHOT_TMPL % shot_n)
                    except Exception:
                        pass
                    log({"page": p.url[:80], "state": st, "shot": SHOT_TMPL % shot_n})
                    print("[%s] change #%d | radios=%s | inputs=%s | rows=%s | marks=%s | toasts=%s"
                          % (st.get("url"), shot_n,
                             [r["t"] + ("*" if r["checked"] else "") for r in (st.get("radios") or [])][:8],
                             [(i.get("value") or i.get("ph") or "")[:22] for i in (st.get("inputs") or [])][:4],
                             [r["name"][:20] for r in (st.get("srcRows") or [])][:4],
                             [m["text"][:40] for m in (st.get("marks") or [])],
                             st.get("toasts") or ""), flush=True)
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
