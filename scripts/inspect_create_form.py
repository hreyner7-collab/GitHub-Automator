"""Inspector pass over Creality's create-model form (READ-ONLY, never submits).

Opens the real form in a NEW tab of the logged-in debug Chrome and reports:
  · every input / button / radio on the form,
  · the three Model Source radios and what each click strategy does to them,
  · whether the Model Source search box is really revealed, and
  · the picker's rows (name / red No-Derivatives flag / coordinates) plus the
    markup of the red row, for a search known to list one.

Nothing is filled in, nothing is submitted; the tab is closed at the end.
Saves debug/inspector_create_model.json.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import CDP_URL, SOURCE_ROWS_JS  # noqa: E402

FORM = "https://www.crealitycloud.com/create-model-new?source=12"
OUT = os.path.join(ROOT, "debug", "inspector_create_model.json")

DUMP_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const box = (e) => { const r = e.getBoundingClientRect();
        return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; };
    const inputs = [...document.querySelectorAll('input,textarea,select')].map(e => ({
        tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
        ph: (e.getAttribute('placeholder') || '').slice(0, 46),
        accept: e.getAttribute('accept') || '', multiple: e.hasAttribute('multiple'),
        vis: !!(e.offsetWidth || e.offsetHeight), box: box(e),
    }));
    const radios = [...document.querySelectorAll('.el-radio')].map(e => ({
        t: clean(e.innerText).slice(0, 24),
        checked: !!e.querySelector('input:checked'),
        vis: !!(e.offsetWidth || e.offsetHeight), box: box(e),
    }));
    const src = [...document.querySelectorAll("input[placeholder*='Paste the URL' i]")].map(e => ({
        vis: !!(e.offsetWidth || e.offsetHeight), box: box(e),
        wrap: (e.closest('div') || {}).className ? String(e.closest('div').className).slice(0, 60) : '',
    }));
    const buttons = [...document.querySelectorAll('button,[role=button]')]
        .map(e => clean(e.innerText).slice(0, 30)).filter(Boolean).slice(0, 30);
    return JSON.stringify({url: location.href, inputs: inputs, radios: radios,
                           source_inputs: src, buttons: buttons});
}
"""


def main() -> int:
    report = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.goto(FORM, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(9000)
            report["opened"] = page.url
            report["before"] = json.loads(page.evaluate(DUMP_JS))

            # ---- click strategies for "Remix Models", one at a time, with the
            #      radio state read back after each
            attempts = []
            radio = page.locator(".el-radio").filter(has_text="Remix Models")
            try:
                r = radio.first.bounding_box()
                if r:
                    page.mouse.click(r["x"] + 12, r["y"] + r["height"] / 2)
                    page.wait_for_timeout(1200)
                attempts.append(("mouse-on-radio", json.loads(page.evaluate(DUMP_JS))))
            except Exception as e:
                attempts.append(("mouse-on-radio", {"error": str(e)[:120]}))
            if not any(x.get("checked") for x in attempts[-1][1].get("radios", [])):
                for name, fn in (
                    ("text-click", lambda: page.get_by_text("Remix Models", exact=False)
                     .first.click(timeout=6000)),
                    ("radio-label-click", lambda: radio.first.click(timeout=6000)),
                    ("js-input-click", lambda: page.evaluate(
                        """() => { const el = [...document.querySelectorAll('.el-radio')]
                                .find(e => /Remix Models/.test(e.innerText || ''));
                            if (!el) return false;
                            const i = el.querySelector('input'); if (i) i.click();
                            el.click(); return true; }""")),
                ):
                    try:
                        fn()
                        page.wait_for_timeout(1500)
                    except Exception as e:
                        attempts.append((name, {"error": str(e)[:120]}))
                        continue
                    attempts.append((name, json.loads(page.evaluate(DUMP_JS))))
                    if any(x.get("checked") for x in attempts[-1][1].get("radios", [])):
                        break
            report["click_attempts"] = [
                {"how": how,
                 "remix_checked": any(r.get("checked") and "Remix" in r.get("t", "")
                                      for r in (state.get("radios") or [])),
                 "source_visible": any(s.get("vis") for s in (state.get("source_inputs") or [])),
                 "error": state.get("error", "")}
                for how, state in attempts]

            revealed = any(a["source_visible"] for a in report["click_attempts"])
            report["revealed"] = revealed

            if revealed:
                inp = page.locator("input[placeholder*='Paste the URL' i]").first
                inp.fill("scale model pallet jack")
                page.wait_for_timeout(4000)
                rows = json.loads(page.evaluate(SOURCE_ROWS_JS))
                report["rows"] = rows
                report["row_parents"] = page.evaluate(r"""
                    () => {
                        const el = document.querySelector('.origin-item');
                        const out = []; let e = el;
                        for (let i = 0; i < 4 && e; i++) {
                            out.push({tag: e.tagName.toLowerCase(),
                                      cls: String(e.className || '').slice(0, 70)});
                            e = e.parentElement;
                        }
                        return out;
                    }
                """)
                report["flagged_markup"] = page.evaluate(r"""
                    () => {
                        const items = [...document.querySelectorAll('.origin-item')];
                        const red = items.find(el => /does not allow for derivative|license policy/i
                                                     .test(el.innerText || ''));
                        return red ? red.outerHTML.replace(/\s+/g, ' ').slice(0, 800) : '';
                    }
                """)
                report["after"] = json.loads(page.evaluate(DUMP_JS))
        finally:
            try:
                page.close()
            except Exception:
                pass

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False)

    print("=== create-model inspector ===")
    print("opened:", report.get("opened"))
    b = report.get("before") or {}
    print("inputs on the form:")
    for i in (b.get("inputs") or []):
        print("   %-8s type=%-8s accept=%-26s vis=%-5s ph=%s"
              % (i["tag"], i["type"], i["accept"][:26], i["vis"], i["ph"]))
    print("radios:", [(r["t"], r["checked"], r["vis"]) for r in (b.get("radios") or [])
                      if r["t"]])
    print("source inputs (before):", b.get("source_inputs"))
    print("click attempts:")
    for a in report.get("click_attempts", []):
        print("   %-18s remix_checked=%-5s source_visible=%-5s %s"
              % (a["how"], a["remix_checked"], a["source_visible"], a["error"]))
    rows = (report.get("rows") or {}).get("rows") or []
    print("picker rows:", len(rows))
    for r in rows[:8]:
        print("   flagged=%-5s red=%-5s warn=%-5s name=%-44s x=%s y=%s"
              % (r.get("flagged"), r.get("red"), r.get("warn"), (r.get("name") or "")[:44],
                 r.get("x"), r.get("y")))
    print("row parents:", report.get("row_parents"))
    print("flagged markup:", (report.get("flagged_markup") or "")[:360])
    print("wrote", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
