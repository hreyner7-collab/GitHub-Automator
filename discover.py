"""
Discovery / research panel — lists the most popular models on a chosen site.
INFORMATION ONLY: it scrapes public listing pages and returns name, link,
thumbnail and stats. It does NOT download, edit, or upload anything.
"""
import json
import os
import random
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright

import selector
import timing
# The licence lists have ONE owner (creality_rules) — these names are kept
# because the finder, main.py, the gate scripts and the tests read them from
# here. They ARE that module's lists, not a second copy of them.
from creality_rules import (LICENCE_ALLOW as CREALITY_LICENCE_ALLOW,
                            LICENCE_DENY as CREALITY_LICENCE_DENY,
                            LICENCE_LABELS)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Per-site listing pages. Each site has SEVERAL popular/ranked listings; we pick
# one at random per request and then randomly sample from a large scrolled pool,
# so every click surfaces a different mix instead of the same top models.
SITES = {
    "makerworld": {
        # popularity-ranked listings only (no "newest")
        "urls": [
            "https://makerworld.com/en/3d-models?sort=trending",
            "https://makerworld.com/en/3d-models?sort=popular",
            "https://makerworld.com/en/3d-models?sort=hottest",
        ],
        "link_re": r"/models/(\d+)",
        "link_sel": "a[href*='/models/']",
    },
    "printables": {
        "urls": [
            "https://www.printables.com/model?ordering=-likes",
            "https://www.printables.com/model?ordering=-downloadCount",
            "https://www.printables.com/model?ordering=-rating",
        ],
        "link_re": r"/model/(\d+)",
        "link_sel": "a[href*='/model/']",
    },
    "thingiverse": {
        "urls": [
            "https://www.thingiverse.com/search?sort=popular&type=things",
            "https://www.thingiverse.com/search?sort=makes&type=things",
        ],
        "link_re": r"/thing:(\d+)",
        "link_sel": "a[href*='/thing:']",
    },
}


# ---------------------------------------------------------------- creality --
# Finding Creality Cloud models the user may re-publish. The licence filter is
# the human's own rule, seen on the live listing's Filters panel, and it is
# reproduced exactly here — then READ BACK before anything is harvested, so a
# mis-clicked checkbox can never widen the list into models we may not publish.
CREALITY_LISTING = "https://www.crealitycloud.com/model-category/3d-print-all"
CREALITY_SEARCH = "https://www.crealitycloud.com/search/models?q={q}&search_type=2"

# how many turned-down candidates a discovery call reports back (they are the
# proof the gate ran, and the run log keeps them)
REJECTED_MAX = 40

# One listing scan's wall-clock ceiling. The 20-model run of 2026-10-23 spent 58
# of its 82 minutes here (601 scroll passes), so an unbounded scan is the reason
# a big run looks like it stopped working; it is also the reason a small run can
# wait. Override with AUTO_DISCOVER_DEADLINE_S.
DISCOVER_DEADLINE_S = float(os.environ.get("AUTO_DISCOVER_DEADLINE_S") or 1500)

# The ticked set is creality_rules.LICENCE_ALLOW (imported above): only the
# licences that allow redistribution, remixing AND commercial use. The
# NonCommercial variants are not ticked because this account publishes on a
# platform that rewards uploads (2026-10-09: re-uploads removed with "The model
# did not pass platform review"), and the NoDerivatives ones because a remix
# may not be made from them at all.

# what the filter panel currently says (row-aware: the label text lives next to
# the checkbox, and the checked state sits on the row's control/inner element)
LICENCE_STATE_JS = r"""
() => {
    const labels = %(labels)s;
    const state = {};
    const boxes = {};
    for (const el of document.querySelectorAll('label,div,span,li')) {
        const t = ((el.innerText || '').trim());
        if (labels.indexOf(t) < 0) continue;
        const row = el.closest('label') || el.parentElement || el;
        const inp = row.querySelector('input[type=checkbox]');
        const inner = row.querySelector('.el-checkbox__inner,[class*=check]');
        // Element UI marks the ROW with is-checked; the inner element and the
        // hidden input are read as well, so any of the three proves the tick.
        const on = (!!(inp && inp.checked)) ||
                   /is-checked|checked/.test((row.className || '').toString()) ||
                   /is-checked|checked/.test((inner ? inner.className : '').toString());
        // a row that is off-screen reports a zero box; keep the widest row seen
        const r = row.getBoundingClientRect();
        if (!(t in state) || r.width > (boxes[t] || 0)) {
            state[t] = on;
            boxes[t] = r.width;
        }
    }
    return JSON.stringify(state);
}
""" % {"labels": json.dumps(LICENCE_LABELS)}

