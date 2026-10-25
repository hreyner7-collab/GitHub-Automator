"""Publish a model to MakerWorld by driving MakerWorld's own wizard in the
user's Chrome over CDP.

Kept deliberately separate from uploader.py (the Creality target): different
UI kit (React + MUI vs Element UI/Vue), different flow (file attach creates a
draft, then every field lives on one draft-edit page), different dedup memory
(makerworld_ledger.py). Nothing here imports Creality code, and nothing here
changes it.

The measured map this is written against is MAKERWORLD-FLOW-CONFIG.md; the raw
page dumps it was derived from are in MAKERWORLD-STUDY/.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import subprocess
import time

from playwright.async_api import async_playwright

import makerworld_flow as mw
import timing

BASE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.join(BASE, "downloads")
PROFILE_DIR = os.path.join(BASE, "chrome_debug_profile")
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
STUDY_DIR = os.path.join(BASE, "MAKERWORLD-STUDY")

BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

NO_PRINT_PHOTOS_ERROR = (
    "MakerWorld requires at least 3 photographs of the PRINTED model in its "
    f"'{mw.SLOT_REAL_PHOTOS}' slot (measured wording: 'Upload ≥3 photos of the printed "
    "model, covering 3 views'). The pipeline's edited studio renders are not print "
    "photos, so nothing was uploaded. Pass print_photos=[paths to photos you printed].")


def _debug_port_up() -> bool:
    import socket
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", CDP_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_browser() -> bool:
    """Start Chrome with the debug port if it is not already up, pointed at
    MakerWorld. Same profile as the Creality path uses, because that is the
    window the user logged MakerWorld into."""
    if _debug_port_up():
        return True
    exe = next((p for p in BROWSER_PATHS if os.path.exists(p)), None)
    if not exe:
        return False
    subprocess.Popen(
        [exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check", mw.ORIGIN + "/en/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if _debug_port_up():
            return True
        time.sleep(0.5)
    return _debug_port_up()


# ── DOM helpers ─────────────────────────────────────────────────────────────
# Every control is found the same way: locate the MEASURED text that owns it,
# then take the first matching element after it in document order. That is how
# two identical "Add Photo" inputs are told apart, and how a licence option is
# picked from its own question rather than the first "Yes" on the page.
# Index of the <input type=file> that belongs to a measured slot, in the same
# document order the form renders (measured slot headings: '4:3 Cover',
# '3:4 Cover is prioritized in App', 'Real life photos', 'Model Pictures').
SLOT_INDEX_JS = r"""
(marker) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('body *')];
  let start = -1;
  for (let i = 0; i < nodes.length; i++) {
    const e = nodes[i];
    if (!e.childElementCount && norm(e.textContent).startsWith(marker)) { start = i; break; }
  }
  const inputs = [...document.querySelectorAll('input[type=file]')];
  if (start >= 0) {
    for (let i = 0; i < inputs.length; i++) {
      if (nodes.indexOf(inputs[i]) > start) return { index: i, how: 'after-heading', marker };
    }
  }
  // fallback: the smallest ancestor block that contains both the marker text
  // and this input
  let best = -1, bestLen = 1e9;
  for (let i = 0; i < inputs.length; i++) {
    let p = inputs[i];
    for (let d = 0; d < 10 && p; d++, p = p.parentElement) {
      const t = norm(p.innerText);
      if (t && t.includes(marker) && t.length < bestLen) { best = i; bestLen = t.length; break; }
    }
  }
  return { index: best >= 0 ? best : -1, how: best >= 0 ? 'ancestor-text' : 'not-found', marker };
}
"""

# React needs the native value setter before the input event, or its state
# never sees the text (same finding as the Creality remix box).
SET_VALUE_JS = r"""
(args) => {
  const el = args.el, text = args.text || '';
  const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
              : el.tagName === 'INPUT' ? window.HTMLInputElement.prototype : null;
  if (!proto) return false;
  const d = Object.getOwnPropertyDescriptor(proto, 'value');
  d.set.call(el, text);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return el.value === text;
}
"""

# The licence NAME the form renders for the answers given, read ONLY from
# inside the License block. The measured Model Origin warning names other
# licences as examples ("CC-BY-ND, CC-BY-NC-ND … are not allowed"), so a search
# over the whole page would read that warning as this listing's licence — the
# same trap the Creality form has (UPLOAD-FLOW-CONFIG.md).
LICENSE_READ_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('body *')];
  const idx = (txt) => nodes.findIndex(e =>
    !e.childElementCount && norm(e.textContent).startsWith(txt));
  let a = idx('Is this model supported only for printing through Bambu Handy?');
  if (a < 0) a = idx('Allow adaptations');
  if (a < 0) return '';
  const b = idx('Visibility');
  const stop = b > a ? b : nodes.length;
  for (let i = a; i < stop; i++) {
    const t = norm(nodes[i].textContent || nodes[i].innerText);
    if (/^(CC[\s-]?0|CC[\s-]?BY(-[\s-]?(NC|SA|ND)){0,2}|Standard Digital File License)/i.test(t))
      return t.slice(0, 60);
  }
  return '';
}
"""

