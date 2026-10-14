"""Read the REAL Creality form straight over CDP — read-only, no Playwright pages.

Playwright's `connect_over_cdp` only exposed the tabs it knew about at attach time,
which is why the first watchdog saw no form at all (the pipeline created its form tab
afterwards). This talks to the target's own websocket instead, so it sees the tab the
pipeline opened and never touches it: one Runtime.evaluate, no clicks, no typing.

Usage: .venv/Scripts/python.exe scripts/read_live_form.py [outfile]
"""
import io
import json
import os
import sys
import urllib.request

from websockets.sync.client import connect          # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDP = "http://127.0.0.1:9222"
OUT = os.path.join(ROOT, sys.argv[1] if len(sys.argv) > 1
                   else os.path.join("debug", "live_form_after_failure.json"))

FORM_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const cls = (e) => e ? (e.className || '').toString().split(' ')[0] : '';
    const at = (e) => { const r = e.getBoundingClientRect();
                        return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)}; };
    const top = (p) => { if (!p) return null; const e = document.elementFromPoint(p.x, p.y);
                         return e ? (e.tagName.toLowerCase() + '.' + cls(e)) : null; };
    const vis = (e) => !!(e.offsetWidth || e.offsetHeight);

    const label = [...document.querySelectorAll('label.el-checkbox')]
        .find(l => /agree to/i.test(l.textContent || ''));
    const box = label ? label.querySelector('input.el-checkbox__original[type=checkbox]') : null;
    const circle = label ? label.querySelector('span.el-checkbox__input') : null;
    const cpt = circle ? at(circle) : null;

    const submit = [...document.querySelectorAll('button,[role=button],div,span')]
        .filter(e => clean(e.textContent) === 'Submit' && vis(e)).pop();
    const spt = submit ? at(submit) : null;

    const nameEl = document.querySelector("input[placeholder*='model name' i]");
    const descEl = document.querySelector('textarea');
    const inputs = [...document.querySelectorAll('input[type=file]')].map((i, n) => ({
        slot: n, accept: (i.getAttribute('accept') || '').slice(0, 44),
        multiple: i.hasAttribute('multiple'),
        files: [...(i.files || [])].map(f => f.name.slice(0, 70)),
    }));
    const previews = [...document.querySelectorAll('img')]
        .filter(i => /^(blob|data):/.test(i.src || ''))
        .map(i => ({src: (i.src || '').slice(0, 20),
                    w: i.getBoundingClientRect().width | 0,
                    h: i.getBoundingClientRect().height | 0}));
    const bg = [...document.querySelectorAll('div,span')]
        .map(e => getComputedStyle(e).backgroundImage)
        .filter(v => /url\("?(blob|data):/.test(v || '')).length;

    // the form's own photo counters: the "Model Images (x/9)" gallery read-out
    // plus the cover cards' own labels, so a fill can be followed as it climbs
    const galMatch = (document.body.innerText || '')
        .match(/Model Images\s*\((\d+)\/9\)/);
    const gallery_counter = galMatch ? parseInt(galMatch[1], 10) : -1;

    const mark = document.querySelector('.origin-choose-mark');
    const list = document.querySelector('.origin-choose-list');
    const answers = [...document.querySelectorAll('input[type=radio]:checked')]
        .map(r => clean(((r.closest('label') || r.parentElement || {}).innerText || '')).slice(0, 40));
    const licenceText = [...document.querySelectorAll('span,div')]
        .filter(e => vis(e) && /CC BY|creativecommons/i.test(clean(e.textContent)))
        .map(e => clean(e.textContent).slice(0, 60)).slice(0, 4);
    // everything Creality is currently SAYING: toasts, tips, field errors
    const say = [...document.querySelectorAll('div.tips,.el-message,.el-notification,' +
                '.el-form-item__error,[class*=error],[class*=toast]')]
        .filter(vis).map(e => clean(e.innerText)).filter(Boolean)
        .filter((v, i, a) => a.indexOf(v) === i).slice(0, 8);
    const anyMsg = [...document.querySelectorAll('span,div,p')]
        .filter(e => vis(e) && /please|required|agree before submitting|can not|cannot|failed|error/i
                     .test(clean(e.textContent)) && clean(e.textContent).length < 120)
        .map(e => clean(e.textContent)).filter((v, i, a) => a.indexOf(v) === i).slice(0, 8);

    return {
        url: location.href, title: document.title,
        step_hint: clean(document.body.innerText).slice(0, 120),
        name: nameEl ? nameEl.value.slice(0, 60) : null,
        has_description: !!descEl && descEl.value.length > 0,
        source: mark ? {text: clean(mark.textContent).slice(0, 70),
                        href: (mark.querySelector('a') || {}).href || ''} : null,
        picker_open: list ? vis(list) : false,
        licence_answers: answers, licence_text: licenceText,
        file_inputs: inputs,
        gallery_counter: gallery_counter,
        filled_slots: inputs.filter(i => i.files.length).map(i => [i.slot, i.files]),
        preview_count: previews.length, previews: previews.slice(0, 8), bg_previews: bg,
        consent: {found: !!label, checked: box ? !!box.checked : null, row_cls: cls(label),
                  circle_pt: cpt, topmost_at_circle: top(cpt)},
        submit: {pt: spt, topmost: top(spt),
                 disabled: submit ? (submit.disabled === true ||
                    /disabled/.test((submit.className || '').toString())) : null},
        creality_says: say, creality_hints: anyMsg,
        text_head: clean(document.body.innerText).slice(0, 400),
    };
}
"""


def _snapshot(target) -> dict:
    """One read-only Runtime.evaluate of FORM_JS on that page target."""
    with connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024,
                 open_timeout=10) as ws:
        # raw CDP does not auto-call a function the way Playwright's evaluate does —
        # the expression must invoke it
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": "(" + FORM_JS + ")()",
                                       "returnByValue": True}}))
        while True:
            msg = json.loads(ws.recv(timeout=20))
            if msg.get("id") == 1:
                break
    if "exceptionDetails" in (msg.get("result") or {}):
        raise RuntimeError("evaluate failed: " + json.dumps(msg)[:300])
    return (msg.get("result") or {}).get("result", {}).get("value") or {}


def watch(minutes: float, dest: str) -> int:
    """Poll the form and keep a change-timeline — the fill's own record (photo
    slots as the form received them, the consent circle and what a real click
    there would hit, the Submit control). Read-only."""
    import time
    deadline = time.time() + minutes * 60
    timeline, last, samples = [], None, 0
    print("watching the live form for up to %s min (read-only)" % minutes)
    while time.time() < deadline:
        try:
            pages = [t for t in json.loads(urllib.request.urlopen(
                CDP + "/json/list", timeout=5).read()) if t.get("type") == "page"]
            target = next((t for t in pages
                           if "create-model" in (t.get("url") or "")), None)
            if target is None:
                time.sleep(1.0)
                continue
            snap = _snapshot(target)
        except Exception as e:
            print("read failed: %s" % str(e)[:120])
            time.sleep(1.5)
            continue
        samples += 1
        key = json.dumps(snap, sort_keys=True, ensure_ascii=False)
        if key != last:
            rec = {"at": time.strftime("%H:%M:%S"), "n": samples}
            rec.update(snap)
            timeline.append(rec)
            last = key
            print("%s n=%d consent=%s topmost@circle=%s slots=%s previews=%s"
                  % (rec["at"], samples, snap["consent"]["checked"],
                     snap["consent"]["topmost_at_circle"],
                     snap["filled_slots"], snap["preview_count"]), flush=True)
        time.sleep(1.5)
    with open(os.path.join(ROOT, dest), "w", encoding="utf-8") as fh:
        json.dump({"samples": samples, "timeline": timeline}, fh,
                  ensure_ascii=False, indent=1)
    print("wrote %s (%d samples, %d changes)" % (dest, samples, len(timeline)))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--watch":
        minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
        return watch(minutes, sys.argv[3] if len(sys.argv) > 3
                     else os.path.join("debug", "live_form_watch.json"))
    targets = json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=5).read())
    pages = [t for t in targets if t.get("type") == "page"]
    print("pages open:")
    for t in pages:
        print("   ", (t.get("url") or "")[:120])
    target = next((t for t in pages if "create-model" in (t.get("url") or "")), None)
    if target is None:
        print("no create-model form tab open — nothing to read")
        return 1
    snap = _snapshot(target)
    os.makedirs(os.path.dirname(os.path.join(ROOT, OUT)), exist_ok=True)
    with open(os.path.join(ROOT, OUT), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=1)
    print(json.dumps(snap, ensure_ascii=False, indent=1)[:5000])
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
