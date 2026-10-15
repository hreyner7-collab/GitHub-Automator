"""Read a whole page in ONE call, instead of probing it element by element.

WHY THIS EXISTS
---------------
The pipeline found elements and checked its work with many small round-trips
(`locator.count()`, `is_visible()`, one `evaluate` per question, a scroll and a
fixed sleep per pass). Each of those is a separate round-trip to the browser, so
cost grew with the size of the page, and the checks were scattered enough that a
step could "succeed" without anything ever confirming it.

The DevTools protocol answers the whole question at once:

  * `DOMSnapshot.captureSnapshot` returns the entire DOM tree -- including nodes
    that are hidden or have no layout box, which is exactly the case for a
    multi-step upload form whose step 2 exists in the DOM but is 0x0 until a file
    is attached -- together with the live form state (`inputValue`,
    `inputChecked`, `optionSelected`) and, when asked, layout rectangles.
  * a single `Runtime.evaluate` returning a tailored projection is the leaner
    fallback, and the parser below understands both.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not CLICK anything, type anything, or advance a wizard. Those APIs
inspect the page's current state; they cannot make a hidden step appear, and
driving a genuinely hidden control is brittle and can be ignored by React. So:
READ with this module, INTERACT with real Playwright input. The reading is what
makes a step checkable, and a checkable step is what stops one run working and
the next silently doing the wrong thing.

Nothing here imports Playwright at module level, and the parsers are pure, so
the model can be tested offline from a recorded snapshot.
"""
from __future__ import annotations

import asyncio
import json

# Fields worth keeping per element. Anything not listed is not carried into the
# model: the point is a small, comparable structure, not a second DOM.
CONTROL_TAGS = ("input", "select", "textarea", "button")
TEXT_TAGS = ("label", "span", "div", "p", "a", "h1", "h2", "h3", "strong", "li")

# The one-call fallback: a tailored report of the live form state. Deliberately
# omits geometry reads (getBoundingClientRect) except for a cheap 0x0 check on
# the controls themselves, because geometry forces layout work on the page.
PAGE_MODEL_JS = r"""
(() => {
  const seen = [];
  const pick = (el) => {
    let box = null;
    try {
      const r = el.getBoundingClientRect();
      box = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    } catch (e) { box = null; }
    let label = '';
    if (el.labels && el.labels.length) label = (el.labels[0].innerText || '').trim();
    if (!label && el.getAttribute) {
      label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    }
    return {
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute && el.getAttribute('type')) || '',
      id: el.id || '',
      name: (el.getAttribute && el.getAttribute('name')) || '',
      cls: (el.getAttribute && el.getAttribute('class')) || '',
      label: String(label).slice(0, 160),
      value: ('value' in el) ? String(el.value == null ? '' : el.value).slice(0, 200) : '',
      checked: ('checked' in el) ? !!el.checked : null,
      selected: (el.tagName === 'SELECT') ? String(el.value || '') : null,
      disabled: !!el.disabled,
      required: !!el.required,
      text: String(el.innerText || '').trim().slice(0, 160),
      box: box,
      visible: !!(box && box[2] > 0 && box[3] > 0)
    };
  };
  document.querySelectorAll('input, select, textarea, button').forEach(el => seen.push(pick(el)));
  const images = Array.from(document.images || []).map(img => {
    let box = null;
    try {
      const r = img.getBoundingClientRect();
      box = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    } catch (e) { box = null; }
    return {
      src: img.currentSrc || img.src || '',
      alt: img.getAttribute('alt') || '',
      cls: img.getAttribute('class') || '',
      natural: [img.naturalWidth || 0, img.naturalHeight || 0],
      complete: !!img.complete,
      box: box,
      visible: !!(box && box[2] > 0 && box[3] > 0)
    };
  });
  return {
    source: 'evaluate',
    url: location.href,
    title: document.title || '',
    ready: document.readyState || '',
    controls: seen,
    images: images,
    texts: Array.from(document.querySelectorAll('label, legend, h2, h3, .title, [class*="title"]'))
      .slice(0, 400)
      .map(el => String(el.innerText || '').trim().slice(0, 160))
      .filter(Boolean)
  };
})()
"""


# ── attribute helpers ────────────────────────────────────────────────────────
def _string(strings: list, index) -> str:
    """DOMSnapshot stores strings as indexes into one shared table."""
    if isinstance(index, str):
        return index
    if isinstance(index, int) and 0 <= index < len(strings):
        return strings[index] or ""
    return ""


def _attr_pairs(pairs: list, strings: list) -> dict:
    """`attributes` is a flat [name, value, name, value, ...] list of indexes."""
    out = {}
    pairs = pairs or []
    for i in range(0, len(pairs) - 1, 2):
        out[_string(strings, pairs[i])] = _string(strings, pairs[i + 1])
    return out


def _bounds_of(layout: dict, node_index: int):
    """The layout rectangle for a DOM node index, or None when it has none.

    A node with no layout object (for example inside `display:none`) simply has
    no entry: that absence is the honest answer to "is this laid out?", and must
    not be confused with "this element does not exist" -- it is in the DOM.
    """
    try:
        idx = (layout.get("nodeIndex") or []).index(node_index)
    except (ValueError, AttributeError):
        return None
    rects = layout.get("bounds") or []
    if idx >= len(rects):
        return None
    rect = rects[idx]
    if not rect or len(rect) < 4:
        return None
    return [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])]