# What the whole step-2 form says about itself right now — the evidence a
# failure report is built from instead of a guess.
FORM_STATE_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  return {
    url: location.href,
    draftId: (location.href.match(/\/drafts\/(\d+)/) || [])[1] || '',
    designId: (location.href.match(/\/models\/(\d+)/) || [])[1] || '',
    fileInputs: [...document.querySelectorAll('input[type=file]')].map(e => e.accept.slice(0, 60)),
    radios: [...document.querySelectorAll('input[type=radio]')].filter(e => e.checked)
      .map(e => norm((e.closest('label') || e).innerText).slice(0, 120)),
    checkboxes: [...document.querySelectorAll('input[type=checkbox]')].filter(e => e.checked)
      .map(e => norm((e.closest('label') || e).innerText).slice(0, 120)),
    textFields: [...document.querySelectorAll('input[type=text]')].filter(vis)
      .map(e => ({ ph: (e.placeholder || '').slice(0, 80), v: (e.value || '').slice(0, 80) })),
    editor: (() => { const ce = document.querySelector('[contenteditable=true]');
                     return ce ? norm(ce.innerText).slice(0, 200) : ''; })(),
    errors: [...document.querySelectorAll('[role=alert], .MuiFormHelperText-root, [class*=error]')]
      .filter(vis).map(e => norm(e.innerText).slice(0, 140)).filter(Boolean).slice(0, 20),
    buttons: [...document.querySelectorAll('button')].filter(vis)
      .map(e => norm(e.innerText)).filter(Boolean).slice(0, 30),
    body: norm(document.body.innerText).slice(0, 4000),
  };
}
"""

# What the form's own counters say about the two photo slots (measured wording:
# 'Real life photos ( 0 / 16 )', 'Model Pictures ( 0 / 16 )').
PHOTO_COUNT_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = {};
  for (const m of ['Real life photos', 'Model Pictures']) {
    const el = [...document.querySelectorAll('body *')]
      .find(e => !e.childElementCount && norm(e.textContent).startsWith(m));
    out[m] = el ? norm(el.textContent).slice(0, 60) : '';
  }
  return out;
}
"""

# The Model Origin suggestion list is the one part of the wizard the study never
# rendered (the walk stopped before typing a source URL), so no row markup is
# measured. These helpers read whatever MakerWorld shows under the search box,
# and the driver chooses a row by its TEXT and confirms it by re-reading the
# block — never by position and never by a selector assumed to be right.
SOURCE_ROW_SELECTOR = ("[class*=origin],[class*=Origin],[class*=search-result] li,"
                       "[class*=suggest] li,[role=option]")

SOURCE_ROWS_JS = r"""
(sel) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect();
                       return r.width > 0 && r.height > 0; };
  const nodes = [...document.querySelectorAll(sel)];
  const out = [];
  nodes.forEach((e, i) => {
    if (!vis(e)) return;
    const t = norm(e.innerText);
    if (t) out.push({ text: t.slice(0, 140), index: i });
  });
  return out.slice(0, 12);
}
"""

# Click row `index` of the same query, but only while it still reads the name
# that was picked: if the list re-rendered between the two calls the position is
# worthless, so this refuses instead of registering a look-alike as the source.
SOURCE_ROW_CLICK_JS = r"""
(args) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const e = [...document.querySelectorAll(args.sel)][args.index];
  if (!e) return { clicked: false, why: 'that row is no longer on the page' };
  const t = norm(e.innerText);
  if (args.want && !t.toLowerCase().includes(args.want))
    return { clicked: false, text: t.slice(0, 140),
             why: `row ${args.index} now reads '${t.slice(0, 80)}'` };
  (e.querySelector('button, a, [class*=card], [class*=item]') || e).click();
  return { clicked: true, text: t.slice(0, 140) };
}
"""