# click point of each licence option (page coordinates)
LICENCE_BOXES_JS = r"""
() => {
    const labels = %(labels)s;
    const out = {};
    for (const el of document.querySelectorAll('label,div,span,li')) {
        const t = ((el.innerText || '').trim());
        if (labels.indexOf(t) < 0) continue;
        const row = el.closest('label') || el.parentElement || el;
        const r = row.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        out[t] = {x: Math.round(r.x + 6), y: Math.round(r.y + r.height / 2),
                  w: Math.round(r.width), h: Math.round(r.height)};
    }
    return JSON.stringify(out);
}
""" % {"labels": json.dumps(LICENCE_LABELS)}

# the model cards, in the order the listing shows them. Several name sources
# are returned because a card's anchor text is sometimes a placeholder ("GIF")
# or empty; the Python side picks the first sane one.
CREALITY_CARDS_JS = r"""
() => {
    const seen = new Map();
    for (const a of document.querySelectorAll('a[href*="/model-detail/"]')) {
        // a.href keeps the page's own locale (/fr/…) — normalise to the bare
        // site URL so a collected model is never refetched through the French
        // rendering the (English URL normalisation already does the same for
        // the pages themselves)
        const url = (a.href || '').split('?')[0]
            .replace(/(crealitycloud\.com)\/[a-z]{2}(?=\/model-detail)/i, '$1');
        if (seen.has(url)) continue;
        const img = a.querySelector('img');
        seen.set(url, {
            name: (a.innerText || '').trim().split('\n')[0].trim().slice(0, 90),
            alt: ((img && (img.alt || '')) || '').trim().slice(0, 90),
            title: (a.getAttribute('title') || a.getAttribute('aria-label') || '')
                        .trim().slice(0, 90),
        });
    }
    return JSON.stringify([...seen.entries()].map(([url, o]) => ({url: url, ...o})));
}
"""

# a card's anchor text is useless when it is empty, an image badge, a bare
# number, or a filename-ish string
_JUNK_NAME = re.compile(r"^(gif|png|jpg|jpeg|webp|img|image|photo|\s*\d+\s*)$", re.I)


def clean_card_name(*cands: str) -> str:
    """First usable model name out of the card's candidates, else ''."""
    for c in cands:
        c = (c or "").strip()
        if not c or _JUNK_NAME.match(c):
            continue
        if len(c) > 1 and not any(ch.isalnum() for ch in c):
            continue
        return c[:70]
    return ""


FILTER_CONTROL_JS = '''() => {
    const el = document.querySelector('.models-filter-control');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {text: (el.innerText || '').trim().slice(0, 30),
            x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height)};
}'''


def _open_filters(page) -> bool:
    """Open the listing's filter panel. Returns True when it is open.

    The control is found by its own class first — the site renders the UI in
    whatever language the account picked (English 'Filters', French
    'Filtres', …), and a text-only find silently refuses to open on the
    wrong one, which used to stop every run with 'the Filters panel did not
    open'."""
    control = None
    try:
        control = json.loads(page.evaluate(FILTER_CONTROL_JS) or "null")
    except Exception:
        control = None
    if control and control.get("w", 0) > 2:
        try:
            page.mouse.click(control["x"] + control["w"] / 2,
                             control["y"] + control["h"] / 2)
            page.wait_for_timeout(2200)
            return True
        except Exception:
            pass
    for text in ("Filters", "Filter", "Filtres", "Filtre", "Filtros",
                 "Filtri", "\u7b5b\u9009", "\u0424\u0438\u043b\u044c\u0442\u0440"):
        try:
            btn = page.get_by_text(text, exact=False).first
            if btn.count():
                btn.click(timeout=5000)
                page.wait_for_timeout(2200)
                return True
        except Exception:
            continue
    return False


# the filter panel's own scrolling body (div.models-filter-control-body): the
# License rows sit below its visible window, so it has to be scrolled before
# anything can be clicked there.
MARK_SCROLLER_JS = r"""
() => {
    const els = [...document.querySelectorAll('*')].filter(e => {
        const c = (e.className || '').toString();
        return /filter|drawer|panel|body/i.test(c) &&
               e.scrollHeight > e.clientHeight + 40;
    });
    const el = els.find(e => /CC BY|Standard License/.test(e.innerText || '')) || els[0];
    if (!el) { return null; }
    el.setAttribute('data-lic-scroller', '1');
    return {cls: (el.className || '').toString().slice(0, 70),
            h: el.clientHeight, sh: el.scrollHeight, top: el.scrollTop};
}
"""


def _reveal_licences(page, tries: int = 14, should_stop=None) -> dict:
    """Scroll the filter panel until every licence row is inside the viewport,
    and return their click points. A row that is off-screen cannot be clicked,
    which is exactly how a filter ends up silently unticked."""
    try:
        page.evaluate(MARK_SCROLLER_JS)
    except Exception:
        pass
    view_h = page.evaluate("() => window.innerHeight") or 900
    boxes: dict = {}
    for _ in range(tries):
        if should_stop and should_stop():
            break
        try:
            boxes = json.loads(page.evaluate(LICENCE_BOXES_JS) or "{}")
        except Exception:
            boxes = {}
        if len(boxes) >= 6 and all(10 < b["y"] < view_h - 30 and b["w"] > 2
                                   for b in boxes.values()):
            return boxes
        try:
            page.evaluate("""() => {
                const el = document.querySelector('[data-lic-scroller]');
                if (el) { el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + 220); }
                else { window.scrollBy(0, 220); }
            }""")
        except Exception:
            pass
        page.wait_for_timeout(350)
    return boxes


