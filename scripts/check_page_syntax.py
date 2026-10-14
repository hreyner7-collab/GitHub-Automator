"""Diag: exact syntax-error position in the dashboard's inline script (CDP)."""
import sys, os, json
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from playwright.sync_api import sync_playwright
from uploader import ensure_browser, CDP_URL

ensure_browser()
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp(CDP_URL)
    page = b.contexts[0].new_page()
    page.goto("http://127.0.0.1:8000/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    src = page.evaluate("() => [...document.scripts].find(s => !s.src).textContent")
    print("script chars:", len(src), "lines:", src.count("\n") + 1)
    with open(os.path.join(BASE, "debug", "served_script.js"), "w",
              encoding="utf-8") as f:
        f.write(src)
    client = page.context.new_cdp_session(page)
    client.send("Runtime.enable")
    res = client.send("Runtime.compileScript", {
        "expression": src, "sourceURL": "dashboard-inline.js",
        "persistScript": False})
    exc = res.get("exceptionDetails")
    if not exc:
        print("NO SYNTAX ERROR in the served inline script")
    else:
        ln = exc.get("lineNumber")
        col = exc.get("columnNumber")
        print("ERROR:", exc.get("text"))
        print("lineNumber(0-based):", ln, "columnNumber:", col)
        lines = src.split("\n")
        # script line N (0-based) == HTML line 333 + N + 1
        for i in range(max(0, (ln or 0) - 3), min(len(lines), (ln or 0) + 3)):
            mark = ">>" if i == ln else "  "
            print("%s JS %3d | HTML %3d | %s" % (mark, i + 1, 334 + i, lines[i][:160]))
    page.close()