# The Model Origin block, read between its own heading and the 'Model Name'
# heading below it. With a name asked for it answers "is this source registered";
# with none it returns the block text for the failure report.
ORIGIN_STATE_JS = r"""
(args) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('body *')];
  const idx = (txt) => nodes.findIndex(e =>
    !e.childElementCount && norm(e.textContent).startsWith(txt));
  const a = idx('Model Origin');
  if (a < 0) return args.want ? false : '';
  const b = idx('Model Name');
  const block = nodes.slice(a, b > a ? b : a + 1)
    .map(e => norm(e.textContent)).filter(t => t && t.length < 300)
    .join(' | ').slice(0, 600);
  return args.want ? block.toLowerCase().includes(String(args.want).toLowerCase()) : block;
}
"""

DUPLICATE_RE = re.compile(r"(?i)(already\s+exist|duplicate|same\s+model|"
                          r"has\s+been\s+uploaded|模型重复|重复)")
ERROR_RE = re.compile(r"(?i)(\bfailed\b|\berror\b|required|cannot|can not|"
                      r"please (fill|enter|select|upload)|not\s+allowed)")


def _shot(name: str) -> str:
    d = os.path.join(DOWNLOADS, "_shots")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


async def _settle(page, budget_ms: int, step_ms: int, cond) -> bool:
    """Poll a condition instead of sleeping a fixed amount (project rule: no
    clock waits for something the code can ask about). `cond` may be a plain
    callable or an async one."""
    for _ in range(max(1, int(budget_ms / step_ms))):
        try:
            got = cond()
            if inspect.isawaitable(got):
                got = await got
            if got:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(step_ms)
    return False


async def _click_text(page, text: str) -> dict:
    """Click a control by its measured visible text (MakerWorld's step-1 branch
    choice is a plain text control, not a label), then read the page back."""
    try:
        loc = page.get_by_text(text, exact=True).first
        if await loc.count() == 0:
            loc = page.get_by_text(text, exact=False).first
        if await loc.count() == 0:
            return {"clicked": False, "why": f"no control reads {text!r}"}
        await loc.click(timeout=8000)
        return {"clicked": True}
    except Exception as exc:  # noqa: BLE001
        return {"clicked": False, "why": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _click_label(page, marker: str, option: str) -> dict:
    """Click a MUI radio/label by its measured text, inside the block that
    begins with the measured question. DOM click, then read the checked state
    back — the click is only believed after the form agrees."""
    handle = await page.evaluate_handle("""
      (args) => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        const nodes = [...document.querySelectorAll('body *')];
        let start = -1;
        if (args.marker) {
          for (let i = 0; i < nodes.length; i++) {
            if (!nodes[i].childElementCount && norm(nodes[i].textContent).startsWith(args.marker)) {
              start = i; break;
            }
          }
          // The question's own heading is gone: refuse rather than answer the
          // first label with this text anywhere on the form — the four licence
          // questions all offer a bare 'Yes' and 'No'.
          if (start < 0) return null;
        }
        const labels = [...document.querySelectorAll('label')];
        let best = null;
        for (const l of labels) {
          const t = norm(l.innerText);
          if (!t || !l.querySelector('input')) continue;
          const exact = t === args.want;
          const pre = t.startsWith(args.want);
          if (!(exact || pre)) continue;
          const pos = nodes.indexOf(l);
          if (start >= 0 && pos < start) continue;
          if (!best || (exact && norm(best.innerText) !== args.want)) best = l;
          if (exact) break;
        }
        return best || null;
      }
    """, {"marker": marker, "want": option})
    el = handle.as_element()
    if el is None:
        return {"clicked": False, "why": f"no label for {option!r}"
                                          + (f" under the heading {marker!r}" if marker else "")}
    try:
        await el.evaluate("e => e.click()")
    except Exception as exc:  # noqa: BLE001
        return {"clicked": False, "why": f"click raised {type(exc).__name__}"}
    await page.wait_for_timeout(250)
    state = await page.evaluate("""
      (args) => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        const nodes = [...document.querySelectorAll('body *')];
        let start = -1;
        if (args.marker) {
          for (let i = 0; i < nodes.length; i++) {
            if (!nodes[i].childElementCount && norm(nodes[i].textContent).startsWith(args.marker)) {
              start = i; break;
            }
          }
        }
        for (const l of [...document.querySelectorAll('label')]) {
          if (norm(l.innerText) !== args.want) continue;
          if (start >= 0 && nodes.indexOf(l) < start) continue;
          const i = l.querySelector('input');
          if (i) return { checked: !!i.checked, text: norm(l.innerText) };
        }
        return { checked: false, text: '' };
      }
    """, {"want": option, "marker": marker})
    return {"clicked": bool(state.get("checked")), "read_back": state,
            "why": "" if state.get("checked") else f"{option!r} still not checked"}


async def _attach_to_slot(page, marker: str, paths: list[str]) -> dict:
    """Give files to the hidden <input type=file> that belongs to a measured
    slot heading. Attaching is reported by the form's own counters, not by
    input.files (Element UI and MUI both clear it after taking the file)."""
    where = await page.evaluate(SLOT_INDEX_JS, marker)
    idx = where.get("index", -1)
    if idx < 0:
        return {"ok": False, "error": f"no file input found for the '{marker}' slot",
                "how": where.get("how")}
    inputs = page.locator("input[type=file]")
    try:
        await inputs.nth(idx).set_input_files(paths, timeout=60_000)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"attaching to '{marker}' failed: {exc}", "index": idx}
    return {"ok": True, "index": idx, "how": where.get("how"), "count": len(paths)}


