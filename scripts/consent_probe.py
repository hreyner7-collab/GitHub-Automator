"""Phase 2 of the agreement-box proof — run over the debug browser (CDP 9222)
after scripts/consent_fill.py has left the REAL create-model form filled on
step 2. Never taps Submit; never publishes anything.

It answers, from the live page itself:
  1. which frame the step-2 form is in, and which frames contain the agreement
     phrase at all (does the row the finder looks for exist on this build?);
  2. what uploader.py's own step-9 finder (CONSENT_JS / CONSENT_STATE_JS /
     CONSENT_CLICK_JS) reports against that live DOM right now;
  3. what the real row markup is — the clickable circle, its class, any hidden
     input — plus every element whose text matches /agree/i;
  4. whether a REAL mouse click on the real control leaves the tick read back
     as checked (click -> read back -> click -> read back, ending where it
     started);
  5. what Creality itself says: the Submit-adjacent warning while unticked
     versus after the tick (tooltip text, visibility, Submit's own state).

Writes debug/consent/consent_probe.json + debug/consent/probe_*.png.

Usage:  .venv/Scripts/python.exe scripts/consent_probe.py
"""
import io
import json
import os
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright            # noqa: E402
from uploader import CDP_URL, CONSENT_JS, CONSENT_STATE_JS  # noqa: E402

OUT = os.path.join(ROOT, "debug", "consent", "consent_probe.json")
SHOTS = os.path.join(ROOT, "debug", "consent")

PHRASE_JS = r"""
() => {
    const t = document.body ? (document.body.innerText || '') : '';
    return JSON.stringify({phrase: /I have read and agree/i.test(t),
                           agree: /agree/i.test(t),
                           footer: /Previous Step/.test(t),
                           len: t.length});
}
"""

