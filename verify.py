"""What the page must SAY after a step -- read back in one call.

WHY THIS EXISTS
---------------
The pipeline advanced from one step to the next on the assumption that the last
one worked. That is why the same code published perfectly on one run and did the
wrong thing on the next: nothing ever read the page back and disagreed.

The evidence is in the project's own run logs, not in theory:
`artifacts/auto_run_20261010-143814.json` has three models refused with "the
publish form never reached step 2, so the model's source cannot be declared" --
a Next click that silently did nothing, discovered only later and reported as a
different failure.

So each step gets a post-condition read from `page_model` (one round-trip), and a
step that fails its post-condition is retried a bounded number of times before it
is reported as its own reason instead of continuing blind.

HONESTY RULE
------------
A check can only be believed when the page could actually be read. When there is
no page model, every check returns `checked=False` and `ok=True`: that means "not
verified", never "verified good". Nothing here makes a run fail on the absence of
its own instrumentation -- the existing probing stays as the fallback.
"""
from __future__ import annotations

import asyncio
from typing import Callable, NamedTuple

import page_model

# The step-2 fields, by the placeholders the live form is known to use
# (UPLOAD-FLOW-CONFIG.md, checked against the live inspector).
STEP2_MARKERS = ("model name", "paste the url")

# The licence rows the discovery filter ticks, as their labels read on the page.
LICENCE_LABELS = ("CC BY", "CC BY-SA", "CC0")


class Check(NamedTuple):
    ok: bool
    reason: str
    checked: bool = True

    def __bool__(self) -> bool:      # `if check:` reads as "the step is good"
        return bool(self.ok)


def unverified(why: str = "the page could not be read in one call") -> Check:
    """Not checked is not the same as good -- say which one it is."""
    return Check(True, "not verified (%s)" % why, checked=False)


def _blob(control: dict) -> str:
    return " ".join(str(control.get(k) or "") for k in
                    ("label", "id", "name", "cls", "type", "text")).lower()


def step_two_open(model: dict | None) -> Check:
    """Is the step-2 form actually laid out, or still the hidden step 1?

    This is the exact failure recorded against three models on 2026-10-10: the
    form stayed on step 1 (its step-2 fields exist in the DOM but have no layout
    box), so the run later refused for a reason that did not name the real cause.
    """
    if not model:
        return unverified()
    laid_out = [c for c in model.get("controls") or []
                if c.get("visible") and c.get("tag") in ("input", "textarea")]
    for control in laid_out:
        if any(marker in _blob(control) for marker in STEP2_MARKERS):
            return Check(True, "step 2 is laid out")
    # are they present but hidden? that is a different (and more informative) story
    hidden = [c for c in model.get("controls") or [] if not c.get("visible")
              and any(m in _blob(c) for m in STEP2_MARKERS)]
    if hidden:
        return Check(False, "the step-2 fields are in the page but not laid out "
                            "(the form is still on step 1)")
    return Check(False, "no step-2 field is laid out on the page")


def name_applied(model: dict | None, wanted: str) -> Check:
    """Does the name field actually hold the name we set?"""
    if not model:
        return unverified()
    if not wanted:
        return Check(True, "no name was set", checked=False)
    values = [str(c.get("value") or "") for c in (model.get("controls") or [])
              if c.get("tag") == "input" and c.get("visible")]
    if any(v.strip() == wanted.strip() for v in values):
        return Check(True, "the name field holds the name that was set")
    if any(wanted.strip() and wanted.strip() in v for v in values):
        return Check(True, "the name field holds the name that was set (shortened)")
    return Check(False, "the name field does not hold the name that was set "
                        "(wanted %r)" % wanted[:60])


