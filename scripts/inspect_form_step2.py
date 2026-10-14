"""Inspector pass over STEP 2 of Creality's create-model form (never submits).

Step 1 is "Upload Files" (the radios on step 2 do not exist in the layout until a
file is attached and Next is pressed). This attaches a REAL model file already in
downloads/, advances to "Fill in the information", then maps every control the
uploader drives — including the Model Source picker, with the red
No-Derivatives row's markup — and checks each of the uploader's own selectors
against what is really on the page.

Saves debug/inspector_step2.json + debug/inspector_step2.png. The tab is closed
at the end and nothing is submitted.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import CDP_URL, SOURCE_ROWS_JS  # noqa: E402

FORM = "https://www.crealitycloud.com/en/create-model-new?source=12"
OUT = os.path.join(ROOT, "debug", "inspector_step2.json")

STATE_JS = r"""
() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const vis = (e) => !!(e.offsetWidth || e.offsetHeight);
    const radios = [...document.querySelectorAll('.el-radio')]
        .map(e => ({t: clean(e.innerText).slice(0, 30),
                    checked: !!e.querySelector('input:checked'), vis: vis(e)}))
        .filter(r => r.t);
    const inputs = [...document.querySelectorAll('input,textarea')]
        .filter(vis)
        .map(e => ({tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
                    ph: (e.getAttribute('placeholder') || '').slice(0, 44),
                    accept: (e.getAttribute('accept') || '').slice(0, 40),
                    multiple: e.hasAttribute('multiple')}));
    const buttons = [...document.querySelectorAll('button,[role=button],div.step-one-btn,[class*=submit]')]
        .filter(vis).map(e => clean(e.innerText).slice(0, 30)).filter(Boolean);
    const heads = [...new Set([...document.querySelectorAll('.el-form-item__label,h3,h4,span,div')]
        .filter(vis).map(e => clean(e.innerText))
        .filter(t => t && t.length < 48 &&
                /license|licence|adaptation|commercial|sharing|source|cover|category|tags|visibility|description|agree|print setting|submit|publish/i.test(t)))];
    return JSON.stringify({url: location.href, radios: radios, inputs: inputs,
                           buttons: [...new Set(buttons)].slice(0, 25), heads: heads.slice(0, 40)});
}
"""


def main() -> int:
    files = sorted(glob.glob(os.path.join(ROOT, "downloads", "*.3mf")),
                   key=os.path.getsize, reverse=True)
    if not files:
        print("SKIP: no .3mf in downloads/ to attach")
        return 0
    model = files[0]
    report = {"model_file": os.path.basename(model),
              "model_bytes": os.path.getsize(model)}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.goto(FORM, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(8000)
            page.bring_to_front()
            work = page
            for f in page.frames:
                if "flowprint/create-model" in f.url:
                    work = f
                    break
            report["used_frame"] = (work is not page)
            report["step1_url"] = page.url

            try:
                await_ = None
                page.get_by_text("STL/CAD files or other types of 3MF file",
                                 exact=False).first.click(timeout=5000)
                report["filetype_radio"] = True
            except Exception as e:
                report["filetype_radio"] = str(e)[:120]
            try:
                work.locator("input[type=file][accept*='.step'], "
                             "input[type=file][accept*='.stl']").first.set_input_files(model)
                report["attached"] = True
            except Exception as e:
                report["attached"] = str(e)[:200]
            page.wait_for_timeout(4000)
            stem = os.path.splitext(os.path.basename(model))[0][:18]
            report["name_registered"] = bool(page.evaluate(
                "(s) => document.body.innerText.includes(s)", stem))

            clicked = []
            for sel in (".step-one-btn.submit", ".step-one-btn"):
                try:
                    loc = work.locator(sel).first
                    if await_loc_count(work, sel):
                        loc.click(timeout=5000)
                        clicked.append(sel)
                        break
                except Exception as e:
                    clicked.append(sel + " -> " + str(e)[:80])
            report["next_clicked"] = clicked
            page.wait_for_timeout(9000)

            report["after_next"] = json.loads(page.evaluate(STATE_JS))
            report["step2_url"] = page.url

            # ---- the uploader's own selectors, checked against the live form
            checks = {}
            for name, sel in (("name_input", "input[placeholder*='model name' i]"),
                              ("desc_textarea", "textarea"),
                              ("source_input", "input[placeholder*='Paste the URL' i]"),
                              ("cover_inputs", "input[type=file][accept*='.jpeg']")):
                try:
                    checks[name] = work.locator(sel).count()
                except Exception as e:
                    checks[name] = "err " + str(e)[:60]
            report["selector_counts"] = checks

            # ---- reveal + read the Model Source picker
            try:
                rad = work.locator(".el-radio").filter(has_text="Remix Models")
                box = rad.first.bounding_box()
                if box:
                    page.mouse.click(box["x"] + 12, box["y"] + box["height"] / 2)
                page.wait_for_timeout(2000)
                st = json.loads(page.evaluate(STATE_JS))
                report["remix_selected"] = any(
                    r["checked"] and "Remix" in r["t"] for r in st["radios"])
                if not report["remix_selected"]:
                    try:
                        rad.first.click(timeout=5000)
                        page.wait_for_timeout(1500)
                        st = json.loads(page.evaluate(STATE_JS))
                        report["remix_selected"] = any(
                            r["checked"] and "Remix" in r["t"] for r in st["radios"])
                    except Exception as e:
                        report["remix_click_error"] = str(e)[:120]
                src = work.locator("input[placeholder*='Paste the URL' i]").first
                report["source_visible"] = src.is_visible()
                if report["source_visible"]:
                    src.fill("scale model pallet jack")
                    page.wait_for_timeout(4000)
                    report["rows"] = json.loads(page.evaluate(SOURCE_ROWS_JS))
                    report["row_parents"] = page.evaluate(r"""
                        () => { const el = document.querySelector('.origin-item');
                            const out = []; let e = el;
                            for (let i = 0; i < 4 && e; i++) {
                                out.push({tag: e.tagName.toLowerCase(),
                                          cls: String(e.className || '').slice(0, 60)});
                                e = e.parentElement; }
                            return out; }
                    """)
                    report["flagged_markup"] = page.evaluate(r"""
                        () => { const items = [...document.querySelectorAll('.origin-item')];
                            const red = items.find(el => /does not allow for derivative|license policy/i
                                                         .test(el.innerText || ''));
                            return red ? red.outerHTML.replace(/\s+/g, ' ').slice(0, 700) : ''; }
                    """)
                    report["after_remix"] = json.loads(page.evaluate(STATE_JS))
            except Exception as e:
                report["remix_error"] = str(e)[:200]

            page.screenshot(path=os.path.join(ROOT, "debug", "inspector_step2.png"))
            # a dry run never submits, so the form is simply discarded
        finally:
            try:
                page.close()
            except Exception:
                pass

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False)

    print("=== step-2 inspector ===")
    print("model:", report["model_file"], report["model_bytes"], "bytes",
          "| attached:", report.get("attached"), "| registered:", report.get("name_registered"))
    print("file type radio:", report.get("filetype_radio"))
    print("next:", report.get("next_clicked"), "-> url", report.get("step2_url"))
    a = report.get("after_next") or {}
    print("visible radios:", [(r["t"], r["checked"]) for r in (a.get("radios") or [])])
    print("visible inputs:", len(a.get("inputs") or []))
    for i in (a.get("inputs") or []):
        print("   %-8s %-8s accept=%-22s %s" % (i["tag"], i["type"], i["accept"][:22], i["ph"]))
    print("buttons:", a.get("buttons"))
    print("licence/source labels:", [h for h in (a.get("heads") or [])
                                     if any(k in h.lower() for k in ("licen", "adapt", "commercial", "sharing"))][:10])
    print("selector counts:", report.get("selector_counts"))
    print("remix selected:", report.get("remix_selected"), "| source visible:",
          report.get("source_visible"))
    rows = (report.get("rows") or {}).get("rows") or []
    print("picker rows:", len(rows))
    for r in rows[:8]:
        print("   flagged=%-5s red=%-5s warn=%-5s name=%-42s x=%s y=%s"
              % (r.get("flagged"), r.get("red"), r.get("warn"), (r.get("name") or "")[:42],
                 r.get("x"), r.get("y")))
    print("row parents:", report.get("row_parents"))
    print("flagged markup:", (report.get("flagged_markup") or "")[:300])
    print("wrote", os.path.relpath(OUT, ROOT))
    return 0


def await_loc_count(work, sel):
    try:
        return work.locator(sel).count() > 0
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