# Every element whose text contains the agreement phrase, plus the inner-most
# ones (the finder sorts candidates by text length, so this shows what it sees).
DOM_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const has = (e) => /i have read and agree/i.test(e.textContent || '');
    const all = [...document.querySelectorAll('*')];
    const match = all.filter(e => has(e));
    const minimal = match.filter(e => ![...e.children].some(c => has(c)));
    const inf = (e, full) => {
        const r = e.getBoundingClientRect();
        const out = {tag: e.tagName.toLowerCase(),
                     cls: String(e.className || '').slice(0, 150), id: e.id || '',
                     rect: [Math.round(r.x), Math.round(r.y),
                            Math.round(r.width), Math.round(r.height)],
                     text: clean(e.textContent).slice(0, 140)};
        if (full) out.html = e.outerHTML.replace(/\s+/g, ' ').slice(0, 1800);
        return out;
    };
    return JSON.stringify({
        matches: match.length,
        minimal: minimal.slice(0, 4).map(e => inf(e, true)),
        parents: minimal.length ? (() => {
            const out = []; let e = minimal[0].parentElement;
            for (let i = 0; i < 6 && e; i++) { out.push(inf(e, false)); e = e.parentElement; }
            return out; })() : [],
        siblings: (minimal.length && minimal[0].parentElement)
            ? [...minimal[0].parentElement.children].slice(0, 6).map(e => inf(e, true)) : [],
        agree_like: all.filter(e => /agree/i.test(e.textContent || '') &&
                                    (e.textContent || '').length < 130)
            .map(e => inf(e, false)).slice(0, 25),
    });
}
"""

# The row + the real clickable control inside it (circle), measured AFTER the
# row was scrolled into view by SCROLL_JS.
CONTROL_JS = r"""
() => {
    const has = (e) => /i have read and agree/i.test(e.textContent || '');
    const all = [...document.querySelectorAll('*')];
    const txt = all.filter(e => has(e) && ![...e.children].some(c => has(c)))[0];
    if (!txt) return JSON.stringify({found: false});
    const row = txt.closest('label') || txt.parentElement || txt;
    const circle = row.querySelector('[class*=check],[class*=circle],[class*=icon]') ||
                   (row.firstElementChild && row.firstElementChild !== txt
                        ? row.firstElementChild : null);
    const input = row.querySelector('input') ||
                  (row.parentElement ? row.parentElement.querySelector('input') : null);
    const inf = (e) => {
        if (!e) return null;
        const r = e.getBoundingClientRect();
        return {tag: e.tagName.toLowerCase(),
                cls: String(e.className || '').slice(0, 150),
                x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                w: Math.round(r.width), h: Math.round(r.height),
                html: e.outerHTML.replace(/\s+/g, ' ').slice(0, 900)};
    };
    return JSON.stringify({found: true, text_el: inf(txt), row: inf(row),
                           circle: inf(circle), input: inf(input)});
}
"""

SCROLL_JS = r"""
() => {
    const has = (e) => /i have read and agree/i.test(e.textContent || '');
    const all = [...document.querySelectorAll('*')];
    const txt = all.filter(e => has(e) && ![...e.children].some(c => has(c)))[0];
    if (!txt) return 'absent';
    (txt.closest('label') || txt.parentElement || txt).scrollIntoView({block: 'center'});
    return 'scrolled';
}
"""

# State read-back that does NOT go through uploader.py: the hidden input's own
# checked flag, the circle's class + computed colours + svg check.
REALITY_JS = r"""
() => {
    const has = (e) => /i have read and agree/i.test(e.textContent || '');
    const all = [...document.querySelectorAll('*')];
    const txt = all.filter(e => has(e) && ![...e.children].some(c => has(c)))[0];
    if (!txt) return JSON.stringify({found: false});
    const row = txt.closest('label') || txt.parentElement || txt;
    const circle = row.querySelector('[class*=check],[class*=circle],[class*=icon]') ||
                   (row.firstElementChild && row.firstElementChild !== txt
                        ? row.firstElementChild : null);
    const input = row.querySelector('input') ||
                  (row.parentElement ? row.parentElement.querySelector('input') : null);
    const cs = circle ? getComputedStyle(circle) : null;
    return JSON.stringify({
        found: true,
        input_checked: input ? !!input.checked : null,
        input_html: input ? input.outerHTML.replace(/\s+/g, ' ').slice(0, 300) : null,
        circle_cls: circle ? String(circle.className || '') : '',
        circle_bg: cs ? cs.backgroundColor : '',
        circle_color: cs ? cs.color : '',
        circle_html: (circle || row).outerHTML.replace(/\s+/g, ' ').slice(0, 600),
        has_svg: !!(circle && circle.querySelector('svg,path')),
        row_cls: String(row.className || ''),
        aria_checked: circle ? (circle.getAttribute('aria-checked') || '') : '',
    });
}
"""

# What Creality itself says around Submit (tooltip / warning text), and the
# Submit control's own state — read while the mouse hovers Submit.
TOOLTIP_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const vis = (e) => {
        const r = e.getBoundingClientRect();
        const cs = getComputedStyle(e);
        return !!(r.width || r.height) && cs.visibility !== 'hidden' &&
               cs.display !== 'none' && +cs.opacity > 0.05;
    };
    const mentions = [...document.querySelectorAll('*')]
        .filter(e => /check the box to agree|agree before submitting|please agree/i
                     .test(e.textContent || '') && (e.textContent || '').length < 200)
        .map(e => ({tag: e.tagName.toLowerCase(),
                    cls: String(e.className || '').slice(0, 110), vis: vis(e),
                    text: clean(e.textContent).slice(0, 160)}))
        .slice(0, 12);
    const submit = [...document.querySelectorAll('button,div,span,[role=button]')]
        .filter(e => clean(e.innerText) === 'Submit')
        .map(e => { const r = e.getBoundingClientRect();
            return {tag: e.tagName.toLowerCase(),
                    cls: String(e.className || '').slice(0, 130), vis: vis(e),
                    x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                    disabled: e.getAttribute('aria-disabled') ||
                              e.getAttribute('disabled') || ''}; })
        .slice(0, 6);
    return JSON.stringify({mentions: mentions, submit: submit});
}
"""


