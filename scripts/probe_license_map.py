"""Confirm the licence-question -> CC badge mapping on the real form.

Opens the create-model page in the logged-in debug Chrome, finds the three
licence questions, clicks a set of answer combinations and reads the CC badge
the form renders underneath each time. Proves (empirically, not by memory)
what a CC BY-NC-SA source needs.

Saves debug/license_map.json + debug/license_map.png. Touches nothing else on
the form (radios only, no file, no submit).
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from playwright.sync_api import sync_playwright  # noqa: E402
from uploader import ensure_browser, CDP_URL, UPLOAD_VERIFY  # noqa: E402

ensure_browser()
QUESTIONS = [
    "Allow adaptations of your work to be shared?",
    "Allow commercial uses of your work?",
    "Allow sharing or redistributing of your work or its derivatives?",
]
# the combos worth proving (Q1, Q2, Q3) -> what the badge should read
COMBOS = [
    ([0, 0, 0], "CC0 / Public Domain"),
    ([2, 1, 1], "CC BY-NC-SA  <- the source of the current test model"),
    ([0, 0, 1], "CC BY"),
    ([2, 0, 1], "CC BY-SA"),
    ([0, 1, 1], "CC BY-NC"),
]
out = {"combos": [], "errors": []}

SET_JS = """
([qText, wantIdx]) => {
    const heads = [...document.querySelectorAll('div,span,h3,label,p')]
        .filter(e => (e.innerText||'').trim() === qText);
    for (const h of heads) {
        let box = h.parentElement;
        for (let i = 0; i < 6 && box; i++) {
            const radios = [...box.querySelectorAll('input[type=radio]')];
            if (radios.length >= 2) {
                const target = radios[wantIdx];
                if (!target) return false;
                const lab = target.closest('label') || target.parentElement;
                if (lab) lab.click(); else target.click();
                return true;
            }
            box = box.parentElement;
        }
    }
    return false;
}
"""

READ_JS = """
([qText]) => {
    const heads = [...document.querySelectorAll('div,span,h3,label,p')]
        .filter(e => (e.innerText||'').trim() === qText);
    for (const h of heads) {
        let box = h.parentElement;
        for (let i = 0; i < 6 && box; i++) {
            const radios = [...box.querySelectorAll('input[type=radio]')];
            if (radios.length >= 2)
                return radios.findIndex(r => r.checked);
            box = box.parentElement;
        }
    }
    return -1;
}
"""

BADGE_JS = """
() => {
    // the badge text is its own paragraph next to "This work is licensed..."
    const ccish = [...document.querySelectorAll('div,span,p,section,a,td')]
        .filter(e => /CC\\s?0|CC\\s?BY|Public\\s+Domain/i.test(e.innerText||''))
        .sort((a,b) => ((a.innerText||'').length - (b.innerText||'').length));
    let text = ccish.length ? ccish[0].innerText : '';
    if (!text) {
        const tip = [...document.querySelectorAll('div,span,p,section')]
            .filter(e => /This work is licensed under/i.test(e.innerText||''))
            .sort((a,b) => ((a.innerText||'').length - (b.innerText||'').length))[0];
        text = tip ? tip.innerText : '';
    }
    const root = (ccish[0] && ccish[0].parentElement) ||
                 (document.querySelector('.el-radio') && null) || document.body;
    let imgs = [];
    try {
        imgs = [...root.querySelectorAll('img')]
            .map(i => ((i.alt||'') + ' ' + (i.src||'').split('/').pop()).trim())
            .slice(0, 6);
    } catch (e) {}
    return ((text || '') + ' || ' + imgs.join(' | ')).slice(0, 500);
}
"""

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
    page = ctx.new_page()
    try:
        page.goto("https://www.crealitycloud.com/create-model-new?source=12",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(9000)
        # step 1 gate: the licence card only renders once the file type is chosen
        try:
            page.get_by_text("STL/CAD files or other types of 3MF file",
                             exact=False).first.click(timeout=6000)
            page.wait_for_timeout(1500)
        except Exception as e:
            out["errors"].append("stl choice: %s" % str(e)[:120])
        # lazy sections: nudge the page until the licence questions exist
        for _ in range(12):
            if page.evaluate(
                    "() => /Allow adaptations of your work/i.test(document.body.innerText)"):
                break
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(700)
        out["questions_found"] = page.evaluate(
            "() => /Allow adaptations of your work/i.test(document.body.innerText)")
        try:
            page.get_by_text(QUESTIONS[0], exact=True).first.scroll_into_view_if_needed(
                timeout=5000)
            page.wait_for_timeout(800)
        except Exception as e:
            out["errors"].append("scroll: %s" % str(e)[:120])

        for combo, expected in COMBOS:
            got = []
            for q, idx in zip(QUESTIONS, combo):
                done = False
                for _ in range(3):
                    done = page.evaluate(SET_JS, [q, idx])
                    if done:
                        break
                    page.wait_for_timeout(500)
                page.wait_for_timeout(350)
            page.wait_for_timeout(900)
            for q in QUESTIONS:
                got.append(page.evaluate(READ_JS, [q]))
            badge = page.evaluate(BADGE_JS)
            out["combos"].append({
                "combo": combo, "expected": expected,
                "radios_read_back": got, "badge": (badge or "").replace("\n", " ")[:300],
            })
            print("combo", combo, "-> read back", got, "|", expected)
            print("   badge:", (badge or "").replace("\n", " ")[:220])
        try:
            page.screenshot(path=os.path.join(BASE, "debug", "license_map.png"),
                            full_page=True)
        except Exception:
            pass
        out["upload_verify"] = dict(UPLOAD_VERIFY)
    except Exception as e:
        out["errors"].append(repr(e)[:300])
    finally:
        try:
            page.close()
        except Exception:
            pass

with open(os.path.join(BASE, "debug", "license_map.json"), "w",
          encoding="utf-8") as f:
    json.dump(out, f, indent=1, ensure_ascii=False)
print("saved debug/license_map.json")