def parse_snapshot(snapshot: dict) -> dict:
    """Flatten a `DOMSnapshot.captureSnapshot` result into the same model shape.

    Tolerant by design: the protocol has grown fields over time (`textValue` is
    newer than `inputValue`), so every section is read with `.get()` and a
    missing one degrades to empty rather than raising.
    """
    snapshot = snapshot or {}
    strings = snapshot.get("strings") or []
    model = {"source": "snapshot", "url": "", "title": "", "ready": "",
             "controls": [], "images": [], "texts": []}
    for doc in (snapshot.get("documents") or []):
        nodes = doc.get("nodes") or {}
        layout = doc.get("layout") or {}
        names = nodes.get("nodeName") or []
        attrs = nodes.get("attributes") or []
        values = nodes.get("nodeValue") or []
        input_values = nodes.get("inputValue") or []
        input_checked = nodes.get("inputChecked") or []
        option_selected = nodes.get("optionSelected") or []
        text_values = nodes.get("textValue") or []
        child_count = len(names)
        for i in range(child_count):
            tag = _string(strings, names[i] if i < len(names) else "").lower()
            if not tag:
                continue
            pairs = _attr_pairs(attrs[i] if i < len(attrs) else [], strings)
            box = _bounds_of(layout, i)
            visible = bool(box and box[2] > 0 and box[3] > 0)
            if tag in CONTROL_TAGS:
                value = _string(strings, input_values[i]) if i < len(input_values) else ""
                if not value and i < len(text_values):
                    value = _string(strings, text_values[i])
                checked = bool(input_checked[i]) if i < len(input_checked) else False
                selected = bool(option_selected[i]) if i < len(option_selected) else False
                model["controls"].append({
                    "tag": tag, "type": pairs.get("type", ""),
                    "id": pairs.get("id", ""), "name": pairs.get("name", ""),
                    "cls": pairs.get("class", ""),
                    "label": (pairs.get("aria-label") or pairs.get("placeholder") or ""),
                    "value": value, "checked": checked if tag == "input" else None,
                    "selected": selected if tag == "option" else None,
                    "disabled": "disabled" in pairs, "required": "required" in pairs,
                    "text": "", "box": box, "visible": visible,
                })
            elif tag == "img":
                model["images"].append({
                    "src": pairs.get("src", ""), "alt": pairs.get("alt", ""),
                    "cls": pairs.get("class", ""), "natural": [0, 0], "complete": False,
                    "box": box, "visible": visible,
                })
            elif tag in TEXT_TAGS:
                text = _string(strings, values[i] if i < len(values) else "")
                if text and text.strip():
                    model["texts"].append(text.strip()[:160])
    model["counts"] = _counts(model)
    return model


def _counts(model: dict) -> dict:
    controls = model.get("controls") or []
    images = model.get("images") or []
    return {
        "controls": len(controls),
        "hidden_controls": sum(1 for c in controls if not c.get("visible")),
        "images": len(images),
        "hidden_images": sum(1 for i in images if not i.get("visible")),
        "texts": len(model.get("texts") or []),
    }


def normalize_report(report: dict) -> dict:
    """Finish a `PAGE_MODEL_JS` report into the model shape (pure)."""
    report = report or {}
    model = {
        "source": "evaluate",
        "url": report.get("url") or "",
        "title": report.get("title") or "",
        "ready": report.get("ready") or "",
        "controls": [dict(c) for c in (report.get("controls") or [])],
        "images": [dict(i) for i in (report.get("images") or [])],
        "texts": [str(t) for t in (report.get("texts") or [])],
    }
    for c in model["controls"]:
        box = c.get("box")
        c["visible"] = bool(c.get("visible")) if "visible" in c else bool(
            box and box[2] > 0 and box[3] > 0)
    for i in model["images"]:
        box = i.get("box")
        i["visible"] = bool(i.get("visible")) if "visible" in i else bool(
            box and box[2] > 0 and box[3] > 0)
    model["counts"] = _counts(model)
    return model


# ── reading it live ──────────────────────────────────────────────────────────
async def read_page(page, timeout_s: float = 25.0) -> dict | None:
    """One call for the whole page, or None if neither route worked.

    None is a real answer, not a hidden failure: the caller keeps its existing
    probing as a fallback, so adding this cannot make a run worse than it was.
    """
    snapshot = await _try_snapshot(page, timeout_s)
    if snapshot is not None:
        try:
            model = parse_snapshot(snapshot)
            if model.get("controls") or model.get("images") or model.get("texts"):
                return model
        except Exception:
            pass
    return await _try_evaluate(page, timeout_s)


async def _try_snapshot(page, timeout_s: float) -> dict | None:
    session = None
    try:
        context = getattr(page, "context", None)
        if context is None or not hasattr(context, "new_cdp_session"):
            return None
        session = await context.new_cdp_session(page)
        return await asyncio.wait_for(
            session.send("DOMSnapshot.captureSnapshot", {
                # Ask only for what the model uses. A longer whitelist costs time
                # on every node and buys nothing here.
                "computedStyles": ["display", "visibility"],
                "includeDOMRects": False,
                "includePaintOrder": False,
            }), timeout=timeout_s)
    except Exception:
        return None
    finally:
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass


async def _try_evaluate(page, timeout_s: float) -> dict | None:
    try:
        report = await asyncio.wait_for(page.evaluate(PAGE_MODEL_JS), timeout=timeout_s)
        if isinstance(report, str):
            report = json.loads(report or "{}")
        return normalize_report(report)
    except Exception:
        return None


def round_trips(model: dict | None) -> int:
    """How many browser round-trips this model cost (1), or 0 for no model."""
    return 1 if model else 0


def summary(model: dict | None) -> str:
    """A one-line description for logs: what was seen, and how much of it hides."""
    if not model:
        return "no page model (the page could not be read in one call)"
    c = model.get("counts") or {}
    return ("%s: %d controls (%d hidden), %d images (%d hidden), %d texts"
            % (model.get("source"), c.get("controls", 0), c.get("hidden_controls", 0),
               c.get("images", 0), c.get("hidden_images", 0), c.get("texts", 0)))