def jload(raw, fallback=None):
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw if raw else fallback)
    except Exception:
        return fallback


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    report = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "create-model" in (p.url or "")), None)
        if page is None:
            print("NO create-model tab is open — run scripts/consent_fill.py first "
                  "(or it closed the page).")
            return 2
        page.bring_to_front()
        page.wait_for_timeout(1200)

        # The form renders on the page itself now, but support the legacy iframe.
        work = None
        for _ in range(40):
            try:
                if page.locator("input[type=file]").count() > 0:
                    work = page
                    break
                fr = next((f for f in page.frames
                           if "flowprint/create-model" in f.url), None)
                if fr and fr.locator("input[type=file]").count() > 0:
                    work = fr
                    break
            except Exception:
                pass
            page.wait_for_timeout(250)
        is_frame = work is not page and work is not None
        report["url"] = page.url
        report["used_frame"] = is_frame
        report["viewport"] = page.evaluate("() => [innerWidth, innerHeight]")

        # 1) frame survey — where does the phrase exist at all?
        survey = []
        for f in page.frames:
            try:
                d = jload(f.evaluate(PHRASE_JS), {})
            except Exception as e:
                d = {"error": str(e)[:120]}
            survey.append({"url": (f.url or "")[:110], "main": f is page.main_frame,
                           **(d or {})})
        report["frame_survey"] = survey

        page.keyboard.press("Escape")            # close any open dropdown
        page.wait_for_timeout(500)

        # 2) uploader.py's own finder, verbatim
        report["finder"] = jload(work.evaluate(CONSENT_JS), {}) if work else None
        try:
            report["finder_state"] = work.evaluate(CONSENT_STATE_JS)
        except Exception as e:
            report["finder_state"] = "err " + str(e)[:120]

        # 3) the real DOM around the phrase
        try:
            work.evaluate(SCROLL_JS)
            page.wait_for_timeout(400)
        except Exception as e:
            report["scroll"] = str(e)[:120]
        report["dom"] = jload(work.evaluate(DOM_JS), {})
        control = jload(work.evaluate(CONTROL_JS), {}) or {}
        report["control"] = control

        def read_reality():
            return jload(work.evaluate(REALITY_JS), {})

        def iframe_offset():
            if not is_frame:
                return 0.0, 0.0
            box = work.frame_element().bounding_box()
            return box["x"], box["y"]

        def real_click(x, y):
            ox, oy = iframe_offset()
            page.mouse.click(ox + x, oy + y)
            page.wait_for_timeout(450)

        def say_after_hover():
            """Hover the Submit control (no click) and read what Creality says."""
            tip = jload(work.evaluate(TOOLTIP_JS), {}) or {}
            subs = [s for s in (tip.get("submit") or []) if s.get("vis")]
            if subs:
                s = subs[0]
                ox, oy = iframe_offset()
                page.mouse.move(ox + s["x"], oy + s["y"])
                page.wait_for_timeout(1300)
                tip = jload(work.evaluate(TOOLTIP_JS), {}) or {}
            return tip

        # 4) the click test — ending in the state we started from
        s0 = read_reality()
        report["state_initial"] = s0
        report["say_initial"] = say_after_hover()
        ticks = {}

        circle = control.get("circle")
        checked0 = bool(s0.get("input_checked"))
        if circle:
            real_click(circle["x"], circle["y"])
            s1 = read_reality()
            report["state_click1"] = s1
            ticks["click1_from_checkbox"] = {"before": checked0,
                                             "after": s1.get("input_checked"),
                                             "flipped": (s1.get("input_checked")
                                                         is not None
                                                         and bool(s1.get("input_checked"))
                                                         != checked0)}
            if checked0 and s1.get("input_checked") is False:
                # was ticked -> our click unticked it; Creality's complaint shows
                report["say_unticked"] = say_after_hover()
                if not ticks["click1_from_checkbox"].get("disabled"):
                    real_click(circle["x"], circle["y"])
                    s2 = read_reality()
                    report["state_click2"] = s2
                    ticks["click2"] = {"after": s2.get("input_checked")}
            report["say_final"] = say_after_hover()
        else:
            report["circle_missing"] = True
        report["ticks"] = ticks

        # 5) screenshots of the footer (agreement row + Submit)
        try:
            w, h = report["viewport"]
            r = (control.get("row") or {}).get("rect") or (0, 0)
            clip = {"x": 0, "y": max(0, h - 220), "width": w, "height": 220}
            page.screenshot(path=os.path.join(SHOTS, "probe_footer.png"), clip=clip)
            page.screenshot(path=os.path.join(SHOTS, "probe_viewport.png"))
        except Exception as e:
            report["screenshot_error"] = str(e)[:160]

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    # ---- report
    print("=== phase 2: live DOM proof ===")
    print("url:", report["url"], "| form frame:",
          "iframe" if report["used_frame"] else "page itself")
    print("frames:")
    for f in report["frame_survey"]:
        mark = "MAIN" if f["main"] else "    "
        print("  %s phrase=%-5s agree=%-5s footer=%-5s %s"
              % (mark, f.get("phrase"), f.get("agree"), f.get("footer"), f["url"][:80]))
    fnd = report.get("finder") or {}
    print("finder (uploader's own):", json.dumps(fnd, ensure_ascii=False)[:700])
    print("finder_state:", report.get("finder_state"))
    dom = report.get("dom") or {}
    print("phrase elements total:", dom.get("matches"))
    for m in (dom.get("minimal") or []):
        print("  minimal:", m["tag"], "|", m["cls"], "|", m["text"][:60])
    print("control:", json.dumps(report.get("control"), ensure_ascii=False)[:900])
    print("state initial:", json.dumps(report.get("state_initial"), ensure_ascii=False)[:400])
    print("ticks:", json.dumps(report.get("ticks"), ensure_ascii=False))
    for k in ("state_click1", "state_click2"):
        if report.get(k):
            print(k, "-> input_checked:", (report[k] or {}).get("input_checked"),
                  "| circle_bg:", (report[k] or {}).get("circle_bg"))
    for k in ("say_initial", "say_unticked", "say_final"):
        if k in report:
            v = report[k] or {}
            print("%s: mentions=%s submit=%s"
                  % (k, json.dumps(v.get("mentions") or [], ensure_ascii=False)[:260],
                     json.dumps(v.get("submit") or [], ensure_ascii=False)[:220]))
    print("wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