def _wait_for_more_cards(page, before: int, budget_ms: int = 2500,
                         step_ms: int = 150, should_stop=None) -> int:
    """Wait until the listing GROWS, instead of always sleeping 2.5s and asking.

    The listing lazy-loads as it is scrolled, so it does have to be scrolled --
    no amount of reading can make the browser fetch the next batch. What was
    wasteful was the FIXED wait: every pass slept the full 2500ms and only then
    counted the cards, so a page that had already finished loading still cost
    2.5s, and the listing is walked up to 200 times per keyword.

    This returns the moment the count grows, and gives up after the same budget
    as before, so the worst case is identical while the common case (the page
    responds in a few hundred ms, or is already at the end) is far cheaper.
    It also notices Stop, which the fixed sleep could not.
    """
    def count() -> int:
        try:
            return len(json.loads(page.evaluate(CREALITY_CARDS_JS) or "[]"))
        except Exception:
            return before          # a read that fails is not a reason to spin

    if should_stop and should_stop():
        return before
    # One early probe catches the common case: the next batch was already in
    # flight and lands in a few hundred ms. If it has not grown by then, wait the
    # rest of the old budget and look once more -- so the WORST case is exactly
    # the old behaviour (one 2.5s wait and one read), never more round-trips.
    page.wait_for_timeout(step_ms)
    after = count()
    if after > before:
        return after
    remaining = budget_ms - step_ms
    while remaining > 0:
        if should_stop and should_stop():
            return after
        chunk = min(step_ms, remaining)
        page.wait_for_timeout(chunk)
        remaining -= chunk
    return count()


def _scroll_to_licences(page) -> None:
    """Back-compat wrapper: reveal the licence rows."""
    _reveal_licences(page)


def filter_creality_licences(page, should_stop=None) -> dict:
    """Tick the licence allow-list on the live listing and read it back.

    Ticks ONLY CC BY / CC BY-SA / CC BY-NC / CC BY-NC-SA / CC BY-NC-ND / CC0 —
    never CC BY-ND, never Standard License — then re-reads every option and
    reports what is actually ticked. Returns
    {opened, state, ticked_on, ticked_off, verified}.
    """
    out = {"opened": False, "state": {}, "ticked_on": [], "ticked_off": [],
           "verified": False}
    if not _open_filters(page):
        return out
    out["opened"] = True

    for _pass in range(3):
        if should_stop and should_stop():
            break
        try:
            state = json.loads(page.evaluate(LICENCE_STATE_JS) or "{}")
        except Exception:
            state = {}
        wanting = [l for l in CREALITY_LICENCE_ALLOW if not state.get(l)]
        wrong = [l for l in CREALITY_LICENCE_DENY if state.get(l)]
        if not wanting and not wrong:
            break
        if _pass == 2:
            break
        boxes = _reveal_licences(page, should_stop=should_stop)
        for label in wanting + wrong:
            if should_stop and should_stop():
                break
            box = boxes.get(label)
            if not box:
                continue
            try:
                page.mouse.click(box["x"] + 4, box["y"])
                page.wait_for_timeout(320)
            except Exception:
                pass
        page.wait_for_timeout(300)

    try:
        out["state"] = json.loads(page.evaluate(LICENCE_STATE_JS) or "{}")
    except Exception:
        out["state"] = {}
    out["ticked_on"] = sorted([k for k, v in out["state"].items() if v])
    out["ticked_off"] = sorted([k for k, v in out["state"].items() if not v])
    out["verified"] = (all(out["state"].get(l) for l in CREALITY_LICENCE_ALLOW)
                       and not any(out["state"].get(l) for l in CREALITY_LICENCE_DENY))

    # apply the filter (the panel's confirm button, in the UI's language:
    # 'Confirm' / 'Confirmer' / 'Appliquer' …)
    for label in ("Confirm", "Confirmer", "Apply", "Appliquer", "OK",
                  "Valider", "Search", "Rechercher", "\u786e\u8ba4",
                  "\u5e94\u7528", "\u041e\u041a"):
        try:
            page.get_by_text(label, exact=True).first.click(timeout=4000)
            out["applied_with"] = label
            page.wait_for_timeout(4500)
            break
        except Exception:
            continue
    return out


