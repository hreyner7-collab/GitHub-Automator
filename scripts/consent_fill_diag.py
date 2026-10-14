"""Instrumented re-run of the consent fill (never submits).

Same real flow as scripts/consent_fill.py (uploader.upload() with
CREALITY_NO_SUBMIT=1), but every step-9 call — CONSENT_JS, CONSENT_STATE_JS,
CONSENT_CLICK_JS — is wrapped so it first snapshots the live DOM into
window.__consentLog: how many elements match the finder's phrase filter, their
shortest candidates, whether label.el-checkbox / the hidden input exist, where
the footer is, the scroll position and readyState.

That snapshot is what tells us WHY step 9's finder saw nothing during a fill
while the very same finder sees the row a minute later.

Writes debug/consent/consent_diag.json (+ keeps upload_result_diag.json).

Usage:  .venv/Scripts/python.exe scripts/consent_fill_diag.py
"""
import asyncio
import io
import json
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ["CREALITY_NO_SUBMIT"] = "1"
sys.path.insert(0, ROOT)

import uploader                                             # noqa: E402
from playwright.async_api import async_playwright            # noqa: E402

OUT = os.path.join(ROOT, "debug", "consent", "consent_diag.json")
OUT_RES = os.path.join(ROOT, "debug", "consent", "upload_result_diag.json")

NAME = "Домкрат для подъема поддонов 1/10-го масштаба"
SRC = "https://www.crealitycloud.com/model-detail/scale-model-pallet-jack"
MODEL = os.path.join(ROOT, "downloads",
                     "Домкрат+для+подъема+поддонов+1_10-го+масштаба.3mf")
PHOTO = os.path.join(ROOT, "downloads",
                     "домкрат_для_подъема_поддонов_1_10-го_мас_img_0.webp")
LICENSE_RAW = ("creativecommons.org/licenses/by-nc-sa/4 License This work is licensed "
               "under the Copyright License 4.0. This license allows reusers to "
               "distribute, remix, adapt, and build upon the material in any medium "
               "or format for noncommercial purposes only, and only so long as "
               "attribution is given to the creator.")
DESC = ("Scale model pallet jack, 1/10 scale — re-uploaded as a remix under the "
        "source licence (CC BY-NC-SA).\n\nOriginal design: " + SRC)

SNAP = r"""
    const __lenOf = (e) => ((e.innerText || e.textContent || '').trim()).length;
    const __snap = () => {
        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
        const all = [...document.querySelectorAll('div,span,p,label,section')];
        const m = all.filter(e => /I have read and agree/i
                                  .test(e.innerText || e.textContent || ''));
        const shortest = m.map(e => ({tag: e.tagName.toLowerCase(),
                                      cls: String(e.className || '').slice(0, 60),
                                      len: __lenOf(e)}))
                          .sort((a, b) => a.len - b.len).slice(0, 6);
        const lab = document.querySelector('label.el-checkbox');
        const inp = document.querySelector('input[type=checkbox]');
        // what is REALLY at the consent circle's centre right now — if the
        // source-picker dropdown is still open, its dismiss layer swallows the
        // next outside click (that is what a first click reading false means)
        const clab = [...document.querySelectorAll('label.el-checkbox')]
            .find(e => /i have read and agree/i.test(e.textContent || ''));
        const circ = clab ? clab.querySelector('.el-checkbox__input,.el-checkbox__inner') : null;
        const efp = circ ? (() => {
            const r = circ.getBoundingClientRect();
            const el = document.elementFromPoint(Math.round(r.x + r.width / 2),
                                                 Math.round(r.y + r.height / 2));
            return el ? {tag: el.tagName.toLowerCase(),
                         cls: String(el.className || '').slice(0, 90)} : null;
        })() : null;
        // the picker leftovers, EVERY instance, with real geometry right now
        const allGeom = (sel) => [...document.querySelectorAll(sel)].map(e => {
            const r = e.getBoundingClientRect();
            const cs = getComputedStyle(e);
            return {display: cs.display, z: cs.zIndex, pos: cs.position,
                    rect: [Math.round(r.x), Math.round(r.y),
                           Math.round(r.width), Math.round(r.height)],
                    txt: clean(e.innerText || '').slice(0, 40)};
        });
        const chain = (() => {
            if (!circ) return null;
            const r = circ.getBoundingClientRect();
            let e = document.elementFromPoint(Math.round(r.x + r.width / 2),
                                              Math.round(r.y + r.height / 2));
            const out = [];
            for (let i = 0; i < 5 && e; i++) {
                const er = e.getBoundingClientRect();
                out.push({tag: e.tagName.toLowerCase(),
                          cls: String(e.className || '').slice(0, 70),
                          rect: [Math.round(er.x), Math.round(er.y),
                                 Math.round(er.width), Math.round(er.height)],
                          inline_style: (e.getAttribute('style') || '').slice(0, 80)});
                e = e.parentElement;
            }
            return out;
        })();
        return {t: Math.round(performance.now()),
                element_at_circle: efp,
                chain_at_circle: chain,
                picker: {marks: allGeom('.origin-choose-mark'),
                         lists: allGeom('.origin-choose-list'),
                         items: allGeom('.origin-item').slice(0, 3)},
                url_tail: location.href.slice(-40),
                matches: m.length,
                under90: m.filter(e => __lenOf(e) < 90).length,
                shortest: shortest,
                label_el_checkbox: !!lab,
                label_cls: lab ? String(lab.className).slice(0, 60) : '',
                any_checkbox_input: !!inp,
                input_checked: inp ? !!inp.checked : null,
                footer_bottom_btn_group: !!document.querySelector('footer.bottom-btn-group'),
                origin_items: document.querySelectorAll('.origin-item').length,
                ready: document.readyState,
                scroll_y: Math.round(window.scrollY),
                doc_h: document.documentElement.scrollHeight,
                win_h: Math.round(window.innerHeight)};
    };
    const __log = (rec) => { (window.__consentLog = window.__consentLog || []).push(rec); };
"""


