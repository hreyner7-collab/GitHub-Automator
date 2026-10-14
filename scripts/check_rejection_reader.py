"""Offline gate for the rejection reader prompt 12 added.

The authorised publish of 2026-10-08 19:29 SUCCEEDED, so `REJECTION_JS` never ran
against a live bounce. This runs the real constant — the same string the uploader
evaluates after a failed Submit — against a fixture that mirrors the markup the
19:14 bounce left behind (`div.content-box model-file-content err-box` on step 1,
label "* Model files", Creality's `.el-form-item__error` message), and against a
clean form to prove it does not invent a rejection.

CHECK: .venv\\Scripts\\python.exe scripts/check_rejection_reader.py
EXPECT: PASS (exit 0)
"""
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uploader import REJECTION_JS, describe_submit_failure   # noqa: E402

BOUNCED = """<!doctype html><html><body>
  <div class="el-steps">
    <div class="el-step"><span class="badge" style="background:rgb(26,198,134)">1</span>
      <span>Upload Files</span></div>
    <div class="el-step"><span class="badge" style="background:rgb(229,229,229)">2</span>
      <span>Fill in the information</span></div>
  </div>
  <div class="el-form-item">
    <label class="el-form-item__label">Model Info</label>
    <div class="content-box model-file-content err-box">
      Model files
      <div>Drag your files here</div>
      <div>Домкрат+для+подъема+поддонов+1_10-го+масштаба.3mf 230.62 KB</div>
    </div>
    <div class="el-form-item__error">Please upload a model file</div>
  </div>
</body></html>"""

CLEAN = """<!doctype html><html><body>
  <div class="el-steps">
    <div class="el-step"><span class="badge" style="background:rgb(229,229,229)">1</span>
      <span>Upload Files</span></div>
    <div class="el-step"><span class="badge" style="background:rgb(26,198,134)">2</span>
      <span>Fill in the information</span></div>
  </div>
  <div class="el-form-item"><label class="el-form-item__label">Name</label>
    <input value="a model"></div>
</body></html>"""


def main() -> int:
    from playwright.sync_api import sync_playwright
    failures = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:
            print("SKIP: no Playwright Chromium available (%s)" % str(e)[:120])
            return 3
        page = browser.new_page()
        try:
            page.set_content(BOUNCED)
            got = page.evaluate(REJECTION_JS)
            print("bounced form ->", got)
            if got.get("step") != 1:
                failures.append("step: expected 1, got %r" % got.get("step"))
            flagged = got.get("flagged") or []
            if not flagged:
                failures.append("no field in Creality's error state was found")
            else:
                f = flagged[0]
                if "err-box" not in (f.get("cls") or ""):
                    failures.append("flagged class: %r" % f.get("cls"))
                if "Model files" not in (f.get("label") or ""):
                    failures.append("flagged label: %r" % f.get("label"))
            if "Please upload a model file" not in (got.get("messages") or []):
                failures.append("Creality's message was not read: %r" % got.get("messages"))
            sentence = describe_submit_failure(got.get("step"), flagged)
            print("sentence   ->", sentence)
            for must in ("bounced back to step 1", "Model files", "err-box",
                         "Nothing was published"):
                if must not in sentence:
                    failures.append("sentence is missing %r" % must)

            page.set_content(CLEAN)
            clean = page.evaluate(REJECTION_JS)
            print("clean form   ->", clean)
            if clean.get("step") != 2:
                failures.append("clean step: expected 2, got %r" % clean.get("step"))
            if clean.get("flagged"):
                failures.append("a clean form must not report flagged fields: %r"
                                % clean.get("flagged"))
            if describe_submit_failure(clean.get("step"), clean.get("flagged"),
                                       fallback="no reason") != "no reason":
                failures.append("a clean form must fall back, not invent a rejection")
        finally:
            browser.close()
    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: the rejection reader names step 1 + the flagged 'Model files' field "
          "and Creality's message, and stays quiet on a clean form")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