def licence_rows_ticked(model: dict | None,
                        wanted: tuple = LICENCE_LABELS) -> Check:
    """Are all the allow-list rows ticked? A row that silently fell unticked is
    the failure mode where a whole run harvests models it is not allowed to
    re-publish."""
    if not model:
        return unverified()
    ticked, missing = [], []
    for label in wanted:
        rows = [c for c in (model.get("controls") or [])
                if c.get("tag") == "input" and label.lower() in _blob(c)]
        if rows and any(c.get("checked") for c in rows):
            ticked.append(label)
        elif rows:
            missing.append(label)
    if not ticked and not missing:
        return Check(True, "no licence rows were found on the page", checked=False)
    if missing:
        return Check(False, "these licence rows are NOT ticked: %s"
                            % ", ".join(missing))
    return Check(True, "licence rows ticked: %s" % ", ".join(ticked))


def covers_present(model: dict | None, expected: int) -> Check:
    """Are the cover photos actually on the form? A slot that stayed empty
    silently blocks the submit, or publishes a listing with no photos."""
    if not model:
        return unverified()
    loaded = [i for i in (model.get("images") or [])
              if i.get("visible") and (i.get("src") or "")]
    if expected and len(loaded) < expected:
        return Check(False, "only %d of %d cover image(s) are on the form"
                            % (len(loaded), expected))
    return Check(True, "%d cover image(s) on the form" % len(loaded))


def consent_ticked(model: dict | None) -> Check:
    """The copyright-consent checkbox -- the one Creality's toast complains about.

    Matched by the SAME phrase the uploader's own reader uses ("I have read and
    agree"), not by "any visible checkbox": the licence rows are checkboxes too,
    and counting one of those as consent would report success for the exact box
    whose absence makes Creality refuse the submit.
    """
    if not model:
        return unverified()
    boxes = [c for c in (model.get("controls") or [])
             if c.get("tag") == "input" and (c.get("type") or "").lower() == "checkbox"
             and c.get("visible")]
    consent = [c for c in boxes if CONSENT_PHRASE in _blob(c)]
    if not consent:
        if boxes:
            return Check(True, "no consent-style checkbox was found on the page",
                         checked=False)
        return Check(True, "no visible checkbox was found", checked=False)
    if any(c.get("checked") for c in consent):
        return Check(True, "the consent checkbox is ticked")
    return Check(False, "the copyright consent checkbox is NOT ticked")


# The phrase the live form uses, and what the uploader's own reader matches.
CONSENT_PHRASE = "i have read and agree"


async def observe(page) -> dict | None:
    """ONE read of the whole page (or None when it cannot be read)."""
    return await page_model.read_page(page)


async def retry(label: str, act: Callable, check: Callable,
                page, attempts: int = 3, delay_s: float = 0.7):
    """Do the action, read the page back, and retry while the check fails.

    `act` and `check` are separate on purpose: the action is real input (a click,
    a fill) so React handles it like a user's, and the check is a read. Returns
    `(Check, tries)`; the caller decides whether the last failure ends the model.
    """
    last = Check(False, "never ran")
    for tries in range(1, max(1, attempts) + 1):
        try:
            result = act()
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            last = Check(False, "%s: %s" % (label, str(e)[:160]))
            await asyncio.sleep(delay_s)
            continue
        model = await observe(page)
        last = check(model)
        if not last.checked or last.ok:
            return last, tries
        if tries < attempts:
            await asyncio.sleep(delay_s)
    return last, max(1, attempts)


def record(checks: dict) -> dict:
    """Flatten checks into the honest shape a run log wants."""
    out = {}
    for name, c in checks.items():
        if c is None:
            continue
        out[name] = {"ok": bool(c.ok), "checked": bool(c.checked),
                     "reason": c.reason}
    return out


def summarise(checks: dict) -> str:
    """One line: what failed, or what could not be checked at all."""
    bad = [n for n, c in checks.items() if c is not None and c.checked and not c.ok]
    if bad:
        return "step checks FAILED: " + "; ".join(
            "%s — %s" % (n, checks[n].reason) for n in bad)
    un = [n for n, c in checks.items() if c is not None and not c.checked]
    if un:
        return "step checks passed or unverified (not readable: %s)" % ", ".join(un)
    return "step checks passed"