# Address a field by the measured heading that owns it. When the heading is not
# on the page the answer is 'no field' — never 'the first field on the page',
# which is the site search box.
FIELD_LOCATOR_JS = r"""
  (args) => {
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const nodes = [...document.querySelectorAll('body *')];
    let start = -1;
    if (args.marker) {
      for (let i = 0; i < nodes.length; i++) {
        if (!nodes[i].childElementCount && norm(nodes[i].textContent).startsWith(args.marker)) {
          start = i; break;
        }
      }
      if (start < 0) return null;
    }
    const fields = [...document.querySelectorAll('input[type=text], input:not([type]), textarea')];
    for (const f of fields) {
      if (f.disabled || f.readOnly) continue;
      if (args.want_ph && !(f.placeholder || '').includes(args.want_ph)) continue;
      if (args.marker && nodes.indexOf(f) < start) continue;
      return f;
    }
    return null;
  }
"""


async def _fill_text(page, marker: str = "", want_ph: str = "", text: str = "") -> dict:
    """Type into a measured text field, addressed by the heading before it or
    by its own measured placeholder, and read the value back."""
    handle = await page.evaluate_handle(FIELD_LOCATOR_JS,
                                       {"marker": marker, "want_ph": want_ph})
    el = handle.as_element()
    if el is None:
        return {"ok": False, "error": f"no text field for marker={marker!r} ph={want_ph!r}"}
    ok = await el.evaluate(SET_VALUE_JS, {"el": el, "text": text})
    await page.wait_for_timeout(200)
    back = await el.evaluate("e => e.value")
    return {"ok": bool(ok) and (back or "") == text, "read_back": (back or "")[:120]}


async def _type_tags(page, marker: str, tags: list[str]) -> dict:
    """Tags are committed by the Enter key (measured helper text: 'Press "Enter"
    to separate tags'), so they are typed through the keyboard, not set as a
    value, and the form's own counter is read back afterwards."""
    handle = await page.evaluate_handle(FIELD_LOCATOR_JS, {"marker": marker, "want_ph": ""})
    el = handle.as_element()
    if el is None:
        return {"ok": False, "error": f"no tag input under {marker!r}", "planned": tags}
    await el.click()
    for t in tags:
        await page.keyboard.type(t[: mw.MAX_TAG_INPUT], delay=5)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(150)
    back = await page.evaluate("""
      (m) => { const norm = s => (s||'').replace(/\\s+/g,' ').trim();
               const el = [...document.querySelectorAll('body *')]
                 .find(e => !e.childElementCount && norm(e.textContent).startsWith(m));
               return el ? norm(el.textContent).slice(0, 60) : ''; }
    """, marker)
    return {"ok": bool(back), "planned": tags, "counter": back}


