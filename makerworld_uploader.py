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
    window the user logged MakerWorld into.

    Detached on purpose: launched as a child of the server it died whenever
    the server or the launching shell exited, and the next run then failed
    with 'connect ECONNREFUSED 127.0.0.1:9222' (measured 2026-10-25)."""
    if _debug_port_up():
        return True
    exe = next((p for p in BROWSER_PATHS if os.path.exists(p)), None)
    if not exe:
        return False
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = (subprocess.DETACHED_PROCESS
                                  | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        flags["start_new_session"] = True
    subprocess.Popen(
        [exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check",
         "--disable-session-crashed-bubble", mw.ORIGIN + "/en/"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True, **flags)
    for _ in range(80):
        if _debug_port_up():
            return True
        time.sleep(0.25)
    return _debug_port_up()


# ── DOM helpers ─────────────────────────────────────────────────────────────
# Every control is found the same way: locate the MEASURED text that owns it,
# then take the first matching element after it in document order. That is how
# two identical "Add Photo" inputs are told apart, and how a licence option is
# picked from its own question rather than the first "Yes" on the page.
# Photo slots are addressed by the CONTAINER the live form gives each one, not
# by the nearest heading. Measured 2026-10-25: the heading heuristic mapped
# "Model Pictures" onto the 4:3 cover input (because "Model Pictures & Videos"
# starts with "Model Pictures"), so the gallery was attached to the cover slot
# and input 6 never received a file. These roots were read off the live draft.
SLOT_INDEX_JS = r"""
(marker) => {
  const roots = [
    ["4:3 Cover", [".dropzone:not(.appUpload)"]],
    ["3:4 Cover is prioritized in App", [".dropzone.appUpload"]],
    ["Real life photos", [".modelRealPicture"]],
    ["Model Pictures", [".js-scroll-designPictures"]],
  ];
  const inputs = [...document.querySelectorAll('input[type=file]')];
  for (const [name, sels] of roots) {
    if (!marker.startsWith(name)) continue;
    for (const sel of sels) {
      const root = document.querySelector(sel);
      const inp = root ? root.querySelector('input[type=file]') : null;
      const index = inputs.indexOf(inp);
      if (index >= 0) return { index, how: 'container:' + sel, marker };
    }
    return { index: -1, how: 'container-without-input', marker };
  }
  return { index: -1, how: 'unknown-slot', marker };
}
"""

# React needs the native value setter before the input event, or its state
# never sees the text (same finding as the Creality remix box).
SET_VALUE_JS = r"""
(el, args) => {
  const text = (args && args.text) || '';
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

# The licence MakerWorld renders for the answers given, read from the form's own
# CC link first. Measured 2026-10-25: the live draft renders
# "Creative Commons Attribution-Share Alike" (the long name), while the question
# this used to anchor on ("Bambu Handy") no longer exists — so the block is
# anchored at the first licence question and the link's own /licenses/by-sa/
# path is the strongest reading. The Model Origin warning names other licences
# as examples ("CC-BY-ND, CC-BY-NC-ND … are not allowed"), so this never scans
# the whole page.
LICENSE_READ_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('body *')];
  const idx = (txt) => nodes.findIndex(e =>
    !e.childElementCount && norm(e.textContent).startsWith(txt));
  let a = idx('Allow adaptations');
  if (a < 0) a = idx('License');
  if (a < 0) return '';
  const b = idx('Visibility');
  const stop = b > a ? b : nodes.length;
  for (let i = a; i < stop; i++) {
    const e = nodes[i];
    if (!e.matches || !e.matches('a[href*="creativecommons.org"]')) continue;
    const href = e.getAttribute('href') || '';
    const m = href.match(/creativecommons\.org\/(?:licenses|publicdomain)\/([a-z0-9\-]+)/i);
    if (m) {
      const code = m[1].split('/')[0];
      if (/^(zero|publicdomain|mark)$/i.test(code)) return 'CC0';
      return 'CC ' + code.toUpperCase();
    }
    const t = norm(e.innerText);
    if (t) return t.slice(0, 60);
  }
  for (let i = a; i < stop; i++) {
    const t = norm(nodes[i].textContent || nodes[i].innerText);
    if (/^(CC[\s-]?0|CC[\s-]?BY(-[\s-]?(NC|SA|ND)){0,2}|Standard Digital File License)/i.test(t))
      return t.slice(0, 60);
  }
  return '';
}
"""

# Covers open a stack of MUI crop dialogs, each with its own Submit. The button
# is clicked by its coordinates because MUI's own overlay makes the first
# locator fail with "subtree intercepts pointer events" (measured 2026-10-25).
CROP_DIALOG_JS = r"""
() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const dialogs = [...document.querySelectorAll('.MuiDialog-root')].filter(vis);
  if (!dialogs.length) return null;
  const d = dialogs[dialogs.length - 1];
  const b = [...d.querySelectorAll('button')].filter(vis)
    .find(e => (e.innerText || '').replace(/\s+/g, ' ').trim() === 'Submit');
  if (!b) return null;
  b.scrollIntoView({block: 'center'});
  const r = b.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2,
          text: (d.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 100)};
}
"""

# Category is a MUI autocomplete; only a real click on one of its rendered
# options selects a category (measured 2026-10-25).
CATEGORY_OPTIONS_JS = r"""
() => [...document.querySelectorAll('li.MuiAutocomplete-option')]
  .map(e => { const r = e.getBoundingClientRect();
              return {text: (e.innerText || '').replace(/\s+/g, ' ').trim(),
                      x: r.x + r.width / 2, y: r.y + r.height / 2,
                      w: r.width, h: r.height}; })
  .filter(o => o.h > 0 && o.text)