# the sort row's label in every language the listing has shown or may show
# (English 'Downloads', French 'Téléchargements', …)
_SORT_WORD = re.compile(
    r"downloads|t[ée]l[ée]chargements|descargas|scaricamenti|"
    r"\u4e0b\u8f7d|\u0441\u043a\u0430\u0447\u0438\u0432\u0430\u043d", re.I)


def _sort_by_downloads(page) -> str:
    """Sort the listing most-downloaded first (the sort row is span.hand)."""
    try:
        texts = page.evaluate(
            """() => [...document.querySelectorAll('span.hand')]
                       .map(e => (e.innerText || '').trim()).filter(Boolean)""") or []
        label = next((t for t in texts if _SORT_WORD.search(t)), "Downloads")
        el = page.locator("span.hand", has_text=label).first
        el.click(timeout=6000)
        page.wait_for_timeout(3500)
        return label
    except Exception as e:
        return "sort not applied: " + str(e)[:90]


def judge_card(name: str, url: str, used_families: set, is_done=None) -> dict:
    """Decide ONE listing card, in the order that matters.

    Already-done first: a model we have finished must not claim its family for
    this run. It used to be judged by the household gate BEFORE this check, so an
    already-published cable organizer took the whole cable-organizer family with
    it and every FRESH cable organizer on the page was turned down with "already
    going out in this run" — a reason that was not true. Only a card that is
    actually harvested claims its family.

    Then the household gate (selector.py, the one owner of what may be published):
    rubbish and off-family cards are refused with the reason for the refusal.

    Returns {"state": "skipped"|"rejected"|"harvest", "reason", "family"}.
    """
    if is_done is not None:
        try:
            if is_done(url, name) or (_english_url(url) != url
                                      and is_done(_english_url(url), name)):
                return {"state": "skipped", "reason": "already published",
                        "family": ""}
        except Exception:
            pass                       # a broken lookup must not stop the harvest
    verdict = selector.allow(name, url, used_families)
    if verdict["allowed"]:
        return {"state": "harvest", "reason": "", "family": verdict["family"]}
    if verdict["source"] == "duplicate-family":
        # a repeat of a family already going out: kept aside, and used only to
        # fill the requested count once every listing has been read
        return {"state": "spare", "reason": verdict["reason"],
                "source": verdict["source"], "family": verdict["family"]}
    return {"state": "rejected", "reason": verdict["reason"],
            "source": verdict["source"], "family": ""}


def _english_url(url: str) -> str:
    """The English form of a Creality model link (drops the /xx/ locale)."""
    return re.sub(r"(crealitycloud\.com)/[a-z]{2}(?=/|$)", r"\1", url or "")


# Listings a run decided NOT to publish (refused by the licence/policy gate, no
# model file, or a page that would not fetch) are written here, so the next Find
# Models does not open them again. A refusal is re-checked once the cooldown ends.
TRIED_PATH = Path(__file__).resolve().parent / "artifacts" / "tried_listings.json"
TRIED_COOLDOWN_SECONDS = 7 * 24 * 3600

# A listing this run could not decide — the fetch died, no file came back, the run
# was stopped — is written down too, but forgiven after a couple of hours instead
# of a week. Two different things share one record: "this model is not ours to
# publish" and "our browser had bad luck with this page".
# 2026-10-23 proved the difference matters: a run that skipped 20 models on a
# transient failure cooled all 20 listings down for a week, so every later scan
# had less to choose from, scrolled deeper, and came back with fewer candidates.
TRIED_TRANSIENT_COOLDOWN_SECONDS = 2 * 3600


# States that mean a run OPENED the listing's page (or was in the middle of it),
# whatever the outcome. "published" is not here: the ledger already refuses those.
USED_STATES = ("skipped", "fetch-failed", "duplicate", "failed", "fetching", "uploading")


def listing_was_used(it: dict) -> bool:
    """True when a run opened this listing's page. A run stopped AFTER the page
    was read leaves the item 'queued' with a 'run stopped …' reason: that page was
    still opened, so it counts too — otherwise the next run opens it again."""
    state = it.get("state") or ""
    if state in USED_STATES:
        return True
    return state == "queued" and (it.get("reason") or "").startswith("run stopped")


# A listing whose page was opened and then FAILED for a reason that says nothing
# about the model — the fetch died, no file came back, the run was stopped, the
# cover photos were unreadable — is not a REFUSAL. Cooling those listings down
# for a week is what starved the pool: every unlucky run removed dozens of still
# perfectly good models from the next scan, and a scan with less to choose from
# has to scroll deeper, until a run asked for 20 cannot fill 20 at all.
# Refusals the model itself caused (licence, policy, duplicate) keep their
# cooldown; only the pipeline's own misfortune is forgiven.
_TRANSIENT_OUTCOME_RE = re.compile(
    r"no usable model file|could not fetch|run stopped|not real model geometry|"
    r"cover photos|re-fetched|fetch attempt", re.I)