async def _click_button(page, text: str, force: bool = False) -> dict:
    try:
        loc = page.locator("button", has_text=text).first
        if await loc.count() == 0:
            return {"clicked": False, "error": f"no button containing {text!r}"}
        await loc.click(timeout=8000, force=force)
        return {"clicked": True}
    except Exception as exc:  # noqa: BLE001
        return {"clicked": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def upload(name: str, description: str, stl_path: str, image_paths: list[str],
                 source_url: str = "", dry_run: bool = False, license_raw: str = "",
                 print_photos: list[str] | None = None, remix_source_url: str = "",
                 remix_source_name: str = "", tags: list[str] | None = None,
                 category: str = "", visibility: str = "Public",
                 kind: str = "remix", should_stop=None) -> dict:
    """Drive MakerWorld's publish wizard for one model. Same result shape the
    dashboard already handles from uploader.upload: success / url / verify /
    message / error, plus draft_id and design_id, which are MakerWorld's own."""
    _mark = timing.Steps("makerworld-publish")
    verify: dict = {}

    covers = mw.usable_images(image_paths)
    photos = mw.usable_images(print_photos or [])
    if not photos and not dry_run:
        return {"success": False, "error": NO_PRINT_PHOTOS_ERROR, "verify": verify}

    verdict = mw.validate_plan(name=name, description=description, source_url=source_url,
                               model_file=stl_path, cover_images=covers, print_photos=photos,
                               remix_source_url=remix_source_url, license_raw=license_raw,
                               kind=kind)
    verify["plan"] = verdict
    if not verdict["ok"]:
        return {"success": False,
                "error": "Refusing to publish to MakerWorld: " + "; ".join(verdict["blockers"]),
                "verify": verify}
    plan = verdict["plan"]

    if not await asyncio.to_thread(_debug_port_up):
        if not await asyncio.to_thread(ensure_browser):
            return {"success": False, "error": "Could not start Chrome. Open Chrome once with "
                    "the debug launcher and log into MakerWorld, then retry."}
        await asyncio.sleep(2)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Could not attach to Chrome on {CDP_URL}. ({exc})"}
        ctx = browser.contexts[0]

        # Close wizard tabs a previous publish left behind (same reason as the
        # Creality path: 20 half-filled forms is a Chrome out of memory). The
        # user's own MakerWorld tabs are never touched.
        for stale in list(ctx.pages):
            try:
                u = stale.url or ""
                if "/my/models/publish" in u or "/my/models/drafts/" in u:
                    await stale.close()
            except Exception:
                pass
        _mark("browser")

        page = await ctx.new_page()
        await page.bring_to_front()

        draft_hits: list[dict] = []

        def _on_response(resp):
            try:
                u = resp.url
                if "/api/v1/design-service/my/draft" in u or "/my/upload" in u:
                    draft_hits.append({"method": resp.request.method, "url": u[:200],
                                       "status": resp.status})
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            # 1) step 1 — the file. Measured: the wizard opens on the MakerLab
            #    branch, the 3D branch is a text control, and attaching a model
            #    file creates the draft and moves the URL to /drafts/<id>/edit.
            await page.goto(mw.entry_url(kind), wait_until="domcontentloaded", timeout=90_000)
            # the wizard is a client-rendered page: wait until its file inputs
            # exist rather than sleeping a fixed amount (they stay hidden even
            # once they are there, so visibility is the wrong thing to poll)
            await _settle(page, 20_000, 400,
                          lambda: page.locator("input[type=file]").count() > 0)

            branch = await _click_text(page, mw.STL_BRANCH_TEXT)
            verify["stl_branch"] = branch
            model_idx = await page.evaluate("""
              () => { const ins = [...document.querySelectorAll('input[type=file]')];
                      for (let i = 0; i < ins.length; i++)
                        if ((ins[i].accept || '').toLowerCase().includes('.stl')) return i;
                      return -1; }
            """)
            if model_idx < 0:
                return {"success": False,
                        "error": "MakerWorld's step 1 shows no model-file input (measured accept= "
                                 "list contains .stl). The form changed — re-measure before "
                                 "publishing anything.",
                        "verify": verify}
            verify["model_input_index"] = model_idx

            try:
                await page.locator("input[type=file]").nth(int(model_idx)) \
                     .set_input_files([stl_path], timeout=120_000)
            except Exception as exc:  # noqa: BLE001
                return {"success": False,
                        "error": f"Attaching {os.path.basename(stl_path)} to MakerWorld failed: "
                                 f"{str(exc)[:180]}", "verify": verify}
            _mark("attach")

            # the draft is created by the site itself; wait for its id to show up
            got_draft = await _settle(page, 90_000, 700,
                                      lambda: bool(mw.draft_id_from_url(page.url)))
            draft_id = mw.draft_id_from_url(page.url)
            verify["draft_id"] = draft_id
            verify["draft_api"] = draft_hits[-6:]
            if not got_draft or not draft_id:
                body = await page.evaluate("() => (document.body.innerText || '').slice(0, 600)")
                return {"success": False,
                        "error": "MakerWorld never created a draft for the attached file, so the "
                                 f"wizard could not advance. Page says: {body[:300]}",
                        "verify": verify}

            if dry_run or os.environ.get("MAKERWORLD_DRY_RUN") == "1":
                # everything up to the draft is real; nothing below it is touched
                try:
                    await page.close()
                except Exception:
                    pass
                return {"success": True, "dry_run": True, "draft_id": draft_id,
                        "verify": verify,
                        "message": "Dry run: MakerWorld accepted the file and made draft "
                                   f"{draft_id}. Nothing was filled or published."}

            nxt = await _click_button(page, mw.NEXT_STEP_BUTTON)
            verify["next_step"] = nxt
            if not nxt.get("clicked"):
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": "MakerWorld's step 1 would not advance: its "
                                 f"'{mw.NEXT_STEP_BUTTON}' control was not pressed "
                                 f"({nxt.get('error') or 'unknown reason'}). Nothing was published.",
                        "verify": verify}
            opened = await _settle(page, 25_000, 500,
                                   lambda: page.evaluate(
                                       "() => !!document.querySelector('[contenteditable=true]')"))
            state = await page.evaluate(FORM_STATE_JS)
            verify["step2_opened"] = {"url": state.get("url"),
                                      "radios": len(state.get("radios") or []),
                                      "file_inputs": len(state.get("fileInputs") or []),
                                      "editor": bool(state.get("editor")) or opened}
            if not opened:
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": "MakerWorld never rendered step 2 (Model Information) after "
                                 "the file was accepted, so nothing was filled or published. "
                                 "Its own text: " + (state.get("body") or "")[:240],
                        "verify": verify}
            _mark("step2")

            # 2) Model Origin — the remix source. Measured: an 'Add' control and
            #    a search box whose placeholder is the string below. UNMEASURED:
            #    the row markup MakerWorld renders for suggestions, so the rows
            #    are read off the page, reported in verify, and chosen by their
            #    TEXT — never by position (the lesson from Creality's four failed
            #    uploads: a prefix look-alike registers the wrong source).
            origin = {}
            if kind == "remix" and plan["remix_source_url"]:
                add = await _click_label(page, "Model Origin", mw.SLOT_ORIGIN_ADD)
                origin["add"] = add
                if not add.get("clicked"):
                    origin["add_button"] = await page.evaluate("""
                      (m) => { const norm = s => (s||'').replace(/\\s+/g,' ').trim();
                               const nodes=[...document.querySelectorAll('body *')];
                               let start=-1;
                               for (let i=0;i<nodes.length;i++){ if(!nodes[i].childElementCount &&
                                 norm(nodes[i].textContent).startsWith(m)){start=i;break;} }
                               for (const b of [...document.querySelectorAll('button')]) {
                                 if (norm(b.innerText) === 'Add' && nodes.indexOf(b) > start) {
                                   b.click(); return true; } }
                               return false; }
                    """, "Model Origin")
                typed = await _fill_text(page, want_ph=mw.SOURCE_INPUT_PLACEHOLDER,
                                         text=plan["remix_source_url"])
                origin["typed"] = typed
                if not typed.get("ok"):
                    verify["origin"] = origin
                    return {"success": False, "url": page.url, "draft_id": draft_id,
                            "error": "MakerWorld's Model Origin box did not take "
                                     f"{plan['remix_source_url']} (measured placeholder: "
                                     f"'{mw.SOURCE_INPUT_PLACEHOLDER[:40]}…'). A remix with no "
                                     "registered source is not published.",
                            "verify": verify}

                def _rows():
                    return page.evaluate(SOURCE_ROWS_JS, SOURCE_ROW_SELECTOR)
                have = await _settle(page, 15_000, 700, _rows)
                rows = await _rows() if have else []
                origin["rows"] = [r["text"] for r in rows]
                want_name = remix_source_name or name
                picked = mw.pick_source_row([r["text"] for r in rows], want_name,
                                            mw.design_id_from_url(plan["remix_source_url"]))
                origin["picked"] = picked
                origin["want"] = want_name
                if picked < 0 or not rows:
                    verify["origin"] = origin
                    return {"success": False, "url": page.url, "draft_id": draft_id,
                            "error": "MakerWorld offered no source-model row that is "
                                     f"'{want_name}' (it offered: "
                                     + "; ".join(origin["rows"][:4]) + "). Refusing to register a "
                                     "look-alike as this listing's source.",
                            "verify": verify}
                origin["clicked"] = await page.evaluate(
                    SOURCE_ROW_CLICK_JS,
                    {"sel": SOURCE_ROW_SELECTOR, "index": rows[picked]["index"],
                     "want": mw._norm_words(want_name)})
                if not origin["clicked"].get("clicked"):
                    verify["origin"] = origin
                    return {"success": False, "url": page.url, "draft_id": draft_id,
                            "error": "MakerWorld's suggestion list changed between reading "
                                     "and clicking it (" + str(origin["clicked"].get("why")) +
                                     "). Refusing to click an unknown row: that is how the "
                                     "wrong model ends up declared as this listing's source.",
                            "verify": verify}

                def _registered():
                    return page.evaluate(ORIGIN_STATE_JS, {"want": mw._norm_words(want_name)})
                reg = await _settle(page, 12_000, 500, _registered)
                origin["registered"] = bool(reg)
                origin["block"] = await page.evaluate(ORIGIN_STATE_JS, {"want": ""})
                verify["origin"] = origin
                if not reg:
                    return {"success": False, "url": page.url, "draft_id": draft_id,
                            "error": "MakerWorld's Model Origin block does not name the source "
                                     f"model after the row was clicked (block reads: "
                                     f"'{(origin['block'] or '')[:160]}'). Nothing was submitted — "
                                     "a remix that does not declare its source is what gets a "
                                     "listing removed.",
                            "verify": verify}
            _mark("origin")

            # 3) Model Name / Tags / Category — measured limits applied first.
            named = await _fill_text(page, marker="Model Name", text=plan["name"])
            verify["name"] = named
            tag_list = mw.plan_tags(tags or [], remix_source_name or name)
            if tag_list:
                verify["tags"] = await _type_tags(page, "Tags", tag_list)
            if category:
                verify["category"] = await _fill_text(page, marker="Category", text=category)

            # 4) Licence — answer the questions, then read back the licence NAME
            #    MakerWorld itself renders. The mapping is MakerWorld's, not
            #    ours, so the name is the only proof worth having.
            lic = plan["license"]
            lic_res = {}
            for q, opt in lic["answers"].items():
                lic_res[q] = await _click_label(page, q.rstrip("?"), opt)
            rendered = await page.evaluate(LICENSE_READ_JS)
            lic_res["read_back"] = rendered
            lic_res["code"] = lic["code"]
            verify["license"] = lic_res
            if not rendered:
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": "MakerWorld did not render a licence name inside the License "
                                 "block after the questions were answered, so this publish "
                                 "cannot be checked against the source licence. Nothing was "
                                 "submitted.",
                        "verify": verify}
            if not mw.license_name_matches(rendered, lic["code"]):
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": f"MakerWorld renders this listing as {rendered}, but the source "
                                 f"licence is {(lic['code'] or 'unknown').upper()}. Refusing to "
                                 "publish a licence the source does not grant.",
                        "verify": verify}
            _mark("licence")

            # 5) Covers (4:3 and 3:4 are both on the form) and the photo slots.
            cover_plan = plan["covers"]
            covers_res = {}
            for slot in (mw.SLOT_COVER_43, mw.SLOT_COVER_34):
                paths = cover_plan[:1]
                if paths:
                    covers_res[slot] = await _attach_to_slot(page, slot, paths)
            real_res = await _attach_to_slot(page, mw.SLOT_REAL_PHOTOS, plan["print_photos"])
            gallery = [p for p in cover_plan[1:][:mw.MAX_PHOTOS]]
            gallery_res = await _attach_to_slot(page, mw.SLOT_MODEL_PHOTOS, gallery) \
                if gallery else {"ok": True, "skipped": "no extra images"}
            counts = await page.evaluate(PHOTO_COUNT_JS)
            verify["covers"] = covers_res
            verify["real_photos"] = {"result": real_res, "counter": counts.get(mw.SLOT_REAL_PHOTOS)}
            verify["model_pictures"] = {"result": gallery_res,
                                        "counter": counts.get(mw.SLOT_MODEL_PHOTOS)}
            if not real_res.get("ok"):
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": "The real-life photo slot could not be filled "
                                 f"({real_res.get('error')}).", "verify": verify}
            _mark("images")

            # 6) Description — MakerWorld's editor is a contenteditable, so the
            #    text is typed into it, then read back from it.
            desc = plan["description"]
            try:
                ed = page.locator("[contenteditable=true]").first
                await ed.click(timeout=8000)
                await page.keyboard.type(desc[:2000], delay=6)
            except Exception as exc:  # noqa: BLE001
                verify["description"] = {"ok": False, "error": str(exc)[:160]}
            state = await page.evaluate(FORM_STATE_JS)
            wrote = (state.get("editor") or "").strip()
            verify["description"] = {"ok": bool(wrote), "chars": len(wrote),
                                     "head": wrote[:120]}

            # 7) Visibility — measured default is Public; only click if it is not.
            if visibility and visibility != "Public":
                verify["visibility"] = await _click_label(page, "Visibility", visibility)
            else:
                verify["visibility"] = {"clicked": True, "how": "left at the form's own default"}

            if os.environ.get("MAKERWORLD_NO_SUBMIT") == "1":
                await page.screenshot(path=_shot("mw_before_submit.png"), full_page=True)
                return {"success": True, "no_submit": True, "url": page.url,
                        "draft_id": draft_id, "verify": verify,
                        "message": "Every field is filled and MakerWorld's draft "
                                   f"{draft_id} is open, but MAKERWORLD_NO_SUBMIT is set, so "
                                   "Publish was not pressed. downloads/_shots/"
                                   "mw_before_submit.png."}

            if should_stop and should_stop():
                return {"success": False, "stopped": True, "url": page.url,
                        "draft_id": draft_id, "verify": verify,
                        "error": "run stopped before Publish — nothing was submitted"}

            pub = await _click_button(page, mw.PUBLISH_BUTTON)
            verify["publish_click"] = pub
            if not pub.get("clicked"):
                await page.screenshot(path=_shot("mw_no_publish.png"), full_page=True)
                return {"success": True, "url": page.url, "draft_id": draft_id,
                        "verify": verify,
                        "error": "", "message": "The MakerWorld form is fully filled but the "
                                 "Publish control was not found — press it in the browser "
                                 "(see downloads/_shots/mw_no_publish.png)."}
            _mark("publish-pressed")

            # 8) Wait for MakerWorld's own answer: the draft URL leaving
            #    /drafts/, a design id appearing, or the form's error text.
            outcome, reason, final = "", "", {}
            for _ in range(120):
                await page.wait_for_timeout(500)
                st = await page.evaluate(FORM_STATE_JS)
                final = st
                u = st.get("url") or page.url
                if st.get("designId") or "/en/models/" in u:
                    outcome = "success"
                    break
                text = " ".join([st.get("body") or ""] + [e or "" for e in st.get("errors") or []])
                if DUPLICATE_RE.search(text):
                    outcome, reason = "duplicate", DUPLICATE_RE.search(text).group(0)
                    break
                errs = [e for e in (st.get("errors") or []) if ERROR_RE.search(e or "")]
                if errs:
                    outcome, reason = "error", "; ".join(errs[:3])
                    break
            if outcome == "success":
                did = (final.get("designId") or mw.design_id_from_url(final.get("url") or ""))
                try:
                    await page.close()
                except Exception:
                    pass
                return {"success": True, "url": final.get("url") or page.url,
                        "design_id": did, "draft_id": draft_id, "verify": verify,
                        "message": "Published to MakerWorld as design "
                                   f"{did or '(unknown id)'}. Check your models list."}
            if outcome == "duplicate":
                return {"success": False, "url": final.get("url") or page.url,
                        "draft_id": draft_id, "duplicate": True, "verify": verify,
                        "error": f"MakerWorld says this model already exists ({reason})."}
            await page.screenshot(path=_shot("mw_after_submit.png"), full_page=True)
            return {"success": False, "url": final.get("url") or page.url,
                    "draft_id": draft_id, "verify": verify,
                    "error": (f"Publish was pressed but MakerWorld did not finish "
                              f"({reason or 'no design id in the URL'}). "
                              "Its own field errors: "
                              + json.dumps((final.get("errors") or [])[:6], ensure_ascii=False)[:400]
                              + " The draft is still open — see downloads/_shots/mw_after_submit.png")}
        except Exception as exc:  # noqa: BLE001
            try:
                await page.screenshot(path=_shot("mw_error.png"), full_page=True)
            except Exception:
                pass
            return {"success": False, "url": page.url, "verify": verify,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:
                pass


def study_snapshot() -> dict:
    """What evidence this module is written against — printed by the offline
    checker so nobody can claim the flow was measured without the dumps."""
    if not os.path.isdir(STUDY_DIR):
        return {"dir": STUDY_DIR, "files": []}
    return {"dir": STUDY_DIR,
            "files": sorted(f for f in os.listdir(STUDY_DIR) if f.endswith(".json"))}