"""

CATEGORY_STATE_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                                 .replace(/\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('body *')];
  const idx = (txt) => nodes.findIndex(e => !e.childElementCount &&
    norm(e.textContent).startsWith(txt));
  const a = idx('Category');
  if (a < 0) return '';
  const b = idx('Tags');
  return nodes.slice(a, b > a ? b : a + 12)
    .map(e => norm(e.textContent)).filter(t => t && t.length < 120)
    .join(' | ').slice(0, 400);
}
"""

# The Category control, found the way the live form actually is (2026-10-25):
# a MUI autocomplete whose <input> carries NO placeholder — 'Quick search by
# entering category keywords' is its floating label. Matching on
# input[placeholder*=…] found nothing and stopped every run here, which is what
# looked like a hang after Tags. Fall back to document order between the
# 'Category' and 'Tags' headings, which is stable across renders.
CATEGORY_BOX_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/[\u200b\u200c\u200d\ufeff]/g, '')
                                 .replace(/\s+/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const usable = [...document.querySelectorAll('input')].filter(i =>
    !i.disabled && !i.readOnly && vis(i) &&
    (!i.type || i.type === 'text' || i.type === 'search'));
  const byPh = usable.find(i => /category keywords|quick search/i.test(i.placeholder || ''));
  if (byPh) return byPh;
  const lab = [...document.querySelectorAll('label, .MuiFormLabel-root, legend')]
    .find(l => /category keywords/i.test(norm(l.innerText || l.textContent)));
  if (lab) {
    const root = lab.closest('.MuiFormControl-root') || lab.parentElement;
    const input = root ? root.querySelector('input') : null;
    if (input && usable.includes(input)) return input;
  }
  const nodes = [...document.querySelectorAll('body *')];
  let start = -1, stop = -1;
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].childElementCount) continue;
    const t = norm(nodes[i].textContent);
    if (start < 0 && /^Category\b/.test(t)) { start = i; continue; }
    if (start >= 0 && /^Tags\b/.test(t)) { stop = i; break; }
  }
  if (start >= 0) {
    for (const i of usable) {
      const p = nodes.indexOf(i);
      if (p > start && (stop < 0 || p < stop)) return i;
    }
  }
  return null;
}
"""

# The measured modal between Publish and the result: "Publish your model" with
# three sync choices and a Confirm button (2026-10-25).
PUBLISH_DIALOG_JS = r"""
(args) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const dialogs = [...document.querySelectorAll('.MuiDialog-root')].filter(vis);
  const d = dialogs.find(e => norm(e.innerText).includes(args.title));
  if (!d) return null;
  const rect = (e) => { const r = e.getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2}; };
  const labels = [...d.querySelectorAll('label')].filter(vis);
  const pick = labels.find(l => norm(l.innerText).startsWith(args.option));
  const radio = pick ? pick.querySelector('input[type=radio]') : null;
  const confirm = [...d.querySelectorAll('button')].filter(vis)
    .find(e => norm(e.innerText) === args.confirm);
  return {
    title: norm(d.innerText).slice(0, 200),
    options: labels.map(l => norm(l.innerText).slice(0, 100)),
    pick: pick ? rect(pick) : null,
    pick_checked: radio ? !!radio.checked : null,
    confirm: confirm ? rect(confirm) : null,
  };
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