def listing_was_refused(it: dict) -> bool:
    """True when a run decided this listing is not worth publishing — as opposed
    to failing to publish it for a reason that could go away on its own."""
    if not listing_was_used(it):
        return False
    if (it.get("state") or "") == "duplicate":
        return True
    return not _TRANSIENT_OUTCOME_RE.search(it.get("reason") or "")


def _entry_is_transient(entry: dict) -> bool:
    """Whether a remembered listing was a refusal or only bad luck.

    Records written before the two tiers existed carry no `transient` flag, so the
    reason they stored decides — which also unwinds the week-long cooldown the
    2026-10-23 run put on 20 listings that never actually failed.
    """
    if "transient" in entry:
        return bool(entry.get("transient"))
    return bool(_TRANSIENT_OUTCOME_RE.search(entry.get("reason") or ""))


def _tried_window(entry: dict) -> float:
    return (TRIED_TRANSIENT_COOLDOWN_SECONDS if _entry_is_transient(entry)
            else TRIED_COOLDOWN_SECONDS)


def _seed_from_runs(runs_dir: Path) -> dict:
    """First use of the record: remember what earlier runs decided, so the
    listings they already refused are not opened again either."""
    data: dict = {}
    for run_file in sorted(runs_dir.glob("auto_run_*.json")):
        try:
            run = json.loads(run_file.read_text(encoding="utf-8"))
            when = time.mktime(time.strptime(run.get("finished") or "",
                                             "%Y-%m-%d %H:%M:%S"))
        except (OSError, ValueError, TypeError):
            continue
        for it in run.get("items") or []:
            if listing_was_used(it) and it.get("url"):
                data[_english_url(it["url"])] = {"name": it.get("name", ""),
                                                 "reason": it.get("reason", ""),
                                                 "when": when,
                                                 "transient": not listing_was_refused(it)}
    return data


def _tried_store(path: Path) -> dict:
    if not path.exists():
        data = _seed_from_runs(path.parent)
        _write_tried(path, data)
        return data
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable record: keep the bad file aside rather than overwrite it with
        # a fresh one (that wiped every remembered listing on the next write).
        try:
            path.replace(path.with_name(path.stem + ".corrupt.json"))
        except OSError:
            pass
        return _seed_from_runs(path.parent)