def wrap(call_name: str, js: str) -> str:
    """Wrap an uploader CONSENT_* expression so it logs the DOM first."""
    return ("() => { " + SNAP + """
        const rec = __snap(); rec.call = '%s'; __log(rec);
        try {
            const out = (%s)();
            if (out && typeof out.then === 'function') {
                return out.then((o) => { __log({call: '%s.result', result: o}); return o; });
            }
            __log({call: '%s.result', result: out});
            return out;
        } catch (e) {
            __log({call: '%s.error', error: String(e).slice(0, 160)});
            throw e;
        }
    }""" % (call_name, js, call_name, call_name, call_name))


async def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    uploader.CONSENT_JS = wrap("CONSENT_JS", uploader.CONSENT_JS)
    uploader.CONSENT_STATE_JS = wrap("CONSENT_STATE_JS", uploader.CONSENT_STATE_JS)
    uploader.CONSENT_CLICK_JS = wrap("CONSENT_CLICK_JS", uploader.CONSENT_CLICK_JS)

    # Close any previously opened create-model tab (our own artifacts) so the
    # run starts from exactly one fresh tab.
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(uploader.CDP_URL)
        for p in list(b.contexts[0].pages):
            if "create-model" in (p.url or ""):
                try:
                    await p.close()
                except Exception:
                    pass

    try:
        res = await uploader.upload(NAME, DESC, MODEL, [PHOTO], SRC,
                                    dry_run=False, license_raw=LICENSE_RAW)
    except Exception as e:
        res = {"success": False, "exception": type(e).__name__ + ": " + str(e)[:600]}
    with open(OUT_RES, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)

    # read the log back from the still-open page
    log = []
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(uploader.CDP_URL)
        page = next((p for p in b.contexts[0].pages
                     if "create-model" in (p.url or "")), None)
        if page:
            try:
                log = json.loads(await page.evaluate(
                    "() => JSON.stringify(window.__consentLog || [])"))
            except Exception as e:
                log = [{"read_error": str(e)[:160]}]
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"upload": {k: res.get(k) for k in
                              ("success", "no_submit", "error", "exception", "message")},
                   "verify": res.get("verify"), "consent_log": log}, fh,
                  ensure_ascii=False, indent=1)

    print("=== consent diag run ===")
    print("success:", res.get("success"), "| no_submit:", res.get("no_submit"),
          "| error:", (res.get("error") or res.get("exception") or "")[:200])
    v = res.get("verify") or {}
    print("consent result:", json.dumps(v.get("consent") or {}, ensure_ascii=False))
    print("copyright_agreed:", v.get("copyright_agreed"))
    print("consent log (%d entries):" % len(log))
    for rec in log:
        if rec.get("call") and rec.get("call").endswith(".result"):
            print("  %-22s -> %s" % (rec["call"], json.dumps(rec.get("result"))[:120]))
        elif rec.get("call"):
            print("  %-22s t=%-7s matches=%-3s under90=%-3s label=%-5s input=%-5s "
                  "footer=%-5s origin_items=%-2s scrollY=%-5s ready=%s"
                  % (rec["call"], rec.get("t"), rec.get("matches"),
                     rec.get("under90"), rec.get("label_el_checkbox"),
                     rec.get("any_checkbox_input"), rec.get("footer_bottom_btn_group"),
                     rec.get("origin_items"), rec.get("scroll_y"), rec.get("ready")))
            print("      element at circle:", json.dumps(rec.get("element_at_circle"),
                                                          ensure_ascii=False)[:160])
            if rec.get("chain_at_circle"):
                print("      chain at circle:", json.dumps(rec["chain_at_circle"],
                                      ensure_ascii=False)[:520])
            if rec.get("picker"):
                print("      picker:", json.dumps(rec["picker"], ensure_ascii=False)[:900])
            print("      shortest:", json.dumps(rec.get("shortest"), ensure_ascii=False)[:220])
    print("wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