# Measured 2026-10-25 (screen recording + live draft): the origin search must be
# typed through the keyboard; setting the input's value leaves the search
# silent. What appears is MakerWorld's own card — thumbnail, title, author —
# rendered as a plain div under the box, NOT a MUI li[role=option]. So the card
# is found by its picture and position, matched by the source's own words, and
# clicked with a real mouse press at its centre; the block is re-read after.
ORIGIN_CARD_JS = r"""
(args) => {
  // lowercase first: the strip below only keeps [a-z0-9], so running it first
  // deleted every capital and turned 'Mini Bookshelf' into 'ini ookshelf'.
  const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const input = [...document.querySelectorAll('input')]
    .find(e => (e.placeholder || '').includes(args.ph));
  if (!input) return null;
  const r = input.getBoundingClientRect();
  const words = args.words || [];
  let best = null;
  for (const e of document.querySelectorAll('div,li,a')) {
    const b = e.getBoundingClientRect();
    if (b.width < 200 || b.height < 30 || b.height > 220) continue;
    if (b.top < r.bottom - 4 || b.top > r.bottom + 220) continue;
    if (b.left < r.left - 60 || b.right > r.right + 120) continue;
    if (!e.querySelector('img')) continue;
    const t = norm(e.innerText);
    const hits = words.filter(w => t.includes(w)).length;
    if (hits < Math.min(2, words.length)) continue;
    if (!best || b.width * b.height < best.w * best.h)
      best = {x: b.x, y: b.y, w: b.width, h: b.height, hits};
  }
  return best;
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


async def _confirm_crop_dialogs(page, max_submits: int = 4,
                                budget_ms: int = 30_000) -> dict:
    """Submit every crop dialog the cover slots opened, topmost first.

    The two cover inputs each open their own MUI crop dialog and the dialogs
    stack; a locator click on the lower one fails because the upper dialog
    intercepts pointer events. So the top visible dialog's Submit is pressed at
    its coordinates, then the next one becomes topmost.
    """
    clicks: list[dict] = []
    spent = 0
    while spent < budget_ms and len(clicks) < max_submits:
        found = None
        deadline = min(budget_ms, spent + (6_000 if not clicks else 5_000))
        while spent < deadline:
            found = await page.evaluate(CROP_DIALOG_JS)
            if found:
                break
            await page.wait_for_timeout(400)
            spent += 400
        if not found:
            break
        await page.mouse.move(found["x"], found["y"])
        await page.wait_for_timeout(150)
        await page.mouse.click(found["x"], found["y"])
        clicks.append(found)
        for _ in range(25):
            await page.wait_for_timeout(400)
            spent += 400
            still = await page.evaluate(CROP_DIALOG_JS)
            if not still or still.get("text") != found.get("text"):
                break
    remaining = await page.evaluate(CROP_DIALOG_JS)
    return {"ok": remaining is None, "submits": len(clicks),
            "texts": [c.get("text", "") for c in clicks],
            "remaining": bool(remaining)}


async def _pick_category(page, name: str) -> dict:
    """Choose the source's category through MakerWorld's autocomplete.

    Measured 2026-10-25: the field is a MUI autocomplete with the floating
    label "Quick search by entering category keywords" — the <input> itself
    has no placeholder, and only a click on a rendered li option sets the
    value. The box is waited for (the form mounts its sections late), located
    by label or by its place between the Category and Tags headings, and the
    search is retried with the last significant word when the full category
    name yields no options.
    """
    want = re.sub(r"\s+", " ", (name or "").strip())
    if not want:
        return {"ok": True, "skipped": "the source names no category"}
    handle = await page.evaluate_handle(CATEGORY_BOX_JS)
    for _ in range(40):
        if handle.as_element() is not None:
            break
        await page.wait_for_timeout(250)
        handle = await page.evaluate_handle(CATEGORY_BOX_JS)
    el = handle.as_element()
    if el is None:
        state = await page.evaluate(FORM_STATE_JS)
        fields = [f.get("ph") for f in (state.get("textFields") or [])]
        unauthorized = "not authorized" in (state.get("body") or "").lower()
        return {"ok": False,
                "error": ("the draft page says this account is not authorized"
                          if unauthorized else
                          "no Category search box on the draft page")
                         + f"; the form's visible text fields read {fields[:8]}",
                "url": state.get("url")}

    queries = [want]
    words = [w for w in re.split(r"[^A-Za-z0-9]+", want) if len(w) >= 4]
    if len(words) > 1:
        queries.append(words[-1])

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").replace("\u200b", "").strip()).lower()

    def words_of(s: str) -> set:
        return set(re.findall(r"[a-z0-9]+", (s or "").lower()))

    options: list[dict] = []
    picked = None
    used = want
    try:
        await el.scroll_into_view_if_needed()
        for q in queries:
            await el.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(q, delay=10)
            options = []
            for _ in range(25):
                options = await page.evaluate(CATEGORY_OPTIONS_JS)
                if options:
                    break
                await page.wait_for_timeout(200)
            if not options:
                continue
            used = q
            picked = (next((o for o in options if norm(o["text"]) == norm(want)), None)
                      or next((o for o in options if norm(o["text"]) == norm(used)), None))
            if picked is None:
                wn = words_of(want)
                close = [o for o in options if wn and wn <= words_of(o["text"])]
                if len(close) == 1:
                    picked = close[0]
            if picked:
                break
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"the Category box could not be typed into "
                                      f"({type(exc).__name__}: {str(exc)[:120]})"}
    if not options:
        return {"ok": False,
                "error": f"MakerWorld offered no category options for {want!r}",
                "query": used}

    how = "exact" if picked else ""
    if picked is None and len(options) == 1:
        picked, how = options[0], "only option"
    if picked is None:
        picked, how = options[0], "first option"
    await page.mouse.move(picked["x"], picked["y"])
    await page.wait_for_timeout(120)
    await page.mouse.click(picked["x"], picked["y"])
    block = ""
    shown = ""
    want_key = norm(picked["text"])
    for _ in range(20):
        shown, around = "", ""
        try:
            shown = await el.input_value()
        except Exception:  # the re-render can replace the input
            pass
        try:
            around = await el.evaluate(
                "e => { const r = e.closest('.MuiFormControl-root') || e.parentElement;"
                " return r ? (r.innerText || '') : ''; }")
        except Exception:
            pass
        if want_key in norm(shown):
            return {"ok": True, "chosen": picked["text"], "how": how,
                    "query": used, "read_back": norm(shown)[:120]}
        if want_key in norm(around):
            return {"ok": True, "chosen": picked["text"], "how": how,
                    "query": used, "read_back": norm(around)[:120]}
        block = await page.evaluate(CATEGORY_STATE_JS)
        if want_key in norm(block):
            return {"ok": True, "chosen": picked["text"], "how": how,
                    "query": used, "read_back": norm(block)[:120]}
        await page.wait_for_timeout(200)
    return {"ok": False,
            "error": f"{picked['text']!r} was clicked but the Category field does not show it",
            "read_back": (block or "")[:200],
            "options": [o["text"] for o in options][:12]}


async def _answer_publish_dialog(page) -> dict:
    """Answer the "Publish your model" sync modal that sits after Publish.

    Measured 2026-10-25: three choices — Sync all models, Sync only CC license
    models, Do not sync any models — and a Confirm button. The CC-only choice
    is the one that matches a CC-licensed remix. A real mouse press is used
    throughout; the radio is read back before Confirm.
    """
    args = {"title": "Publish your model", "option": "Sync only CC license models",
            "confirm": "Confirm"}
    found = None
    for _ in range(40):
        found = await page.evaluate(PUBLISH_DIALOG_JS, args)
        if found:
            break
        await page.wait_for_timeout(250)
    if not found:
        return {"seen": False}
    out: dict = {"seen": True, "options": found.get("options"),
                 "title": found.get("title", "")}
    if found.get("pick"):
        await page.mouse.move(found["pick"]["x"], found["pick"]["y"])
        await page.wait_for_timeout(150)
        await page.mouse.click(found["pick"]["x"], found["pick"]["y"])
        await page.wait_for_timeout(350)
        after = await page.evaluate(PUBLISH_DIALOG_JS, args)
        out["picked"] = bool(after and after.get("pick_checked"))
        found = after or found
    target = (found or {}).get("confirm")
    if target:
        await page.mouse.move(target["x"], target["y"])
        await page.wait_for_timeout(150)
        await page.mouse.click(target["x"], target["y"])
        out["confirmed"] = True
    else:
        out["confirmed"] = False
        out["why"] = "the sync modal has no visible Confirm button"
    return out


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
    ok = await el.evaluate(SET_VALUE_JS, {"text": text})
    await page.wait_for_timeout(200)
    back = await el.evaluate("e => e.value")
    return {"ok": bool(ok) and (back or "") == text, "read_back": (back or "")[:120]}


async def _type_tags(page, marker: str, tags: list[str]) -> dict:
    """Tags are committed by the Enter key (measured helper text: 'Press "Enter"
    to separate tags'), so they are typed through the keyboard, not set as a
    value, and the form's own counter is read back afterwards.

    Tags are optional on the form, so a timeout here is reported, never raised:
    a dropdown covering the field must not cost the whole listing."""
    handle = await page.evaluate_handle(FIELD_LOCATOR_JS, {"marker": marker, "want_ph": ""})
    el = handle.as_element()
    if el is None:
        return {"ok": False, "error": f"no tag input under {marker!r}", "planned": tags}
    try:
        await el.click(timeout=8_000)
        for t in tags:
            await page.keyboard.type(t[: mw.MAX_TAG_INPUT], delay=5)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(150)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "planned": tags,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    back = await page.evaluate("""
      (m) => { const norm = s => (s||'').replace(/[\\u200b\\u200c\\u200d\\ufeff]/g,'')
                                     .replace(/\\s+/g,' ').trim();
               const el = [...document.querySelectorAll('body *')]
                 .find(e => !e.childElementCount && norm(e.textContent).startsWith(m));
               return el ? norm(el.textContent).slice(0, 60) : ''; }
    """, marker)
    m = re.search(r"\((\d+)\s*/\s*\d+\)", back or "")
    count = int(m.group(1)) if m else -1
    return {"ok": True if count < 0 else count >= 1,
            "planned": tags, "counter": back, "count": count}


async def _click_button(page, text: str, force: bool = False) -> dict:
    try:
        loc = page.locator("button", has_text=text).first
        if await loc.count() == 0:
            return {"clicked": False, "error": f"no button containing {text!r}"}
        await loc.click(timeout=8000, force=force)
        return {"clicked": True}
    except Exception as exc:  # noqa: BLE001
        return {"clicked": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _type_origin_url(page, url: str) -> dict:
    """Type the source link into Model Origin. The site searches on keystrokes,
    so a native value set (which works for plain fields) leaves it silent."""
    box = page.locator("input[placeholder*='Paste the URL']").first
    if await box.count() == 0:
        return {"ok": False, "error": "no Model Origin URL box on the draft page"}
    try:
        await box.scroll_into_view_if_needed()
        await box.click(timeout=8_000)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(url, delay=12)
        await page.wait_for_timeout(250)
        back = await box.input_value()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"the Model Origin box could not be typed into "
                                      f"({type(exc).__name__}: {str(exc)[:140]})"}
    return {"ok": back == url, "read_back": back[:160], "how": "keyboard"}


async def _click_origin_card(page, want_name: str) -> dict:
    """Wait for the source's card (thumbnail + title) under the box, hover it,
    and press it with the mouse — the move the screen recording shows."""
    raw = [re.sub(r"[^a-z0-9]+", " ", w).strip()
           for w in mw._norm_words(want_name).split()]
    words = [w for w in raw if len(w) >= 4] or [w for w in raw if w]
    if not words:
        return {"ok": False, "error": "the source has no name to match a card by"}
    args = {"ph": "Paste the URL", "words": words}
    card = None
    for _ in range(60):
        card = await page.evaluate(ORIGIN_CARD_JS, args)
        if card:
            break
        await page.wait_for_timeout(400)
    if not card:
        return {"ok": False, "error": "no card with the source's picture appeared under "
                                      "the Model Origin box", "words": words}
    cx, cy = card["x"] + card["w"] / 2, card["y"] + card["h"] / 2
    await page.mouse.move(cx, cy)
    await page.wait_for_timeout(250)
    await page.mouse.click(cx, cy)
    return {"ok": True, "card": {k: round(card[k]) for k in ("x", "y", "w", "h")},
            "words": words}


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
            #    branch and the 3D branch is a text control. Attaching queues the
            #    file; Next Step is what creates the draft and moves the URL to
            #    /drafts/<id>/edit.
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

            # Measured 2026-10-25: attaching only queues the file — the site
            # shows its own Cancel while it uploads, then lists the file under
            # Raw Model Files. The draft is created when *Next Step* is pressed,
            # and only then does the URL become /my/models/drafts/<id>/edit.
            listed = await _settle(page, 120_000, 500, lambda: page.evaluate(
                """() => { const t = document.body.innerText || '';
                           const buttons = [...document.querySelectorAll('button')]
                             .map(b => (b.innerText || '').trim());
                           return t.includes('Open Source')
                                  && /\\.(3mf|stl|step|stp)\\b/i.test(t)
                                  && !buttons.includes('Cancel'); }"""))
            verify["file_listed"] = listed
            if not listed:
                body = await page.evaluate("() => (document.body.innerText || '').slice(-600)")
                return {"success": False,
                        "error": "MakerWorld never finished taking the attached file, so no draft "
                                 f"could be created. Page says: {body[:300]}",
                        "verify": verify}
            step1 = await _click_button(page, mw.NEXT_STEP_BUTTON)
            verify["step1_next"] = step1
            if not step1.get("clicked"):
                return {"success": False,
                        "error": "MakerWorld listed the file but its step-1 "
                                 f"'{mw.NEXT_STEP_BUTTON}' control would not press "
                                 f"({step1.get('error') or 'unknown reason'}). Nothing was published.",
                        "verify": verify}

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

            # The draft page carries the whole Model Information form; the
            # wizard Next Step that used to sit here is gone (measured
            # 2026-10-25), so only press one when the form really is absent.
            opened = await _settle(page, 25_000, 500,
                                   lambda: page.evaluate(
                                       "() => !!document.querySelector('[contenteditable=true]')"))
            if not opened and await page.locator("button", has_text=mw.NEXT_STEP_BUTTON).count():
                nxt = await _click_button(page, mw.NEXT_STEP_BUTTON)
                verify["next_step"] = nxt
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
            #    a search box whose placeholder is the string below; typing the
            #    link makes MakerWorld render its own design card (thumbnail,
            #    title, author) under the box. The card is matched by the
            #    source's own words, clicked with a real mouse, and then the
            #    Model Origin block is re-read — never chosen by position (the
            #    lesson from Creality's four failed uploads: a look-alike
            #    registers the wrong source).
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
                typed = await _type_origin_url(page, plan["remix_source_url"])
                origin["typed"] = typed
                if not typed.get("ok"):
                    verify["origin"] = origin
                    return {"success": False, "url": page.url, "draft_id": draft_id,
                            "error": "MakerWorld's Model Origin box did not take "
                                     f"{plan['remix_source_url']} (measured placeholder: "
                                     f"'{mw.SOURCE_INPUT_PLACEHOLDER[:40]}…'). A remix with no "
                                     "registered source is not published.",
                            "verify": verify}

                want_name = remix_source_name or name
                origin["want"] = want_name
                origin["card"] = await _click_origin_card(page, want_name)
                if not origin["card"].get("ok"):
                    verify["origin"] = origin
                    return {"success": False, "url": page.url, "draft_id": draft_id,
                            "error": "MakerWorld never showed the source model's card under the "
                                     f"Model Origin box ({origin['card'].get('error')}), so no "
                                     "source was registered. Nothing was published.",
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

            # 3) Model Name / Category / Tags — in the form's own measured
            #    order (Name, Category, Tags), so no field is filled while the
            #    form has scrolled it out of view. The name is required; tags
            #    are optional but retried once. Every answer is read back.
            named = await _fill_text(page, marker="Model Name", text=plan["name"])
            if not named.get("ok"):
                await page.wait_for_timeout(300)
                named = await _fill_text(page, marker="Model Name", text=plan["name"])
            verify["name"] = named
            if not named.get("ok"):
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": "MakerWorld's Model Name field would not take the source's "
                                 f"name ({named.get('error') or named.get('read_back')}). "
                                 "Nothing was published.",
                        "verify": verify}
            if category:
                cat_res = await _pick_category(page, category)
                if not cat_res.get("ok"):
                    # The picker mounts late on this step — a person filling the
                    # form by hand just opens it again, so do that once before
                    # calling it a failure.
                    await page.wait_for_timeout(700)
                    cat_res = await _pick_category(page, category)
                verify["category"] = cat_res
                if not cat_res.get("ok"):
                    return {"success": False, "url": page.url, "draft_id": draft_id,
                            "error": "MakerWorld's Category picker could not take "
                                     f"{category!r}: {cat_res.get('error')}. Category is "
                                     "required, so nothing was published.",
                            "verify": verify}
            tag_list = mw.plan_tags(tags or [], remix_source_name or name)
            if tag_list:
                tags_res = await _type_tags(page, "Tags", tag_list)
                if not tags_res.get("ok"):
                    await page.wait_for_timeout(300)
                    tags_res = await _type_tags(page, "Tags", tag_list)
                verify["tags"] = tags_res

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
                    await page.wait_for_timeout(400)
            crop = await _confirm_crop_dialogs(page)
            verify["crop_dialogs"] = crop
            if not crop.get("ok"):
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": "The cover crop dialog would not submit, so MakerWorld never "
                                 "accepted the 4:3/3:4 covers. Nothing was published.",
                        "verify": verify}
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
            wrote, desc_err = "", ""
            try:
                ed = page.locator("[contenteditable=true]").first
                await ed.click(timeout=8000)
                for i, part in enumerate(desc.split("\n")):
                    if i:
                        await page.keyboard.press("Enter")
                    if part:
                        await page.keyboard.type(part, delay=2)
                state = await page.evaluate(FORM_STATE_JS)
                wrote = (state.get("editor") or "").strip()
            except Exception as exc:  # noqa: BLE001
                desc_err = str(exc)[:160]
            same = (re.sub(r"\s+", " ", wrote) == re.sub(r"\s+", " ", desc))
            verify["description"] = {"ok": bool(wrote), "chars": len(wrote),
                                     "same_as_source": same, "head": wrote[:120],
                                     "error": desc_err}
            if not wrote:
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "error": "MakerWorld's Description editor did not take the source's "
                                 "own text, and Description is required. Nothing was published.",
                        "verify": verify}

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
                # Not a success. This used to answer success=True with a note to
                # press the button by hand, which let an unattended run count a
                # listing nobody published as published and lock the model.
                return {"success": False, "url": page.url, "draft_id": draft_id,
                        "verify": verify, "filled": True, "submitted": False,
                        "error": "The MakerWorld form is fully filled but the Publish control "
                                 "was not found, so nothing was published "
                                 "(see downloads/_shots/mw_no_publish.png)."}
            _mark("publish-pressed")

            # 8) MakerWorld answers Publish with one more modal — "Publish your
            #    model", three sync choices and Confirm (measured 2026-10-25).
            verify["publish_dialog"] = await _answer_publish_dialog(page)
            _mark("publish-dialog")

            # 9) Wait for MakerWorld's own answer: a design id, the published
            #    toast, the Verifying redirect, or the form's error text.
            outcome, reason, final = "", "", {}
            for _ in range(150):
                await page.wait_for_timeout(500)
                st = await page.evaluate(FORM_STATE_JS)
                final = st
                u = st.get("url") or page.url
                # What the form itself is complaining about, kept apart from the
                # page text: the body carries the description just typed and the
                # source model's own name, and "already exists" found in either
                # is not MakerWorld rejecting the upload.
                said = " ".join(e or "" for e in st.get("errors") or [])
                text = " ".join([st.get("body") or "", said])
                if st.get("designId") or "/en/models/" in u:
                    outcome = "success"
                    break
                if re.search(r"(?i)model published|published\s*&\s*synced", text) \
                        or re.search(r"/@[^/]+/verifying", u):
                    outcome = "success"
                    break
                if DUPLICATE_RE.search(said):
                    outcome, reason = "duplicate", DUPLICATE_RE.search(said).group(0)
                    break
                errs = [e for e in (st.get("errors") or []) if ERROR_RE.search(e or "")]
                if errs:
                    outcome, reason = "error", "; ".join(errs[:3])
                    break
            if outcome == "success":
                final_url = final.get("url") or page.url
                pending = bool(re.search(r"/@[^/]+/verifying", final_url))
                did = (final.get("designId") or mw.design_id_from_url(final_url))
                if not did and pending:
                    did = await page.evaluate(r"""
                      () => { const m = [...document.querySelectorAll('a[href*="/models/"]')]
                        .map(a => a.getAttribute('href') || '')
                        .map(h => h.match(/\/models\/(\d+)/))
                        .find(Boolean);
                        return m ? m[1] : ''; }
                    """)
                verify["final_url"] = final_url
                verify["pending_review"] = pending
                try:
                    await page.close()
                except Exception:
                    pass
                if pending:
                    return {"success": True, "url": final_url, "design_id": did,
                            "draft_id": draft_id, "pending_review": True, "verify": verify,
                            "message": "MakerWorld accepted the submission"
                                       + (f" as design {did}" if did else "")
                                       + " — it is in the Verifying queue for review, "
                                         "not public yet."}
                return {"success": True, "url": final_url,
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
