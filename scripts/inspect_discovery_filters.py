"""Inspector: the Creality listing's Filters -> License control, exactly.

Opens https://www.crealitycloud.com/model in the logged-in debug Chrome,
opens the Filters panel, scrolls down to the License group, ticks ONLY the
licences we may publish (CC BY, CC BY-SA, CC BY-NC, CC BY-NC-SA, CC BY-NC-ND,
CC0 — never CC BY-ND, never Standard License), reads the ticked set back, then
applies and harvests the result cards.

Prints a report and writes debug/discovery_filters.json.
Exit code is non-zero when the ticked set does not equal the allow-list or when
no model cards come back — so a mis-clicked filter can never be mistaken for a
good run.

Usage: .venv/Scripts/python.exe scripts/inspect_discovery_filters.py [keyword]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright          # noqa: E402

from uploader import CDP_URL, ensure_browser             # noqa: E402

# the "3D Models" listing (the nav link's real URL — /model is a 404)
LISTING = "https://www.crealitycloud.com/model-category/3d-print-all"

# The licences this account may re-publish from (2026-10-09 policy): only those
# that allow redistribution, remixing AND commercial use.
ALLOW = ["CC0", "CC BY", "CC BY-SA"]
DENY = ["CC BY-NC", "CC BY-NC-SA", "CC BY-ND", "CC BY-NC-ND", "Standard License"]


def _text(el) -> str:
    return (el.inner_text() or "").strip()


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else ""
    ensure_browser()
    report: dict = {"listing": LISTING, "keyword": keyword,
                    "allow": ALLOW, "deny": DENY}

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        page = browser.contexts[0].new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        try:
            page.goto(LISTING, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(6000)

            if keyword:
                box = page.locator("input[placeholder*='earch' i]").first
                box.click(timeout=5000)
                box.fill(keyword)
                page.keyboard.press("Enter")
                page.wait_for_timeout(5000)

            # sort: most-downloaded first
            try:
                page.get_by_text("Downloads", exact=True).first.click(timeout=5000)
                page.wait_for_timeout(3000)
            except Exception as e:
                report["sort_error"] = str(e)[:120]

            # open the filter panel
            report["filters_button"] = page.get_by_text("Filters", exact=False).count()
            try:
                page.get_by_text("Filters", exact=False).first.click(timeout=5000)
                page.wait_for_timeout(2500)
            except Exception as e:
                report["filters_click_error"] = str(e)[:160]

            # the panel scrolls; the License group sits far down
            panel = page.locator(".el-drawer, .filter, [class*=drawer], [class*=filter-panel]").first
            for _ in range(8):
                try:
                    if panel.count() and panel.is_visible():
                        panel.evaluate("el => el.scrollTop = el.scrollHeight")
                    else:
                        page.mouse.wheel(0, 800)
                except Exception:
                    page.mouse.wheel(0, 800)
                page.wait_for_timeout(500)
                if page.get_by_text("License", exact=True).count():
                    break
            try:
                page.get_by_text("License", exact=True).first.scroll_into_view_if_needed(timeout=4000)
            except Exception:
                pass
            page.wait_for_timeout(600)

            # collect every licence option with its state
            opts = page.evaluate(r"""() => {
                const labels = ['CC BY','CC BY-SA','CC BY-NC','CC BY-NC-SA',
                                'CC BY-ND','CC BY-NC-ND','CC0','Standard License'];
                const out = [];
                const seen = new Set();
                for (const el of document.querySelectorAll(
                        'label,div,span,li')) {
                    const t = ((el.innerText||'').trim());
                    if (!labels.includes(t) || seen.has(el)) continue;
                    // the row that actually holds the control
                    const row = el.closest('label') || el.parentElement || el;
                    const inp = row.querySelector('input[type=checkbox]');
                    const inner = row.querySelector('.el-checkbox__inner,[class*=check]');
                    const r = row.getBoundingClientRect();
                    seen.add(el);
                    out.push({label: t, tag: row.tagName.toLowerCase(),
                              cls: (row.className||'').toString().slice(0,60),
                              checked: !!(inp && inp.checked) ||
                                       (/is-checked|checked/.test(
                                          (inner?inner.className:'').toString())),
                              box: [Math.round(r.x), Math.round(r.y),
                                    Math.round(r.width), Math.round(r.height)]});
                }
                return out;
            }""")
            report["licence_options"] = opts

            # tick the allow-list by clicking each option's row center
            ticked = []
            for want in ALLOW:
                hit = None
                for o in opts:
                    if o["label"] == want:
                        hit = o
                        break
                if not hit:
                    report.setdefault("missing", []).append(want)
                    continue
                x, y = hit["box"][0], hit["box"][1]
                if hit["box"][2] <= 0:
                    continue
                try:
                    page.mouse.click(x + 6, y + hit["box"][3] / 2)
                    page.wait_for_timeout(250)
                    ticked.append(want)
                except Exception as e:
                    report.setdefault("tick_errors", []).append([want, str(e)[:80]])

            # read the whole licence set back
            state = page.evaluate(r"""() => {
                const labels = ['CC BY','CC BY-SA','CC BY-NC','CC BY-NC-SA',
                                'CC BY-ND','CC BY-NC-ND','CC0','Standard License'];
                const out = {};
                for (const el of document.querySelectorAll('label,div,span,li')) {
                    const t = ((el.innerText||'').trim());
                    if (!labels.includes(t)) continue;
                    const row = el.closest('label') || el.parentElement || el;
                    const inp = row.querySelector('input[type=checkbox]');
                    const inner = row.querySelector('.el-checkbox__inner,[class*=check]');
                    out[t] = (!!(inp && inp.checked)) ||
                             /is-checked|checked/.test((inner?inner.className:'').toString());
                }
                return out;
            }""")
            report["licence_state_after_click"] = state
            report["ticked_on"] = [k for k, v in state.items() if v]
            report["ticked_off"] = [k for k, v in state.items() if not v]

            # apply the filter
            for label in ("Apply", "Confirm", "OK", "Search"):
                try:
                    page.get_by_text(label, exact=True).first.click(timeout=3000)
                    report["applied_with"] = label
                    break
                except Exception:
                    continue
            page.wait_for_timeout(5000)

            cards = page.evaluate(r"""() => {
                const seen = new Map();
                for (const a of document.querySelectorAll('a[href*="/model-detail/"]')) {
                    const url = a.href.split('?')[0];
                    if (seen.has(url)) continue;
                    let name = (a.innerText||'').trim().split('\n')[0];
                    if (!name) {
                        const img = a.querySelector('img');
                        name = (img && (img.alt||'')) || '';
                    }
                    seen.set(url, name.slice(0, 90));
                }
                return [...seen.entries()].map(([url, name]) => ({url, name}));
            }""")
            report["cards"] = cards[:12]
            report["card_count"] = len(cards)
        finally:
            page.close()

    (ROOT / "debug").mkdir(exist_ok=True)
    (ROOT / "debug" / "discovery_filters.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")

    print("filters button found:", report.get("filters_button"))
    print("licence options seen:", [o["label"] for o in report.get("licence_options", [])])
    print("ticked ON :", report.get("ticked_on"))
    print("ticked OFF:", report.get("ticked_off"))
    print("applied with:", report.get("applied_with"))
    print("cards harvested:", report.get("card_count"))
    for c in report.get("cards", [])[:8]:
        print("   ", c["name"][:50], "|", c["url"].split("/")[-1][:50])

    want_on = set(ALLOW)
    want_off = set(DENY)
    state = report.get("licence_state_after_click") or {}
    problems = []
    for lic in ALLOW:
        if not state.get(lic):
            problems.append("not ticked: " + lic)
    for lic in DENY:
        if state.get(lic):
            problems.append("should be unticked: " + lic)
    if not report.get("card_count"):
        problems.append("no model cards came back")
    if problems:
        print("\nFAIL: " + "; ".join(problems))
        return 1
    print("\nPASS: licence filter == allow-list and cards are coming back")
    print("wrote debug/discovery_filters.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