def _write_tried(path: Path, data: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    # write to a temp file and swap it in, so a crash mid-write cannot leave a
    # half-written record behind
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def remember_tried(url: str, name: str, reason: str, path=None, now=None,
                   transient: bool = False) -> None:
    """Record that this listing was opened and not published. The first decision
    keeps its date, so the cooldown is not pushed back on every run."""
    if not url:
        return
    store = Path(path) if path else TRIED_PATH
    data = _tried_store(store)
    key = _english_url(url)
    now_ts = time.time() if now is None else now
    # keep the first date while the cooldown runs; once it has ended, a fresh
    # decision starts a new cooldown (otherwise the listing was opened every run)
    prev = data.get(key)
    if prev and now_ts - float(prev.get("when") or 0) < _tried_window(prev):
        # a real refusal is still inside its week: bad luck must not shorten it,
        # and a repeat refusal must not push its date back
        if not transient or not _entry_is_transient(prev):
            return
    data[key] = {"name": name or "", "reason": (reason or "")[:200],
                 "when": now_ts, "transient": bool(transient)}
    _write_tried(store, data)


def was_tried_recently(url: str, path=None, now=None) -> bool:
    """True while a listing's cooldown is running, so Find Models skips it
    without opening the page — a week for a refusal, a couple of hours for a run
    that merely had bad luck with it."""
    if not url:
        return False
    entry = _tried_store(Path(path) if path else TRIED_PATH).get(_english_url(url))
    if not entry:
        return False
    current = time.time() if now is None else now
    return current - float(entry.get("when") or 0) < _tried_window(entry)


def find_creality(keywords: list[str] | None = None, count: int = 10,
                  sort: str = "Downloads", is_done=None, progress=None,
                  should_stop=None, exclude=None, extra: int | None = None,
                  deadline_s: float | None = None) -> dict:
    """Find Creality Cloud models worth re-publishing.

    For each keyword (and once for the whole listing when none is given): the
    listing is sorted by downloads, the licence filter is ticked with the
    allow-list and READ BACK, then model cards are harvested while the listing
    scrolls. Returns
    {models: [{name, url, keyword}], licence: {...}, keywords, note,
     rejected: [{name, url, reason, source}], rejected_total, families}.

    `extra` — how many candidates BEYOND `count` to harvest. A run asked for 20
    gets exactly 20 rows to try, and when some of them do not publish (no model
    file, a licence the page reads differently than the card did) the queue is
    already empty, so the whole listing scan runs again. Measured on 2026-10-23:
    20 models asked for, 20 fetches that brought no file back, two full scans
    (3459s) and zero published. Harvesting a surplus in ONE scan turns those
    replacements into queue rows that cost nothing.

    `deadline_s` — a wall-clock ceiling for the whole scan. The one-of-each-family
    rule sends the scan to every keyword, and 601 scroll passes at ~2.4s each is
    exactly what a 20-model run did on 2026-10-23: 58 of its 82 minutes went on
    discovery. Coming back with what it has, and saying so, beats never coming
    back at all.

    `exclude` — listing URLs this run has already attempted (a failed or refused
    model is replaced by a fresh one, never re-picked). They are scrolled past
    without being counted, exactly like an already-done model.

    `is_done(url, name)` — models that are already in the "already published"
    config are NEVER harvested: they are not counted towards `count` and the
    listing is scrolled past them, so a run does not even look at a model it
    has finished before.

    Every other card is judged by `selector` — the ONE owner of "is this a useful
    household item?" — before it is harvested: rubbish (a toilet, a garage or
    workshop project, a novelty, a trinket) and anything outside the useful
    household families is not counted towards `count` either, so the listing
    keeps scrolling for an allowed model, and each refusal comes back in
    `rejected` with the reason it was turned down so the run can log it. That is
    the fix for a run whose answer to "household items" was a Toilet and a
    bathroom accessory set. Driven through the user's logged-in debug Chrome
    (same bootstrap as the fetcher/uploader).
    """
    from browser_opener import sync_playwright_auto
    from uploader import ensure_browser, CDP_URL

    t_total = time.perf_counter()

    keywords = [k.strip() for k in (keywords or []) if k and k.strip()]
    # the account's Chrome renders /fr/ pages; drive the ENGLISH site so the
    # listing and its Filters panel speak the language the rest reads
    count = max(1, int(count or 1))
    # Harvest a surplus by default: replacements for the models that will not
    # publish are already in the queue instead of needing a second full scan.
    if extra is None:
        extra = max(3, count // 2)
    target = count + max(0, int(extra))
    if deadline_s is None:
        deadline_s = DISCOVER_DEADLINE_S
    deadline = t_total + float(deadline_s) if deadline_s else None
    timed_out = False
    ensure_browser()

    found: dict[str, dict] = {}
    skip_urls = {_english_url(u) for u in (exclude or ())}
    skipped_names: list[str] = []          # already-done models, never harvested
    skipped_seen: set[str] = set()         # one entry per card, however many passes re-read it
    rejected: list[dict] = []              # judged not worth publishing, with why
    rejected_seen: set = set()
    used_families: set = set()             # one of each family per run (a preference)
    spares: list[tuple[str, dict]] = []    # repeats of a family, used only to fill `count`
    spare_seen: set = set()

    def in_hand() -> int:
        """How many publishable candidates the scan actually holds.

        This is harvests PLUS the repeats kept aside to fill the count, not
        harvests alone. The one-of-each rule is a preference, so `found` stops
        growing at the number of families (8) however long the scan runs: a
        request for 20 compared against `found` could never be satisfied, so
        every listing was scrolled to its end (2026-10-23: 601 passes, 58 min)
        and the run still came back short.
        """
        return len(found) + len(spares)

    licence_report: dict = {}
    note = ""
    stops = [CREALITY_SEARCH.format(q=k.replace(" ", "+")) for k in keywords] \
        or [CREALITY_LISTING]
    # the account's Chrome renders /fr/ pages; drive the ENGLISH site so the
    # listing and its Filters panel speak the language the rest reads
    stops = [re.sub(r"(crealitycloud\.com)/[a-z]{2}(?=/|$)", r"\1", u) for u in stops]

    stopped = False
    with sync_playwright_auto() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        for entry in stops:
            if in_hand() >= target or stopped:
                break
            if should_stop and should_stop():
                stopped = True
                break
            if deadline and time.perf_counter() > deadline:
                timed_out = True          # no new listing: the scan has had its time
                break
            page = browser.contexts[0].new_page()
            page.set_viewport_size({"width": 1440, "height": 900})
            try:
                with timing.phase("listing.open"):
                    page.goto(entry, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(6000)
                if should_stop and should_stop():
                    stopped = True        # Stop pressed: end the scan before the sort
                    break
                with timing.phase("listing.sort"):
                    _sort_by_downloads(page)
                if should_stop and should_stop():
                    stopped = True        # Stop pressed during the sort
                    break
                with timing.phase("licence.filter"):
                    rep = filter_creality_licences(page, should_stop=should_stop)
                if should_stop and should_stop():
                    stopped = True        # Stop pressed during the licence filter
                    break
                licence_report = rep
                if not rep["opened"]:
                    note = ("the listing's Filters panel did not open — refusing to "
                            "harvest from an unfiltered list")
                    continue           # this keyword only: try the next one
                if not rep["verified"]:
                    note = ("the licence filter did not read back as the allow-list "
                            "(ticked on: %s, off: %s) — refusing to harvest from an "
                            "unverified filter"
                            % (", ".join(rep["ticked_on"]), ", ".join(rep["ticked_off"])))
                    continue           # this keyword only: try the next one
                # harvest, scrolling the listing until it stops growing
                empty_scrolls = 0
                for pss in range(200):  # a ceiling only: the listing stops when it stops growing
                    if should_stop and should_stop():
                        stopped = True     # Stop pressed: end the scan at this pass
                        break
                    if deadline and time.perf_counter() > deadline:
                        timed_out = True    # the scan has had its wall-clock: keep what
                        break               # it found rather than never returning
                    with timing.phase("harvest.read"):
                        cards = json.loads(page.evaluate(CREALITY_CARDS_JS) or "[]")
                    for c in cards:
                        if in_hand() >= target:
                            # the page can hold far more cards than the run needs:
                            # judging them all would only cost time
                            break
                        url = c.get("url") or ""
                        if not url or url in found or _english_url(url) in skip_urls:
                            continue
                        name = clean_card_name(c.get("name"), c.get("alt"),
                                               c.get("title")) or \
                            _name_from_slug(url).title()
                        # one pure decision, in the order that matters: already
                        # done (skip it here so the run never even picks it, and
                        # dig deeper for a fresh one) THEN the household gate
                        # (rubbish and off-family cards are not harvested, do not
                        # count towards `count`, and are reported back with the
                        # reason they were turned down).
                        verdict = judge_card(name, url, used_families, is_done)
                        if verdict["state"] == "skipped":
                            if url not in skipped_seen:
                                skipped_seen.add(url)
                                skipped_names.append(name or url)
                            continue
                        if verdict["state"] == "spare":
                            if url not in spare_seen and url not in found:
                                spare_seen.add(url)
                                spares.append((url, {
                                    "name": name,
                                    "url": re.sub(r"(crealitycloud\.com)/[a-z]{2}(?=/)", r"\1", url),
                                    "keyword": (entry.split("q=")[-1].split("&")[0].replace("+", " ")
                                                if "q=" in entry else ""),
                                    "family": verdict["family"], "spare": True}))
                            continue
                        if verdict["state"] == "rejected":
                            if url not in rejected_seen:
                                rejected_seen.add(url)
                                rejected.append({"name": name, "url": url,
                                                 "reason": verdict["reason"],
                                                 "source": verdict.get("source", "")})
                            continue
                        # give the run the ENGLISH URL: a /fr/ model link fetches
                        # the French page, whose huh-licence read is exactly the
                        # "no licence was read" skip (strip any locale prefix)
                        english_url = re.sub(r"(crealitycloud\.com)/[a-z]{2}(?=/)",
                                             r"\1", url)
                        found[url] = {"name": name, "url": english_url,
                                      "keyword": (entry.split("q=")[-1].split("&")[0]
                                                  .replace("+", " ")
                                                  if "q=" in entry else "")}
                    if progress:
                        progress({"keyword": (entry.split("q=")[-1].split("&")[0].replace("+", " ") if "q=" in entry else "all models"), "pass": pss + 1, "cards": len(cards), "found": in_hand(), "harvests": len(found), "count": count})
                    if in_hand() >= target:
                        break
                    # the end of the listing = the PAGE stopped growing. Count the
                    # cards on the page, not the accepted models: rejected and
                    # already-done cards never reach `found`, so comparing against
                    # it never saw the end and the scan ran all 200 passes.
                    before = len(cards)
                    with timing.phase("harvest.scroll"):
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        after = _wait_for_more_cards(page, before,
                                                     should_stop=should_stop)
                        if should_stop and should_stop():
                            stopped = True
                            break
                    empty_scrolls = empty_scrolls + 1 if after <= before else 0
                    if empty_scrolls >= 2:
                        break
            except Exception as e:
                note = note or ("listing scan failed: " + str(e)[:160])
            finally:
                try:
                    page.close()
                except Exception:
                    pass

    # The one-of-each rule is a preference, not a ceiling: once every listing has
    # been read and the run still wants more, the repeats fill the count in the
    # order they were seen. Each one already passed the household gate.
    for url, item in spares:
        if len(found) >= target or stopped:
            break
        if url not in found:
            found[url] = item
    # The ceiling is the reason the queue is short, so it is reported first: a
    # scan that ran out of its own time must not be described as a listing that
    # ran out of models.
    if timed_out and not stopped:
        # honest about WHY the scan stopped with the queue short: it ran out of
        # its own time, not out of listings
        note = (note + " | " if note else "") + (
            "the listing scan stopped at its %d-second ceiling with %d candidate(s) "
            "in hand (asked for %d) — raise AUTO_DISCOVER_DEADLINE_S for a longer scan"
            % (int(deadline_s or 0), len(found), count))
    elif len(found) < count and not note and not stopped:
        note = ("only %d of the %d models asked for were found in the listings"
                % (len(found), count))
    # The whole scan's wall-clock, recorded next to its parts: this is the phase
    # that dominates a run (one fixed 6s wait per keyword, a 2.5s wait per scroll
    # pass, up to 200 passes), so it has to be in the table, not guessed at.
    timing.record("discover.total", time.perf_counter() - t_total)
    return {"models": list(found.values())[:target], "licence": licence_report,
            "keywords": keywords, "note": note, "count": count,
            "harvested": len(found), "timed_out": timed_out,
            "skipped_done": len(skipped_names),
            "already_done": skipped_names[:12],
            "rejected": rejected[:REJECTED_MAX],
            "rejected_total": len(rejected), "stopped": stopped}


def _name_from_slug(url: str) -> str:
    # ".../models/2964241-gt-r-r34-mini-kit" -> "Gt R R34 Mini Kit"
    tail = url.rstrip("/").split("/")[-1]
    tail = re.sub(r"^(thing:)?\d+-?", "", tail, flags=re.I)   # strip leading id / thing:
    tail = re.sub(r"[-_]+", " ", tail).strip()
    return tail.title() if tail else ""


_NAV_NOISE = re.compile(r"popular|newest|most makes|filters|sort|trending|explore", re.I)


def _clean_name(slug_name: str, alt: str, title: str, card_text: str) -> str:
    # Prefer the URL slug name; then the image alt or link title; only use the
    # card text as a last resort, and reject obvious nav/filter-bar noise.
    for cand in (slug_name, alt, title):
        c = (cand or "").strip()
        c = re.sub(r"^(thumbnail representing|image of|photo of|preview of)\s+", "", c, flags=re.I)
        if c and not _NAV_NOISE.search(c):
            return c[:70]
    first = (card_text or "").split("  ")[0].strip()
    if first and not _NAV_NOISE.search(first):
        return first[:60]
    return "Untitled"


async def discover(site: str, count: int = 20) -> list[dict]:
    site = (site or "").lower()
    cfg = SITES.get(site)
    if not cfg:
        raise ValueError(f"Unknown site '{site}'. Choose from: {', '.join(SITES)}")

    # Pick a random ranked listing each call (trending / popular / newest / …) so
    # we don't keep landing on the exact same page.
    listing = random.choice(cfg["urls"])

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(user_agent=UA, viewport={"width": 1400, "height": 1000})
            page = await ctx.new_page()
            await page.goto(listing, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
            # Scroll just enough to load the TOP of the (popularity-sorted) list — we
            # only sample from the most-popular slice, so no need to go deep.
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1200)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(800)

            items = await page.evaluate(
            """
            ({sel, linkRe}) => {
                const re = new RegExp(linkRe);
                const links = [...document.querySelectorAll(sel)];
                const seen = new Set(); const out = [];
                for (const a of links) {
                    const m = (a.href || '').match(re);
                    if (!m) continue;
                    const id = m[1];
                    if (seen.has(id)) continue; seen.add(id);
                    // climb just 2 levels to the immediate card (not the page chrome)
                    let card = a; for (let i=0;i<2 && card.parentElement;i++) card = card.parentElement;
                    const img = (a.querySelector('img') || card.querySelector('img'));
                    let thumb = '', alt = '';
                    if (img) {
                        alt = (img.alt || '').trim();
                        thumb = img.currentSrc || img.src || img.getAttribute('data-src') || '';
                        if (!thumb || thumb.startsWith('data:')) {
                            const ss = img.getAttribute('srcset') || img.getAttribute('data-srcset') || '';
                            if (ss) thumb = ss.split(',')[0].trim().split(' ')[0];
                        }
                    }
                    const title = (a.getAttribute('title') || a.getAttribute('aria-label') || '').trim();
                    const txt = (card.innerText || '').replace(/\\s+/g,' ').trim().slice(0, 140);
                    out.push({ id, url: a.href.split('#')[0].split('?')[0], thumb, alt, title, text: txt });
                }
                return out;
            }
            """,
                {"sel": cfg["link_sel"], "linkRe": cfg["link_re"]},
            )
        finally:
            await browser.close()

    # The listing is sorted by popularity, so items[0..N] are the most popular.
    # Sample from just the TOP slice — this keeps results genuinely popular while
    # still varying which of the top models appear each time.
    top_pool = items[: max(count * 2, 30)]
    if len(top_pool) > count:
        items = random.sample(top_pool, count)
    else:
        items = top_pool
        random.shuffle(items)

    results = []
    for it in items:
        # try to lift a download/like count out of the card text (e.g. "12.3k")
        stat = ""
        mstat = re.search(r"(\d[\d.,]*\s*[kK]?)\s*(downloads?|likes?|prints?|boosts?|↓|♥)", it["text"])
        if mstat:
            stat = mstat.group(0)
        results.append({
            "name": _clean_name(_name_from_slug(it["url"]), it.get("alt", ""), it.get("title", ""), it["text"]),
            "url": it["url"],
            "thumb": it["thumb"],
            "stats": stat,
        })
    return results
