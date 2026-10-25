import re
import os
import json
import html as _html
import requests
from urllib.parse import urlparse

import timing

# no-derivatives markers: "by-nd", "by-nc-nd", a bare "nd" token, or words
# (\b keeps "and"/"end" from matching)
nd_re = re.compile(r"by-nc-nd|by-nd|\bnd\b|no-?deriv", re.I)

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

# Creality's create-model form accepts ONLY these image types (read from each
# image input's accept attribute: ".jpeg,.png,.jpg,.webp,.gif"). Galleries
# serve some photos as .avif, which the form silently refuses — so every photo
# is converted to a format the form takes BEFORE it is queued for upload.
UPLOAD_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Read the model page's licence CARD. Every card opens with the boilerplate
# line "This work is licensed under the Copyright License 4.0." — it is on CC
# models too — so the line that decides is the licence paragraph after it:
#   CC model    -> "This license allows reusers to distribute, remix, adapt, and
#                  build upon the material ..."  + a creativecommons.org link
#   Creality's  -> "... these files cannot be used for any commercial use; it's
#   Standard      for personal use only. 3D model or any portion of the model in
#                  a digital or physical format may not be shared, transmitted,
#                  redistributed, remixed, duplicated, or sold."
# Both the CC link and the card text are returned; classification only refuses
# the restrictive paragraph, never the boilerplate line.
LICENSE_JS = r"""
    () => {
        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
        const CC_LINK = /creativecommons\.org\/(?:licenses|publicdomain)\/[a-z0-9\-\/]+/i;
        // A licence STATEMENT is any text that talks about licensing. Creality
        // writes "This work is licensed under the Copyright License 4.0." on
        // EVERY model page (CC ones included) and also shows a site-wide
        // Standard-License paragraph, so BOTH can be read on a CC page and
        // neither one, alone, is this model's licence.
        // The same lines in the site's other languages are matched too — the
        // account's logged-in Chrome serves /fr/ pages ("Cette œuvre est sous
        // licence…", the French CC grant paragraph), and a reader that only
        // spoke English skipped every French model with "no licence was read".
        const IS_LICENCE = /this work is licensed under|creativecommons\.org|cc\s?by|ccby|cc\s?0\b|cc0\b|public domain|no rights reserved|this license allows reusers|cette .uvre est sous licence|sous licence|licence standard|domaine public/i;
        // ...whereas THESE only appear where a CC licence is actually named.
        const CCISH = /creativecommons\.org|cc\s?by[\s\-\/]|ccby|cc0\b|cc\s?0\b|this license allows reusers|public domain|cette licence autorise|domaine public|sous licence (cc|creative commons)/i;

        const els = [...document.querySelectorAll('p,span,div,li,td,section,article,label')]
            .map(e => ({ e, t: clean(e.innerText) }))
            .filter(c => c.t && c.t.length <= 1800 && IS_LICENCE.test(c.t));
        // innermost statements only: a candidate that contains another is a wrapper
        const inner = els.filter(c => !els.some(o => o !== c && o.e.contains(c.e)));
        const byLen = (a, b) => a.t.length - b.t.length;
        // 1) the model's OWN licence card: the shortest statement that NAMES a
        //    licence (CC link / CC name / the CC grant paragraph). The site's
        //    Standard-License block has none of those, so it cannot win here —
        //    that mix-up is how a CC-licensed source got refused.
        const named = inner.filter(c => CCISH.test(c.t) || CC_LINK.test(c.t))
            .sort((a, b) => {
                const w = (c) => /this license allows reusers|creativecommons\.org/i.test(c.t) ? 0 : 1;
                return w(a) - w(b) || a.t.length - b.t.length;
            })[0];
        const pick = named || inner.sort(byLen)[0] || els.sort(byLen)[0] || null;

        // read the whole licence section around it: the card is rendered in
        // pieces, so a wider read is what keeps a licence from being missed.
        // It must NOT swallow page-level text: the moment an ancestor adds
        // ANOTHER licence statement (the page footer's standard-license
        // paragraph, or a different model's card) the climb stops, so one
        // model's card and the site boilerplate can never be mixed together.
        const marks = (s) => ((s.match(/this work is licensed under|creativecommons\.org|this license allows reusers|available under a standard license|cette .uvre est sous licence|sous licence/gi) || []).length);
        let card = pick ? pick.t : '';
        if (pick) {
            let up = pick.e, hops = 0, best = card;
            while (up.parentElement && hops < 3) {
                const wide = clean(up.parentElement.innerText);
                if (wide.length > 1400) break;                  // a page-level box
                if (marks(wide) > marks(best)) break;           // a second statement
                up = up.parentElement; hops++;
                if (wide.length > best.length) best = wide.slice(0, 1200);
            }
            card = best;
        }
        if (!card) {
            const bm = (document.body.innerText || '')
                .match(/(?:This work is licensed under|Cette .uvre est sous licence)[\s\S]{0,600}/i);
            if (bm) card = clean(bm[0]);
        }

        // the CC link: inside the card, else the licence area, else the page
        let cc = (card.match(CC_LINK) || [])[0] || '';
        let up = pick ? pick.e : null, hops = 0;
        while (!cc && up && hops < 5) {
            const a = up.querySelector('a[href*="creativecommons"],img[src*="creativecommons"]');
            const s = a ? (a.getAttribute('href') || a.getAttribute('src') || '') : '';
            cc = (s.match(CC_LINK) || [])[0] || '';
            up = up.parentElement; hops++;
        }
        if (!cc) cc = (document.documentElement.innerHTML.match(CC_LINK) || [])[0] || '';

        // licence ICON badges: the licence is also DRAWN as small images
        // (cc.svg, by.svg, nc.svg …). Read only from the licence AREA — icons
        // picked up anywhere on the page would be someone else's badge.
        const icons = [];
        up = pick ? pick.e : null;
        for (let i = 0; i < 5 && up; i++) {
            for (const im of up.querySelectorAll('img,use,image')) {
                const s = (im.getAttribute('src') || im.getAttribute('alt') ||
                           im.getAttribute('xlink:href') || '').toLowerCase();
                if (s && s.length < 160 &&
                    /(^|[\/\-_ ])(cc|by|nc|sa|nd|zero)([.\/\-_ ]|$)/.test(s) &&
                    icons.indexOf(s) < 0) icons.push(s);
            }
            up = up.parentElement;
        }
        return JSON.stringify({cc_url: cc, card: card.slice(0, 1600),
                               icons: icons.slice(0, 12)});
    }
"""


# Find the clickable element whose OWN text matches `text_re`: the INNERMOST
# match, smallest first. A wrapper div merely CONTAINS a control's text, and
# clicking the container's centre navigated to the designer's profile page
# (`?profileId=…`) instead of downloading — found live 2026-10-09 on the
# French-rendered model page, which is why the manual link came back with 6
# photos and no model file. Shared by the fetcher and the live probes.
FIND_JS = r"""
    (reStr) => {
        const re = new RegExp(reStr, 'i');
        const collect = () => {
        const hits = [];
        for (const el of document.querySelectorAll('button,div,a,span,[role=button]')) {
            const t = (el.innerText||'').trim();
            if (!t || t.length > 60 || !re.test(t)) continue;
            const r = el.getBoundingClientRect();
            // a real control's own text span can be small (the dialog's
            // "Tout télécharger" label is 103x14), and the topmost test below
            // is what keeps a wrapper from being clicked instead
            if (r.width < 20 || r.height < 12 || r.x < 0 || r.y < 0
                || r.y >= window.innerHeight) continue;
            // innermost only — never a container that holds the control…
            const inner = [...el.querySelectorAll('button,div,a,span,[role=button]')]
                .some(c => {
                    const ct = (c.innerText||'').trim();
                    return ct && ct.length <= 60 && re.test(ct)
                        && c.getBoundingClientRect().width > 0;
                });
            if (inner) continue;
            // …and TOPMOST at its own centre: the page keeps its dropdown menu
            // items in the DOM while the dropdown is CLOSED (a hidden "Télécharger
            // des fichiers d'impression" row and its 200px container both match),
            // so clicking those coordinates hits the profile card behind them.
            const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
            if (cx < 0 || cy < 0 || cx >= window.innerWidth || cy >= window.innerHeight)
                continue;
            const top = document.elementFromPoint(cx, cy);
            if (!top || (top !== el && !el.contains(top) && !top.contains(el)))
                continue;
            hits.push({x: cx, y: cy, t, area: r.width * r.height});
        }
        return hits;
        };
        // The control can sit below the fold (on a short window the 'Download 3MF'
        // action box is under the viewport), so nothing is visible until it is
        // scrolled into view. Scroll the first matching control into view, then search again.
        let hits = collect();
        if (!hits.length) {
            for (const el of document.querySelectorAll('button,div,a,span,[role=button]')) {
                const t = (el.innerText||'').trim();
                if (t && t.length <= 60 && re.test(t) && el.getBoundingClientRect().width >= 20) {
                    el.scrollIntoView({block: 'center'});
                    break;
                }
            }
            hits = collect();
        }
        if (!hits.length) return null;
        hits.sort((a, b) => a.area - b.area);        // the control itself
        return hits[0];
    }
"""

# The model-file download control, in the site's own languages (the fetch drives
# the REAL page, which is served in the browser session's language).
DOWNLOAD_VERB_RE = (r"download|t[ée]l[ée]charger|telecharger|descargar|"
                    r"herunterladen|baixar|scaricare|descarga|sc[ae]rica")
DOWNLOAD_THING_RE = (r"3mf|stl|cao|cad|fichiers?|files?|archivos?|dateien?|"
                     r"arquivos?|model|design|druckdatei")
# "Download 3MF" when the exact item is already showing…
DOWNLOAD_3MF_RE = (r"(?:" + DOWNLOAD_VERB_RE + r")\s*3mf"
                   r"|3mf\s*(?:" + DOWNLOAD_VERB_RE + r")")
# …else the page's own download control in the site's language: "Download print
# files" is "Télécharger des fichiers d'impression" in French, and CAD is "CAO".
DOWNLOAD_MAIN_RE = (
    DOWNLOAD_3MF_RE
    + r"|(?:" + DOWNLOAD_VERB_RE + r")[^|]{0,32}(?:" + DOWNLOAD_THING_RE + r")"
    + r"|view stl/cad files|voir les fichiers stl/cao|stl/cad-dateien"
    + r"|ver archivos stl/cad|ver arquivos stl/cad|visualizza file stl/cad"
    + r"|stl/cao|stl/cad")
DOWNLOAD_ITEM_RE = (
    r"3mf|t[ée]l[ée]charger|descargar|herunterladen|baixar|scaricare|download")

# Step 2 of the download — the control INSIDE the file dialog — is searched for
# among the dialog's own elements only, and never inside the page header, nav or
# footer. FIND_JS alone was not enough here: the signed-in site's top navigation
# carries a "Downloads" item, DOWNLOAD_VERB_RE matches it, and FIND_JS returns
# the SMALLEST matching box — so the step-2 "click the dialog's Download All"
# could click the nav link instead and leave the model page. That is a model with
# a perfectly good file reported as "no usable model file came back from the
# page", and it is why a long run stopped publishing while a single model worked.
FIND_DIALOG_JS = r"""
    (reStr) => {
        const re = new RegExp(reStr, 'i');
        const panels = [...document.querySelectorAll(
            '.el-dialog,.el-drawer,.el-message-box,[role=dialog]')]
            .filter(d => d.getBoundingClientRect().width > 0);
        const roots = panels.length ? panels : [document.body];
        const hits = [];
        for (const root of roots) {
            for (const el of root.querySelectorAll('button,div,a,span,[role=button]')) {
                if (el.closest('header,nav,footer,.header,.nav,.footer')) continue;
                const t = (el.innerText || '').trim();
                if (!t || t.length > 60 || !re.test(t)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 12 || r.x < 0 || r.y < 0) continue;
                if (r.y >= window.innerHeight) continue;
                const inner = [...el.querySelectorAll('button,div,a,span,[role=button]')]
                    .some(c => {
                        const ct = (c.innerText || '').trim();
                        return ct && ct.length <= 60 && re.test(ct)
                            && c.getBoundingClientRect().width > 0;
                    });
                if (inner) continue;
                const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
                if (cx < 0 || cy < 0 || cx >= window.innerWidth || cy >= window.innerHeight)
                    continue;
                const top = document.elementFromPoint(cx, cy);
                if (!top || (top !== el && !el.contains(top) && !top.contains(el)))
                    continue;
                hits.push({x: cx, y: cy, t, area: r.width * r.height,
                           panel: !!panels.length});
            }
        }
        if (!hits.length) return null;
        hits.sort((a, b) => a.area - b.area);
        return hits[0];
    }
"""

# The dialog that "View STL/CAD Files" opens lists each printable file as a row,
# and a row's ONLY download control is an icon with no text at all:
#     <div class="model-item"> … <i class="iconfont icon-download"></i></div>
# Both finders above only answer with an element whose TEXT matches, so they can
# never see it. Live 2026-10-24 on /model-detail/ender-3-cable-chain: the dialog
# held a real STL (5.94 KB) and a 3MF, both finders answered null for the
# download wording, step 2 clicked nothing, no download started, and a model with
# perfectly good files was skipped as "no usable model file came back from the
# page" — the note every one of the 2026-10-23 run's 20 skips carried.
# So the dialog is read by STRUCTURE instead of by words. One constant, three
# calls: q={} lists what the dialog holds, q={index:N} locates (or, with
# force, DOM-clicks) that row's own icon, q={all:true} does the same for the
# dialog's folder-level "download everything" control.
MODEL_FILES_JS = r"""
    (q) => {
        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
        const ICON = '.icon-download, [class*=icon-download]';
        const toBytes = (t) => {
            const m = /([0-9][0-9.,]*)\s*(gb|mb|kb|gib|mib|kib|b)\b/i.exec(clean(t));
            if (!m) return 0;
            const n = parseFloat(m[1].replace(/\s/g, '').replace(',', '.'));
            if (!(n > 0)) return 0;
            const u = m[2].toLowerCase();
            return Math.round(n * (/^g/.test(u) ? 1073741824
                                 : /^m/.test(u) ? 1048576
                                 : /^k/.test(u) ? 1024 : 1));
        };
        const panels = [...document.querySelectorAll(
            '.el-dialog,.el-drawer,[role=dialog]')]
            .filter(d => d.getBoundingClientRect().width > 0);
        const roots = panels.length ? panels
            : [...document.querySelectorAll(
                  '.model-download-wrap,.folder-box,.folder-files')];
        if (!roots.length) return {error: 'no file dialog is open'};
        const rows = [];
        const seen = new Set();
        // The name is the row's own label. On this layout the element holding it
        // (.model-name) ALSO holds the size, so the size is subtracted rather
        // than trusted as part of the name.
        const nameOf = (row, size) => {
            for (const sel of ['.name', '.model-name', '.file-name', '.title']) {
                const el = row.querySelector(sel);
                if (!el) continue;
                const t = clean(el.innerText);
                if (!t) continue;
                if (size && t.endsWith(size))
                    return clean(t.slice(0, t.length - size.length));
                return t;
            }
            return clean(row.innerText).slice(0, 70);
        };
        for (const root of roots) {
            for (const row of root.querySelectorAll(
                    '.model-item,.file-item,.folder-files > *,.dialog-file-list > *')) {
                if (seen.has(row)) continue;
                seen.add(row);
                const r = row.getBoundingClientRect();
                const cs = getComputedStyle(row);
                // A row the browser does not draw is not a file: the listing keeps
                // its templates mounted, and clicking one starts nothing.
                if (r.width < 20 && r.height < 20) continue;
                if (cs.display === 'none' || cs.visibility !== 'visible') continue;
                const sizeEl = row.querySelector('.size,.file-size,.model-size');
                const size = clean(sizeEl ? sizeEl.innerText : '');
                rows.push({row, icon: row.querySelector(ICON), size,
                           bytes: toBytes(size), name: nameOf(row, size)});
            }
        }
        const find_all = () => {
            const drawn = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && cs.display !== 'none'
                    && cs.visibility === 'visible';
            };
            for (const root of roots) {
                const icon = root.querySelector('.folder-operate ' + ICON);
                if (drawn(icon))
                    return {el: icon, label: clean(icon.title) || 'download all'};
                const btn = [...root.querySelectorAll('button,a,.el-button')]
                    .find(b => drawn(b)
                        && /download all|tout t[ée]l[ée]charger|descargar todo/i
                            .test(clean(b.innerText)));
                if (btn) return {el: btn, label: clean(btn.innerText)};
            }
            return null;
        };
        const wants = q || {};
        let target = null;
        const asked = wants.all || typeof wants.index === 'number';
        if (wants.all) target = find_all();
        else if (typeof wants.index === 'number') {
            const it = rows[wants.index];
            if (!it) return {error: 'row ' + wants.index + ' is gone'};
            target = {el: it.icon, label: it.name};
        }
        if (asked && (!target || !target.el))
            return {error: 'no control to click: ' + ((target || {}).label || '')};
        if (target) {
            const el = target.el;
            try { el.scrollIntoView({block: 'center'}); } catch (e) {}
            if (wants.force) {
                try { el.click(); } catch (e) { return {error: String(e).slice(0, 90)} }
                return {clicked: true, name: target.label || ''};
            }
            const r = el.getBoundingClientRect();
            const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
            const top = document.elementFromPoint(x, y);
            return {x, y, name: target.label || '',
                    hit: !!top && (top === el || el.contains(top) || top.contains(el)),
                    inView: r.width > 0 && r.height > 0
                        && y >= 0 && y < window.innerHeight};
        }
        const point = (el) => {
            if (!el) return null;
            const r = el.getBoundingClientRect();
            if (r.width < 3 || r.height < 3) return null;
            return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
        };
        const al = find_all(), ap = al ? point(al.el) : null;
        return {rows: rows.map((it, i) => ({i, name: it.name, size: it.size,
                bytes: it.bytes, icon: point(it.icon)})),
                all: ap ? {x: ap.x, y: ap.y, name: al.label} : null};
    }
"""


def _dialog_files(page) -> dict:
    """What the file dialog really holds: its rows (name, size, its own icon's
    point) and its folder-level control. Retries while the dialog is still
    mounting; returns {"error": …} when there is nothing to read."""
    got: dict = {}
    for _ in range(12):
        try:
            got = page.evaluate(MODEL_FILES_JS, {}) or {}
        except Exception as e:
            return {"error": str(e)[:120]}
        if got.get("rows"):
            return got
        if str(got.get("error", "")).startswith("no file dialog"):
            return got            # no dialog: waiting will not conjure one
        page.wait_for_timeout(500)
    return got


def _dialog_click(page, where: dict) -> dict:
    """Click one control of the file dialog — a row's icon ({"index": i}) or the
    folder-level one ({"all": True}) — with the real mouse when the page says the
    point is reachable, and through the DOM when it is not: the dialog scrolls
    its own list, so a row below the fold has no point a mouse can be put on."""
    last: dict = {}
    for force in (False, True):
        try:
            got = page.evaluate(MODEL_FILES_JS, dict(where, force=force)) or {}
        except Exception as e:
            return {"error": str(e)[:120]}
        if got.get("error"):
            return got
        last = got
        if force:
            return got
        if got.get("hit") and got.get("inView"):
            try:
                page.mouse.click(got["x"], got["y"])
                return {"clicked": True, "name": got.get("name", ""), "mouse": True}
            except Exception as e:
                return {"error": str(e)[:120]}
    return {"error": "not reachable", "name": str(last.get("name", ""))[:60]}


def _files_from_dialog(page, downloads, slug: str, model_url: str, report: dict,
                       verify, wait_ms: int = 500, patience: int = 30,
                       max_rows: int = 3) -> str | None:
    """Take the model file out of Creality's file dialog and return the saved path
    of the first answer that verifies as real geometry; None when nothing did.

    Rows are tried largest-first: a listing keeps a few-KB preview stub next to
    the real print file, and a DOM-order click is how a 5 KB stand-in was being
    fetched for a 2 MB part. Every saved file must pass `verify` before it is
    returned, so an unverified file is never handed to the uploader, and each
    refusal is named in the report — the run can then say WHICH file was junk
    instead of "no usable model file came back from the page".
    """
    got = _dialog_files(page)
    rows = got.get("rows") or []
    if not rows:
        report["dialog"] = str(got.get("error", "the dialog listed no files"))[:120]
        return None
    report["files"] = [{"name": r.get("name", ""), "size": r.get("size", "")}
                       for r in rows][:10]
    clickable = sorted((r for r in rows if r.get("icon")),
                       key=lambda r: -(r.get("bytes") or 0))
    attempts: list = [{"index": r["i"]} for r in clickable[:max_rows]]
    if got.get("all"):
        attempts.append({"all": True})
    report["tried_files"] = []
    rejected: list = []
    mark = len(downloads)
    for where in attempts:
        clicked = _dialog_click(page, where)
        report["tried_files"].append(
            {"name": str(clicked.get("name", ""))[:60],
             "how": "mouse" if clicked.get("mouse") else
                    ("dom" if clicked.get("clicked") else ""),
             "error": str(clicked.get("error", ""))[:90]})
        if not clicked.get("clicked"):
            continue
        left = patience
        while len(downloads) == mark and left:
            page.wait_for_timeout(wait_ms)
            left -= 1
        if len(downloads) == mark:
            continue
        page.wait_for_timeout(600)     # let "download all" finish queueing its rest
        new = downloads[mark:]
        mark = len(downloads)
        for d in new:
            fname = (getattr(d, "suggested_filename", "") or (slug + ".stl"))
            fname = fname.replace("/", "_")
            path = os.path.join(DOWNLOADS_DIR, fname)
            try:
                d.save_as(path)
            except Exception as e:
                report["save_error"] = str(e)[:120]
                continue
            if os.path.exists(path) and verify([path], model_url):
                return path
            rejected.append(fname)
    if rejected:
        report["rejected"] = rejected[:6]
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^\w-]", "_", name.lower())[:40]


def _save(url: str, path: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30, stream=True)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)
    return path


def _render_page(url: str, wait_ms: int = 6000) -> dict:
    """Headless-Playwright render of a JS-heavy model page (in a worker thread so
    it's safe to call from sync code). Returns {title, description, imgs[]}."""
    import threading
    import asyncio as _asyncio

    hold = {}

    async def _inner():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(user_agent=HEADERS["User-Agent"],
                                                viewport={"width": 1400, "height": 1000})
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(wait_ms)
                # lazy sections: scroll and stop as soon as the image count stops
                # growing, instead of a fixed 3 x 1500 ms plus a 500 ms settle on
                # a page that finished loading in the first screen
                for _ in range(4):
                    seen = await page.evaluate("() => document.images.length") or 0
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    try:
                        await page.wait_for_function("(n) => document.images.length > n",
                                                     timeout=1200, arg=seen)
                    except Exception:
                        break
                await page.evaluate("window.scrollTo(0, 0)")
                title = await page.title()
                desc = await page.evaluate(
                    "() => { const m = document.querySelector('meta[property=\"og:description\"]')"
                    " || document.querySelector('meta[name=\"description\"]'); return m ? m.content : ''; }"
                )
                imgs = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('img')).map(i => ({"
                    " src: i.currentSrc || i.src || '', w: i.naturalWidth || 0, h: i.naturalHeight || 0 }))"
                )
            finally:
                await browser.close()
            return {"title": title or "", "description": desc or "", "imgs": imgs}

    def _run():
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        try:
            hold["data"] = loop.run_until_complete(_inner())
        except Exception as e:
            hold["error"] = str(e)
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=80)
    if "error" in hold:
        raise RuntimeError(hold["error"])
    return hold.get("data", {"title": "", "description": "", "imgs": []})


def _clean_title(title: str) -> str:
    for sep in [" by ", " | ", " - Thingiverse", " | Download", " - Free", " - 3D"]:
        if sep in title:
            title = title.split(sep)[0]
    return title.strip()


# Creality Cloud builds the SEO title as "<real name> <localised tail> | <category>",
# e.g. the French page (found live 2026-10-09): "Cliqueur moelleux en forme de
# ravioli Fichier STL et 3MF pour l'impression 3D | Autre | Creality Cloud".
# Publishing the title as the listing name would publish that tail, so the
# tail is cut only where a file-format token is followed by a printing word —
# "STL File Holder" (no printing word) is a real name and is left alone.
SEO_FORMAT_RE = re.compile(
    r"\s(?:fichier|archivo|datei|file|файл|arquivo|dosya|bestand|fil|"
    r"stl|3mf)\s*(?:stl|3mf|&|and|y|et|und|и|e)?\s*(?:stl|3mf)?\b", re.I)
SEO_PRINT_RE = re.compile(
    r"(impression|imprim|impresi[oó]n|impress[aã]o|druck|print|печат|"
    r"stampa|druk|utskrift)", re.I)


def _strip_seo_tail(name: str) -> str:
    """The real name out of a Creality SEO title: drop the \"| category\"
    segment, then a localised \"Fichier STL et 3MF pour l'impression 3D\"-style
    tail, then any trailing site suffix."""
    name = (name or "").strip()
    name = name.split("|")[0].strip()
    m = SEO_FORMAT_RE.search(name)
    if m and SEO_PRINT_RE.search(name[m.start():]):
        name = name[:m.start()]
    for suffix in (" - Creality Cloud", " | Creality Cloud", " | Creality"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip(" -–—·:,|")


def _model_name_from(title: str, h1: str = "") -> str:
    """The model's OWN name. The page's h1 is trusted when the SEO title was
    built from it (the h1 is then the title's own prefix: "<name> <tail>");
    the tail-stripped title is the fallback, and the whole title only when
    nothing else is usable. A longer cleaned title that agrees with the
    heading wins, so a heading cut short by the page cannot truncate the name."""
    h1 = (h1 or "").strip()
    title = (title or "").strip()
    cleaned = _strip_seo_tail(title)
    if h1 and 2 < len(h1) <= 120 and title.lower().startswith(h1.lower()):
        name = _strip_seo_tail(h1) or h1
        if cleaned and len(cleaned) > len(name) \
                and cleaned.lower().startswith(name.lower()):
            return cleaned
        return name
    return cleaned or h1 or "Creality Model"


def to_uploadable(path: str) -> str:
    """Return a path the Creality form will actually accept.

    `.avif` (used by some Creality galleries for the cover photo) is not in any
    image input's accept list, so an upload with it silently loses that photo.
    Convert to JPEG in place and hand back the new path; if the bytes cannot be
    decoded at all, return "" so the caller can drop it honestly.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in UPLOAD_IMAGE_EXTS:
        return path
    try:
        from PIL import Image
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            out = os.path.splitext(path)[0] + ".jpg"
            rgb.save(out, "JPEG", quality=92)
    except Exception:
        return ""
    try:
        os.remove(path)
    except OSError:
        pass
    return out


# Metadata that marks an image as machine-generated. Written as plain bytes in
# PNG text chunks and JPEG/XMP packets, so a byte search finds it. Only what is
# a clear label is listed: a C2PA content credential alone is NOT a verdict
# (cameras write those too).
AI_LABEL_MARKERS = (
    b"trainedAlgorithmicMedia",          # IPTC digitalSourceType for AI output
    b"Stable Diffusion", b"ComfyUI", b"Midjourney", b"DALL\xc2\xb7E", b"DALL-E",
    b"Negative prompt", b"parameters\x00", b"Civitai", b"NovelAI",
)


def _ai_label_reason(path: str) -> str:
    """'' when the file carries no AI-generation label; otherwise the label.
    Must run on the ORIGINAL bytes: converting to JPEG drops PNG text chunks."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception:
        return ""
    for m in AI_LABEL_MARKERS:
        if m in data:
            return "carries an AI-generation label (%s)" % m.decode("latin-1")
    return ""


def _flat_render_reason(path: str) -> str:
    """'' for a photograph; otherwise why the image is a flat CAD/slicer-style
    render. A real photo has thousands of distinct colours (sensor noise, light
    falloff, texture). A render of a model on a plain background has few colours
    and a background that covers a large share of the frame. Colours are
    counted at 32 levels per channel on a sample of at most ~256 px a side."""
    try:
        import numpy as np
        img = _imread_any(path)
        if img is None:
            return ""
        h, w = img.shape[:2]
        step = max(1, max(h, w) // 256)
        px = (img[::step, ::step] >> 3).astype(np.int32).reshape(-1, 3)
        codes = px[:, 0] * 1024 + px[:, 1] * 32 + px[:, 2]
        _, counts = np.unique(codes, return_counts=True)
        share = counts.max() / codes.size
        if len(counts) < 300 and share >= 0.4:
            return ("looks like a flat CAD/slicer render (%d colours, the "
                    "plain background covers %d%% of the frame)"
                    % (len(counts), round(share * 100)))
    except Exception:
        return ""
    return ""


# A real surface in a listing photo is either a coloured, grainy surface (wood,
# a mat, a coloured floor) or a near-black backdrop. A slicer/CAD render sits on
# a neutral grey or white bed. Calibrated on the two reference photos the user
# supplied: the desk photo's frame border is ~90% saturated with texture 4.4;
# the slicer screenshot's border has no colour at all, and its grid lines give
# texture 14.2, so texture alone cannot tell them apart.
BACKGROUND_SATURATION = 45        # HSV saturation (0-255) that counts as coloured
BACKGROUND_SURFACE_SHARE = 0.5    # share of the frame border that must be coloured
BACKGROUND_TEXTURE_FLOOR = 1.5    # mean |Laplacian| on the coloured border (grain)


def _background_reason(path: str) -> str:
    """'' when the frame's background is a black backdrop or a coloured, grainy
    surface (a desk, table, mat or floor); otherwise why it is not. The frame
    border is the background. A slicer or CAD render has a neutral grey or white
    bed, so it is rejected whatever its texture."""
    try:
        import cv2
        import numpy as np
        img = _imread_any(path)
        if img is None:
            return ""
        h, w = img.shape[:2]
        step = max(1, max(h, w) // 256)
        small = img[::step, ::step]
        hh, ww = small.shape[:2]
        m = max(2, min(hh, ww) // 10)
        border = np.zeros((hh, ww), bool)
        border[:m, :] = border[-m:, :] = True
        border[:, :m] = border[:, -m:] = True
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        vals = gray[border]
        if float(np.median(vals)) < 45 and float(vals.std()) < 20:
            return ""                                    # a black backdrop
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        sat = hsv[..., 1][border]
        val = hsv[..., 2][border]
        coloured = (sat >= BACKGROUND_SATURATION) & (val >= 40) & (val <= 245)
        share = float(coloured.mean())
        if share < BACKGROUND_SURFACE_SHARE:
            return ("background is a neutral grey or white render bed (%d%% of the "
                    "frame edge is coloured): a slicer/CAD screenshot, not a desk, "
                    "table, floor or black backdrop" % round(share * 100))
        lap = cv2.Laplacian(gray, cv2.CV_32F)
        texture = float(np.abs(lap[border]).mean())
        if texture < BACKGROUND_TEXTURE_FLOOR:
            return ("background is a coloured but smooth surface with no grain or "
                    "sensor noise (a render, not a real surface)")
        return ""                                        # coloured and grainy: a real surface
    except Exception:
        return ""


def _poll(page, budget_ms: int, step_ms: int, cond):
    """Evaluate cond() now and then every `step_ms` until it is truthy or the
    budget runs out; returns the last value it produced.

    This is the difference between waiting for a CONDITION and waiting for a
    NUMBER. The fetch path slept a fixed 8000 ms for the page to render, 3 x 1200
    ms for lazy sections, 5000 ms after an empty gallery read and 950 ms per
    gallery click: 18.6 s of the measured 25.3 s fetch was the code sleeping on a
    clock while the site answered long before (job 20261024-131810). Each of
    those now exits as soon as its own evidence shows up, and keeps the old
    figure as the cap, so a slow page still gets exactly the grace it got before.
    """
    import time
    t0 = time.time()
    got = None
    while True:
        got = cond()
        if got or (time.time() - t0) * 1000.0 >= budget_ms:
            return got
        page.wait_for_timeout(step_ms)


# How many of ONE listing's photos are fetched at the same time. Each is an
# independent HTTP GET followed by an independent decode, so waiting for them in
# a queue buys nothing: eight photos at ~300 ms of CDN latency each cost the run
# 2.4 s of pure waiting on every single model. Overlapping the waiting changes
# no decision — the same audit still judges every photo, and the kept set and
# its order are exactly what the serial loop produced.
IMAGE_FETCH_WORKERS = 6


def _download_images(images: list[str], slug: str, limit: int = 12,
                     strict: bool = True, report: list | None = None) -> list[str]:
    """Download images and keep the usable ones, in the order given.

    strict=True applies the blur/size audit used for opportunistic scans.
    strict=False is for images already proven to be the CREATOR'S OWN gallery
    photos: the blur floor must not throw away a real photo (soft studio shots
    score well under the Laplacian floor while being perfectly good listings).

    Every kept photo is converted to a format the upload form accepts, so the
    set handed to the uploader is exactly the set that can be published.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    def _one(item):
        """Fetch and judge one photo: (path, None) or (None, (url, why))."""
        i, img_url = item
        tag = img_url.split("?")[0][-90:]

        def _drop(why):
            return None, (tag, why)

        ext = img_url.split("?")[0].split(".")[-1].lower()
        if not ext.isalpha() or len(ext) > 4:
            ext = "jpg"
        p = os.path.join(DOWNLOADS_DIR, f"{slug}_img_{i}.{ext}")
        err = ""
        for attempt in range(2):          # one retry: a CDN hiccup is not a missing photo
            try:
                _save(img_url, p)
                err = ""
                break
            except Exception as e:
                err = "download failed: %s: %s" % (type(e).__name__, str(e)[:120])
                time.sleep(1.5) if attempt == 0 else None
        if err:
            return _drop(err)
        try:
            why = ""
            if os.path.getsize(p) <= 1024:
                why = "empty or broken download (under 1 KB)"
            elif _ai_label_reason(p):
                why = "labelled AI-generated"   # never a listing photo
            elif ext not in UPLOAD_IMAGE_EXTS:
                # convert first, then audit the real pixels of the converted file
                p = to_uploadable(p)
                if not p:
                    why = "could not convert to an uploadable format"
            if not why and strict and not _image_quality_ok(p):
                why = "too small or out of focus"
            elif not why and not strict and not _image_quality_ok(p, min_side=200, min_sharpness=0.0):
                why = "under 200 px, or not a readable image"
            if not why:
                why = _flat_render_reason(p) or ""   # a CAD/slicer-style render
            if not why and strict:
                why = _background_reason(p) or ""    # opportunistic scans: not on a desk or black backdrop
            if why:
                if p and os.path.exists(p):
                    os.remove(p)
                return _drop(why)
            return p, None
        except Exception as e:
            return _drop("check failed: %s: %s" % (type(e).__name__, str(e)[:120]))

    want = list(enumerate(images[:limit]))
    workers = max(1, IMAGE_FETCH_WORKERS)
    if len(want) > 1 and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(want))) as pool:
            got = list(pool.map(_one, want))
    else:
        got = [_one(w) for w in want]

    paths = []
    for p, drop in got:                    # index order, exactly as the serial loop
        if drop is not None:
            if report is not None:
                report.append({"url": drop[0], "reason": drop[1]})
        if p:
            paths.append(p)
    return paths


def _imread_any(path: str):
    """Decode an image from its BYTES.

    cv2.imread(path) cannot open a Windows path containing non-ASCII characters
    (a model called "Домкрат для подъема поддонов" produced a path cv2 refused,
    so every photo of that model was judged unreadable and dropped — the upload
    then failed with "No edited images for this model"). Reading the bytes in
    Python and decoding those sidesteps the path encoding entirely, and PIL
    covers formats cv2 cannot decode (e.g. avif).
    """
    import io
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception:
        return None
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
    try:
        import numpy as np
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            rgb = np.array(im.convert("RGB"))
        return rgb[:, :, ::-1].copy()          # RGB -> BGR, like cv2 returns
    except Exception:
        return None


def _image_quality_ok(path: str, min_side: int = 400, min_sharpness: float = 12.0,
                      allow_undecodable: bool = False) -> bool:
    """Reject images that would produce a blurry listing: thumbnails below
    min_side pixels and out-of-focus/flat blobs (Laplacian variance floor).

    allow_undecodable=True keeps images neither OpenCV nor PIL can decode
    instead of silently deleting what may be a real photo."""
    try:
        import cv2
        img = _imread_any(path)
        if img is None:
            return allow_undecodable
        h, w = img.shape[:2]
        if min(h, w) < min_side:
            return False
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return bool(cv2.Laplacian(gray, cv2.CV_64F).var() >= min_sharpness)
    except Exception:
        return True


def _fetch_thingiverse(thing_id: str, slug: str) -> dict:
    from urllib.parse import urlparse, parse_qs, unquote
    data = _render_page(f"https://www.thingiverse.com/thing:{thing_id}")
    name = _clean_title(data["title"]) or f"Thing {thing_id}"
    slug = _slug(name)

    # Thingiverse serves images through resize.thingiverse.com/?url=<real cdn url>.
    # Unwrap to the real cdn.thingiverse.com asset and keep the largest per asset.
    best = {}
    for img in data["imgs"]:
        src = img["src"]
        real = src
        if "resize.thingiverse.com" in src and "url=" in src:
            q = parse_qs(urlparse(src).query)
            if q.get("url"):
                real = unquote(q["url"][0])
        if "cdn.thingiverse.com" not in real:
            continue
        key = re.sub(r"/(thumb|large|preview|featured|card|tiny)[^/]*", "", real)
        area = (img["w"] or 0) * (img["h"] or 0)
        if key not in best or area > best[key][0]:
            best[key] = (area, real)
    images = [s for _a, s in sorted(best.values(), key=lambda x: -x[0])]
    image_paths = _download_images(images, slug)

    # Public zip of all files (no login needed on Thingiverse)
    stl_path = os.path.join(DOWNLOADS_DIR, f"{slug}.zip")
    try:
        _save(f"https://www.thingiverse.com/thing:{thing_id}/zip", stl_path)
        if os.path.getsize(stl_path) < 1024:
            stl_path = None
    except Exception:
        stl_path = None

    return {"name": name, "description": data["description"][:500],
            "stl_path": stl_path, "image_paths": image_paths, "slug": slug}


def _fetch_printables(model_id: str, slug_hint: str) -> dict:
    data = _render_page(f"https://www.printables.com/model/{model_id}")
    name = _clean_title(data["title"]) or f"Model {model_id}"
    slug = _slug(name)

    # Printables gallery images live on media.printables.com under
    # /media/prints/<uuid>/... with several size variants per image. Group by the
    # print uuid and keep the largest variant of each.
    groups = {}
    for img in data["imgs"]:
        src = img["src"]
        if "media.printables.com" not in src:
            continue
        m = re.search(r"/(?:prints|models)/([0-9a-fA-F-]{16,})/", src)
        if not m:
            continue
        uuid = m.group(1)
        area = (img["w"] or 0) * (img["h"] or 0)
        if uuid not in groups or area > groups[uuid][0]:
            groups[uuid] = (area, src)
    images = [s for _a, s in sorted(groups.values(), key=lambda x: -x[0])]
    image_paths = _download_images(images, slug)

    # Printables gates model-file downloads behind login, like MakerWorld — the
    # uploader will fetch the file from the logged-in browser when available.
    return {"name": name, "description": data["description"][:500],
            "stl_path": None, "image_paths": image_paths, "slug": slug}


CDP_URL = "http://127.0.0.1:9222"

# The visible Download control on a MakerWorld model page, measured 2026-10-25:
# a `Download 3MF` label in the Print Files card. The page also carries hidden
# copies of the same label, so the click targets the smallest VISIBLE box rather
# than the first match.
MW_FIND_DOWNLOAD_JS = r"""
(text) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const hits = [...document.querySelectorAll('button,[role=button],a,div,span')]
    .filter((e) => norm(e.innerText).toLowerCase() === text.toLowerCase())
    .map((e) => { const r = e.getBoundingClientRect();
                  return {e, r, area: r.width * r.height}; })
    .filter((x) => x.r.width > 0 && x.r.height > 0);
  if (!hits.length) return null;
  hits.sort((a, b) => a.area - b.area);
  const el = hits[0].e;
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
}
"""


def _mw_download_via_chrome(model_id: str, slug: str) -> str:
    """Download the print file through the user's own logged-in Chrome.

    MakerWorld only hands a design's file to a signed-in visitor, so the
    anonymous headless scrape can read the page but never gets the file — every
    such find used to end as "would not hand out the model file". This clicks the
    page's own Download control instead and keeps whatever the browser saved.
    Returns '' when the page offers no such control.
    """
    import asyncio as _asyncio
    import threading

    from playwright.async_api import async_playwright

    dest = os.path.join(DOWNLOADS_DIR, f"{slug}.3mf")
    holder: dict = {}

    async def _once():
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            page = await (browser.contexts[0]).new_page()
            try:
                await page.goto(f"https://makerworld.com/en/models/{model_id}",
                                wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(6000)
                await page.evaluate("window.scrollTo(0, 1200)")
                await page.wait_for_timeout(1200)
                box = None
                for text in ("Download 3MF", "Download STL", "Download"):
                    box = await page.evaluate(MW_FIND_DOWNLOAD_JS, text)
                    if box:
                        break
                if not box:
                    return ""
                async with page.expect_download(timeout=90000) as dl:
                    await page.mouse.click(box["x"], box["y"])
                await (await dl.value).save_as(dest)
                return dest
            finally:
                await page.close()

    def _run():
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        try:
            holder["path"] = loop.run_until_complete(_once())
        except Exception as exc:  # noqa: BLE001
            holder["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=180)
    if "error" in holder:
        print(f"[makerworld] logged-in download failed: {holder['error']}")
    return holder.get("path", "")


def _mw_html_to_text(value: str) -> str:
    """The source description as text, with no rewriting.

    MakerWorld stores descriptions as HTML (the common one is a <boostme>
    block). A boost block renders as a titled card, so its title is site
    chrome and its <boostcontent> is the designer's own text (measured
    2026-10-25 against the source listing).
    """
    m = re.search(r"(?is)<boostcontent>(.*?)</boostcontent>", value or "")
    if m:
        value = m.group(1)
    t = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6]|blockquote)>", "\n", value or "")
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _mw_api_design(model_id: str) -> dict:
    """The public design API (no login) gives the source's own metadata:
    title, authored description, categories, tags and licence. The page's
    og:description is a search-engine blurb, so it is only the fallback."""
    try:
        r = requests.get(
            f"https://makerworld.com/api/v1/design-service/design/{model_id}",
            headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return {}
        d = r.json()
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    lic = str(d.get("license") or "").strip()
    if re.fullmatch(r"(?i)by(?:-(?:nc|sa|nd)){0,2}", lic):
        lic = "CC " + lic
    elif re.fullmatch(r"(?i)cc0|0", lic):
        lic = "CC0"
    creator = d.get("designCreator") or {}
    creator_name = (creator.get("name") if isinstance(creator, dict) else "") or ""
    description = _mw_html_to_text(d.get("summary") or "")
    if not description and creator_name:
        # MakerWorld's own line at the top of a listing with no authored
        # description — not an invented blurb.
        description = f"Download this free 3D print file designed by {creator_name}."
    return {
        "title": str(d.get("title") or "").strip(),
        "description": description,
        "license_raw": lic,
        "categories": [c.get("name") for c in (d.get("categories") or [])
                       if isinstance(c, dict) and c.get("name")],
        "tags": [t for t in (d.get("tags") or []) if isinstance(t, str) and t.strip()],
        "creator": creator_name,
    }


def _fetch_makerworld(model_id: str) -> dict:
    """Use Playwright in a background thread to fully render the MakerWorld page and scan every image."""
    import threading
    import asyncio as _asyncio

    result_holder = {}

    async def _scrape():
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(user_agent=HEADERS["User-Agent"])
                page = await context.new_page()

                await page.goto(
                    f"https://makerworld.com/en/models/{model_id}",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                # MakerWorld renders its gallery lazily and its analytics traffic
                # means networkidle never fires, so wait for the images THEMSELVES
                # to arrive — capped at the 5 s this used to sleep unconditionally,
                # plus one poll after scrolling for anything below the fold.
                try:
                    await page.wait_for_function("() => document.images.length >= 6",
                                                 timeout=5000)
                except Exception:
                    pass
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    _n = await page.evaluate("() => document.images.length") or 0
                    await page.wait_for_function("(k) => document.images.length > k",
                                                 timeout=1500, arg=_n)
                except Exception:
                    pass
                await page.evaluate("window.scrollTo(0, 0)")

                # Page title → model name (strip MakerWorld's SEO suffixes)
                title = await page.title()
                for suffix in [" - Free 3D Print Model - MakerWorld", " - 3D Print Model - MakerWorld",
                               " - Free 3D Print Model", " - MakerWorld", " | MakerWorld"]:
                    title = title.replace(suffix, "")
                name = title.strip() or f"Model {model_id}"

                # Meta description
                description = await page.evaluate("""
                    () => {
                        const m = document.querySelector('meta[property="og:description"]')
                               || document.querySelector('meta[name="description"]');
                        return m ? m.content : '';
                    }
                """) or ""

                # The licence this design is actually published under. MakerWorld
                # renders it as a Creative Commons link rather than as text, so the
                # anchor is the primary reading and a CC name in the page text is
                # only the fallback.
                license_raw = await page.evaluate("""
                    () => {
                        const links = [...document.querySelectorAll('a[href]')]
                            .map(a => a.href)
                            .filter(h => /creativecommons\\.org|\\/license/i.test(h));
                        if (links.length) return links.slice(0, 3).join(' ');
                        const m = (document.body.innerText || '')
                            .match(/(CC\\s?0|CC[\\s-]?BY(?:[\\s-]?(?:NC|SA|ND)){0,2}|Standard Digital File License)/i);
                        return m ? m[0] : '';
                    }
                """) or ""

                # Collect EVERY <img> src on the page along with its natural size
                raw_imgs = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('img')).map(img => ({
                        src: img.currentSrc || img.src || '',
                        w: img.naturalWidth || img.width || 0,
                        h: img.naturalHeight || img.height || 0
                    }))
                """)

            # The real product gallery images on MakerWorld live under the
            # ".../model/<code>/design/..." path. Everything else (avatars under
            # /user/ or /avatar/, review photos under /ratings/, comment photos
            # under /comment/, print-profile thumbnails under /instance/, and
            # store filament images on store.bblcdn.com) is page chrome we skip.
            #
            # The page ALSO embeds /design/ images for recommended/related models
            # (e.g. a different maker's design). Those belong to a *different*
            # model code, so we keep only images whose code matches THIS model's
            # gallery — i.e. the dominant code (the one with the most images).
                import re as _re
                from collections import Counter as _Counter

                design_imgs = []  # (code, key, area, src)
                code_counts = _Counter()
                for img in raw_imgs:
                    src = img["src"]
                    if not src.startswith("http") or "/design/" not in src:
                        continue
                    m = _re.search(r"/model/([^/]+)/design/", src)
                    code = m.group(1) if m else None
                    if not code:
                        continue
                    key = src.split("?")[0]  # path without the resize query
                    area = (img["w"] or 0) * (img["h"] or 0)
                    design_imgs.append((code, key, area, src))
                    code_counts[code] += 1

                product_imgs = []
                if code_counts:
                    main_code = code_counts.most_common(1)[0][0]
                    # Dedupe the main model's images (1000x1000 vs 400x400 share a path)
                    best = {}  # key -> (area, src)
                    for code, key, area, src in design_imgs:
                        if code != main_code:
                            continue
                        if key not in best or area > best[key][0]:
                            best[key] = (area, src)
                    # Download originals (strip OSS resize param for full resolution)
                    product_imgs = [src.split("?")[0] for _area, src in best.values()]
            finally:
                await browser.close()
            return {"name": name, "description": description, "images": product_imgs,
                    "license_raw": license_raw}

    def _run():
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        try:
            result_holder["data"] = loop.run_until_complete(_scrape())
        except Exception as e:
            result_holder["error"] = str(e)
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=60)

    if "error" in result_holder:
        raise RuntimeError(f"MakerWorld scrape failed: {result_holder['error']}")

    data = result_holder.get("data", {})
    # The API's metadata wins over the page scrape: the scrape's description is
    # the og:description blurb, while the API carries the description the
    # designer actually wrote. The page is still what supplies the gallery.
    meta = _mw_api_design(model_id)
    name = meta.get("title") or data.get("name", f"Model {model_id}")
    slug = _slug(name)
    description = meta.get("description") or data.get("description", "")
    license_raw = meta.get("license_raw") or data.get("license_raw", "")
    images = data.get("images", [])

    print(f"[makerworld] found {len(images)} images on page")

    image_paths = []
    for i, img_url in enumerate(images):
        ext = img_url.split(".")[-1].split("?")[0] or "jpg"
        if not ext.isalpha() or len(ext) > 4:
            ext = "jpg"
        p = os.path.join(DOWNLOADS_DIR, f"{slug}_img_{i}.{ext}")
        try:
            _save(img_url, p)
            if _image_quality_ok(p):
                image_paths.append(p)
            else:
                os.remove(p)
        except Exception:
            pass

    # Attempt file download
    stl_path = None
    try:
        api_url = f"https://makerworld.com/api/v1/design-bom-file/download?designId={model_id}"
        r = requests.get(api_url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and r.content[:2] == b"PK":
            stl_path = os.path.join(DOWNLOADS_DIR, f"{slug}.zip")
            with open(stl_path, "wb") as f:
                f.write(r.content)
    except Exception:
        pass

    if not stl_path:
        stl_path = _mw_download_via_chrome(model_id, slug) or None
        if stl_path:
            print(f"[makerworld] file came from the logged-in Chrome: {stl_path}")

    return {"name": name, "description": description, "stl_path": stl_path,
            "image_paths": image_paths, "slug": slug, "license_raw": license_raw,
            "categories": meta.get("categories") or [],
            "tags": meta.get("tags") or [],
            "creator": meta.get("creator", "")}


def _fetch_generic(url: str) -> dict:
    """Try to download the URL directly as an STL file."""
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path) or "model"
    slug = _slug(os.path.splitext(filename)[0])
    stl_path = os.path.join(DOWNLOADS_DIR, f"{slug}.stl")
    try:
        _save(url, stl_path)
    except Exception:
        stl_path = None
    return {"name": filename, "description": "", "stl_path": stl_path, "image_paths": [], "slug": slug}


def cc_code_from_text(text: str) -> str:
    """The CC licence NAMED in a piece of text: "CC BY-NC-SA 4.0",
    "creativecommons.org/licenses/by/4", "ccby", "CC0" → 'by-nc-sa', 'by',
    'cc0'. Returns '' when the text names no CC licence (Creality's own
    standard-license paragraph names none, so it comes back empty)."""
    s = (text or "").lower()
    um = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z0-9\-]+)", s)
    if um:
        c = um.group(1)
        return "cc0" if c in ("zero", "mark", "publicdomain") else c
    s = re.sub(r"cc\s*[-_]?\s*by", "cc by", s)      # "ccby", "cc-by" → "cc by"
    s = re.sub(r"cc\s*[-_]?\s*0\b", "cc0", s)
    toks = re.findall(r"[a-z0-9]+", s)
    for i, tk in enumerate(toks):
        if tk == "cc0" or (tk == "cc" and i + 1 < len(toks) and toks[i + 1] == "0"):
            return "cc0"
        if tk == "cc" and i + 1 < len(toks) and toks[i + 1] == "by":
            attrs = []
            for nxt in toks[i + 2:i + 5]:
                if nxt in ("nc", "sa", "nd"):
                    if nxt not in attrs:
                        attrs.append(nxt)
                else:
                    break
            order = [a for a in ("nc", "sa", "nd") if a in attrs]
            return "by" + ("-" + "-".join(order) if order else "")
    return ""


def code_from_icons(icons) -> str:
    """The CC licence drawn as an icon strip (cc.svg + by.svg + nc.svg …)."""
    if not icons:
        return ""
    try:
        from uploader import badge_from_icons
        return cc_code_from_text(badge_from_icons(icons))
    except Exception:
        return ""


def classify_license(raw: str, icons=None) -> dict:
    """Classify a Creality Cloud license string/badge code for re-upload
    safety. The upload form itself forbids No-Derivatives sources (CC BY-ND,
    CC BY-NC-ND) for remixes, so those are refused before any upload. The
    NonCommercial sources (CC BY-NC, CC BY-NC-SA) are refused as well: this
    account publishes on a platform that rewards uploads, which is not the
    "noncommercial use" an NC source licenses — NC re-uploads are what got
    taken down on 2026-10-09 ("The model did not pass platform review").

    Reads every signal Creality's page gives — the CC link, the licence NAMED
    in the card's own text, the licence ICON badge — and gives them precedence
    over the boilerplate that sits on EVERY model page:
      · "This work is licensed under the Copyright License 4.0."  (site line)
      · the site-wide Standard-License paragraph
    A licensed (CC) source therefore can no longer be refused just because the
    boilerplate was the text that got read, and a Creality Standard-License
    source is still refused, with the exact sentence it was read from.
    Returns {raw, code, nd, nc, restricted, ok_for_reupload, note}.
    """
    t = (raw or "").lower().strip().replace("_", "-")
    m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z0-9\-]+)", t)
    code = m.group(1) if m else ""
    # not every licence the page shows links to creativecommons.org (the badge
    # is often drawn as icons, or the name is text), so read those too
    named_code = cc_code_from_text(t)
    icon_code = code_from_icons(icons)
    if not code:
        code = named_code or icon_code

    # ND must catch CC BY-NC-ND too, not just CC BY-ND: the old token check
    # ("by-nd") silently missed "by-nc-nd", so an ND source could be published.
    # ND is decided by the licence's IDENTITY too, when the page gives one: the
    # form's Model-Source tip literally says "licenses such as CC BY-ND and CC
    # BY-NC-ND do not allow for derivative works", and reading an ND attribute
    # off that sentence refused a genuinely CC BY source (2026-10-09). With no
    # identity at all, the words still decide — an ND page that names no licence
    # must never be treated as remixable.
    nd = ("-nd" in code) if code else bool(nd_re.search(t))
    # NC is decided by the licence's IDENTITY (the CC link / the name in the
    # card / the icon badge) whenever we have one, so a page that merely
    # MENTIONS an NC licence — the form's Model-Source tip names "CC BY-NC-ND"
    # as an example — cannot turn a CC BY source noncommercial. Only when the
    # page names no licence at all do the words decide.
    nc = ("-nc" in code) if code else bool(
        any(tok in t for tok in ("by-nc", "noncommercial", "non-commercial")))

    # ---- which licence is this? (evidence, strongest first)
    evidence = []
    if m:
        evidence.append("the creativecommons.org link")
    if named_code:
        evidence.append("the licence named in the card ("
                        + named_code.upper().replace("BY", "CC BY") + ")")
    if icon_code:
        evidence.append("the licence icon badge ("
                        + icon_code.upper().replace("BY", "CC BY") + ")")
    if re.search(r"this license allows reusers|no rights reserved"
                 r"|public dedication|cc0 \(aka cc zero\)"
                 # French rendering of the same CC grant paragraph
                 r"|cette licence autorise les r[ée]utilisateurs", t):
        evidence.append("the CC grant paragraph")

    # Creality's OWN licence: "3D model or any portion of the model … may not be
    # shared, transmitted, redistributed, remixed, duplicated, or sold." Read
    # that sentence, and quote it, so an honest refusal can be checked.
    std_block = (re.search(r"sold on creality cloud are available under a standard license", t)
                 or re.search(r"may\s+not\s+be\s+(shared|sold|transmitted|redistributed)", t)
                 or ("redistributed" in t and "remixed" in t)
                 or re.search(r"personal\s+use\s+only", t)
                 # French rendering of the same Standard-License block
                 or re.search(r"ne peuvent pas [êe]tre partag[ée]s|usage personnel exclusivement", t))
    site_line = bool(re.search(r"this work is licensed under", t))
    quoted = ""
    if std_block:
        # the sentence that forbids the remix is the useful one to quote
        for pat in (r"[^.\n]{0,90}may\s+not\s+be\s+shared[^.]{0,140}",
                    r"[^.\n]{0,90}personal\s+use\s+only[^.]{0,90}",
                    r"[^.\n]{0,90}available under a standard license[^.]{0,90}"):
            q = re.search(pat, t)
            if q:
                quoted = q.group(0)
                break
        quoted = (quoted or std_block.group(0)).strip().strip(".,")

    restricted = bool(std_block and not evidence)
    unknown = bool(not evidence and not std_block)
    if restricted:
        nd = True
        nc = True

    # unknown: refuse rather than guess. ND cannot be remixed at all; NC may not
    # be remixed for a platform that rewards the upload — both are refused here,
    # which is what the finder's allow-list now mirrors.
    ok = bool(evidence) and not nd and not nc
    note = ""
    if restricted:
        note = ("Source is published under Creality's Standard (Copyright) "
                "License 4.0 — it forbids redistribution and remixing, so it "
                "cannot be re-uploaded as a remix. Pick a CC-licensed source "
                "(CC0 / CC BY / CC BY-SA).")
        if quoted:
            note += " Read from the page: \u201c" + quoted[:160] + "\u201d"
    elif unknown:
        note = ("This model's licence could NOT be read from the page"
                + (" (it shows only Creality's boilerplate line" if site_line
                   else " (no licence card was found)")
                + "), so it was not treated as a CC source — nothing is "
                "uploaded until the licence is known. Open the link and check"
                " it, then fetch it again.")
    elif nd:
        note = ("No-Derivatives license (" + (code or "ND") + ") — remix "
                "re-upload is not allowed (the upload form itself rejects ND "
                "sources).")
    elif nc:
        note = ("NonCommercial license (" + (code or "NC").upper()
                .replace("BY", "CC BY") + ") — it allows sharing and remixing "
                "for NONCOMMERCIAL use only, and this listing is published on a "
                "platform that rewards uploads, so the licence does not cover "
                "it. Pick a CC0 / CC BY / CC BY-SA source.")
    elif code == "cc0" or "public domain" in t or "publicdomain" in t:
        note = "CC0 / public domain — no attribution required (still credited)."
    else:
        note = ("CC source" + (" " + code.upper().replace("BY", "CC BY") if code else "")
                + " — remix allowed (" + "; ".join(evidence) + "); the source "
                "link is declared in the listing (Remix Models).")
    return {"raw": (raw or "")[:600], "code": code, "nd": nd, "nc": nc,
            "restricted": restricted, "unknown": unknown,
            "evidence": evidence, "quote": quoted[:200],
            "ok_for_reupload": ok, "note": note}


def _fetch_creality_api(url: str):
    """Read the listing from the page's own payload and API. None = use the DOM.

    Returns the same record shape as `_fetch_creality_dom`. It returns None —
    never a guess — whenever the answer is not complete enough to publish on: an
    unread field, no decisive licence, no photos, no file. The caller then runs
    the rendered-page pass once.

    RuntimeError is raised only when a slower second read cannot change the
    answer: the listing is gone, the browser is signed out, or the licence is
    refused by `classify_license` — the same judge the DOM path uses.
    """
    import creality_api as cc
    from browser_opener import sync_playwright_auto
    from uploader import ensure_browser, CDP_URL, UPLOAD_VERIFY, _verify_model_files

    model_url = _canon_model_url(url)
    if not ensure_browser():
        raise RuntimeError("Creality Cloud needs the logged-in debug Chrome: "
                           "start it once (debug port 9222), log in, then retry.")
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    mark = timing.Steps("fetch.api")
    try:
        with sync_playwright_auto() as pw:
            ctx = pw.chromium.connect_over_cdp(CDP_URL).contexts[0]
            rec = cc.get_reader(ctx).read(model_url, dest_dir=DOWNLOADS_DIR)
    except Exception:
        return None
    mark("read")

    if rec.get("signin"):
        raise RuntimeError("The Creality session is signed out in the debug Chrome — "
                           "log in there, then retry.")
    if rec.get("dead"):
        raise RuntimeError("The Creality listing is gone (the page says %r) — "
                           "nothing to publish." % (rec.get("h1") or "Page Not Found"))

    lic = classify_license(rec.get("license_raw") or "")
    UPLOAD_VERIFY["license_text"] = (rec.get("license_raw") or "")[:600]
    UPLOAD_VERIFY["license_evidence"] = lic.get("evidence")
    UPLOAD_VERIFY["license_icons"] = []      # this read draws no badge
    if not (lic.get("evidence") or lic.get("restricted")):
        return None            # undecided: the licence card on the page decides
    if not lic.get("ok_for_reupload"):
        raise RuntimeError("Licence refused — %s" % (lic.get("note") or lic.get("raw")))
    if rec.get("unknown") or not rec.get("stl_path") or not rec.get("covers"):
        return None

    name = (rec.get("name") or "").strip()
    slug = _slug(name)
    desc = (rec.get("description")
            or _plain_text(rec.get("description_html") or ""))[:500]
    image_paths = _download_images(list(rec["covers"]), slug, strict=False)
    mark("gallery")
    if not image_paths:
        return None                     # the rendered gallery may still have them
    if not _verify_model_files([rec["stl_path"]], model_url):
        raise RuntimeError("The downloaded file from Creality Cloud is not real "
                           "model geometry (3MF/STL check failed) — not uploading junk.")
    mark("file")

    gallery = {"scope": "api:covers", "images": len(rec["covers"]),
               "downloaded": len(image_paths), "found_by": ["api"],
               "paths": [u.replace("https://pic2-cdn.creality.com", "")[:80]
                         for u in rec["covers"][:12]]}
    UPLOAD_VERIFY["gallery"] = gallery
    UPLOAD_VERIFY["read_via"] = "api"
    return {"name": name, "description": desc, "stl_path": rec["stl_path"],
            "image_paths": image_paths, "slug": slug,
            "author": rec.get("author") or "", "license": lic,
            "source_url": model_url,
            "file_attempt": {"control": "api:3mfDownload", "downloads": 1,
                             "api": {k: rec.get(k) for k in
                                     ("files_ms", "three_d_ms", "download_url_ms")},
                             "bytes": (rec.get("file") or {}).get("bytes")},
            "signin_required": False, "gallery": gallery, "read_via": "api"}


def _plain_text(html: str) -> str:
    """The model node's HTML description as one plain line."""
    s = re.sub(r"<[^>]+>", " ", html or "")
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _fetch_creality(url: str) -> dict:
    """A Creality Cloud fetch: the API read first, the rendered page as fallback.

    `_fetch_creality_api` answers in ~2-3 s from the page's own payload and
    endpoints. `_fetch_creality_dom` is the slow path it replaces; it runs only
    when the API read could not prove a field the publish depends on.
    """
    from uploader import UPLOAD_VERIFY
    _mark = timing.Steps("fetch")
    try:
        got = _fetch_creality_api(url)
    except RuntimeError:
        raise
    except Exception:
        got = None
    if got is not None:
        return got
    _mark("dom_fallback")
    UPLOAD_VERIFY["read_via"] = "dom"
    return _fetch_creality_dom(url)


def _fetch_creality_dom(url: str) -> dict:
    """Fetch a Creality Cloud model by driving the rendered page.

    This is the fallback half of `_fetch_creality`: metadata + original photos +
    the real 3MF file, read from a logged-in Chrome over CDP. It is the slow path
    (25.3 s measured) and stays exactly as it was, because everything it proves
    on screen — the licence card, the gallery, the download dialog — is the
    evidence a fallback exists for.
    """
    from browser_opener import sync_playwright_auto
    from uploader import ensure_browser, CDP_URL, UPLOAD_VERIFY

    # Per-step timings. main.py times the whole fetch, so without these marks a
    # slow fetch is one number with nothing to point at.
    _mark = timing.Steps("fetch")

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    if not ensure_browser():
        raise RuntimeError("Creality Cloud needs the logged-in debug Chrome: "
                           "start it once (debug port 9222), log in, then retry.")

    model_url = _canon_model_url(url)                 # always the non-locale model URL

    data = {"title": "", "desc": "", "author": "", "license": "", "imgs": []}
    stl_path = None
    gallery_info: dict = {}    # filled gallery-scoped
    lic_icons: list = []       # the licence badge's icon strip, if the page draws one
    with sync_playwright_auto() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        downloads = []
        page.on("download", lambda d: downloads.append(d))
        try:
            _mark("connect")
            page.goto(model_url, wait_until="domcontentloaded", timeout=60000)
            # WAIT FOR THE MODEL PAGE, not just any response: a fetch fired right
            # after discovery serves the login/Cloudflare wall first (2026-10-09:
            # five run fetches each read licence='' because the wall was all the
            # page had). The page is only believed when an h1/inner text with the
            # model slug's words shows up — or after ~30s, when we go with what
            # we have rather than hang.
            import time as _t0_time
            _t0 = _t0_time.time()
            while _t0_time.time() - _t0 < 30:
                _probe = page.evaluate(
                    "() => ({t: document.title || '', h1: (document.body.innerText || '').slice(0, 800)})") or {}
                if "crealitycloud.com" in (page.url or "") and _probe.get("h1", ""):
                    break
                page.wait_for_timeout(400)
            # Then wait for the SPA to have RENDERED something usable, not for a
            # fixed 8 s: the model title plus the first images. A listing that is
            # ready in 900 ms used to cost 8000 ms of sitting still, on every
            # model, and a slow one still gets the whole 8 s of grace.
            try:
                page.wait_for_function(
                    """() => {
                        const h = document.querySelector('h1');
                        return !!((h && (h.innerText || h.textContent) || '').trim())
                               && document.images.length >= 3;
                    }""", timeout=8000)
            except Exception:
                pass
            # Scroll to the bottom and let the lazy sections appear: ask whether
            # the image count is still growing instead of sleeping 1200 ms three
            # times over. A page that stops loading after one screen costs one
            # poll, and the loop is still capped.
            seen = 0
            for _ in range(6):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    page.wait_for_function("(n) => document.images.length > n",
                                           timeout=1200, arg=seen)
                except Exception:
                    pass
                n = page.evaluate("() => document.images.length") or 0
                if n == seen:
                    break
                seen = n
            page.evaluate("window.scrollTo(0, 0)")

            data = page.evaluate("""
                () => {
                    const body = document.body.innerText || '';
                    const html = document.documentElement.innerHTML || '';
                    const meta = (sel) => { const m = document.querySelector(sel);
                                            return m ? m.content : ''; };
                    // author: the line right before a standalone 'Follow'
                    let author = '';
                    const am = body.match(/(?:^|\\n)([^\\n]{2,32})\\n\\s*Follow(?:\\n|$)/);
                    if (am) author = am[1].trim();
                    // the licence is read separately (LICENSE_JS) so the
                    // pipeline and the inspector script can never disagree
                    const imgs = [];   // creator photos are collected gallery-scoped below
                    // the page's own name heading (the SEO title is built FROM
                    // it and carries a localised tail that must not be published)
                    const h1el = document.querySelector('h1');
                    const h1 = h1el ? (h1el.innerText || h1el.textContent || '').trim() : '';
                    return { title: document.title, h1,
                             desc: meta('meta[property="og:description"]')
                             || meta('meta[name="description"]'),
                             author, license: '', imgs };
                }
            """)

            _mark("page")
            # ---- the model's own licence card (see LICENSE_JS). The card is
            # rendered in pieces, so it is read up to three times, waiting
            # between tries, and the first read that actually NAMES a licence
            # (or shows Creality's Standard-License paragraph) wins. A single
            # empty/boilerplate read must never decide a licence.
            lic_d = {}                 # the best read so far
            lic_text = ""
            lic_probe = {}
            for attempt in range(3):
                try:
                    raw_lic = page.evaluate(LICENSE_JS)
                    got = json.loads(raw_lic) if isinstance(raw_lic, str) else (raw_lic or {})
                except Exception:
                    got = {}
                text = " ".join(x for x in [(got.get("cc_url") or ""),
                                            (got.get("card") or "")] if x).strip()
                probe = classify_license(text, got.get("icons"))
                # keep the read that actually decides something (a named licence
                # or Creality's own restriction), else the richest text
                if (probe.get("evidence") or probe.get("restricted")
                        or len(text) > len(lic_text)):
                    lic_d, lic_text, lic_probe = got, text, probe
                if probe.get("evidence") or probe.get("restricted"):
                    break                       # the licence is named — done
                if attempt < 2:
                    page.mouse.wheel(0, 600)
                    page.wait_for_timeout(1500)
            data["license"] = lic_text
            UPLOAD_VERIFY["license_text"] = data["license"][:600]
            UPLOAD_VERIFY["license_evidence"] = lic_probe.get("evidence")
            lic_icons = list(lic_d.get("icons") or [])
            UPLOAD_VERIFY["license_icons"] = lic_icons[:6]

            _mark("licence")
            # ---- gallery photos: ONLY the model's own gallery images.
            # Measured layout (inspector): the model's gallery block is
            #   div.model-gallery > div.swiper-thumb        (the thumbnail strip)
            #                  > div.swiper-top-content > div.swiper-cover  (slides)
            # and each photo sits in a div.cover (slide) or div.thumb (strip).
            # Other /comp/model/ images on the page are NOT photos:
            #   div.creality-model-cover inside div.print-config-comp / div.list-3mf
            #     — the slicer PLATE previews ("52m | 1 plate | 24.70g")
            #   related/recommended model cards, avatars, comment pictures
            # So the strip is read first, the slides only from inside
            # div.model-gallery, and every rule rejects the plate/related rails.
            # The strip's next-arrow is then clicked so thumbnails paged behind
            # it are collected too.
            gallery_js = r"""
                () => {
                    // Host/path rules, re-measured live 2026-10-09:
                    //   model photos : .../crealityCloud/upload/…, .../comp/model/…
                    //                  AND .../crealityCloud/common/… — the
                    //                  dumpling page keeps SIX of its seven
                    //                  photos under /common/, so calling that
                    //                  folder "junk" is what dropped 6 of 7
                    //                  photos and produced "1 image out of six"
                    //   never a photo : /faces/, /crealityEmoji/,
                    //                  /crealityWebStaticImg/, logos, no-folder
                    //                  placeholders — and, outside the gallery,
                    //                  anything in a promo/banner container
                    const isCreator = (s) => {
                        const t = (s || '').split('?')[0];
                        if (t.indexOf('/crealityCloud/upload/') >= 0) return true;
                        if (t.indexOf('/comp/model/') >= 0) return true;
                        return false;
                    };
                    // a photo OF THIS MODEL: the cover folder AND /common/
                    // (measured: /common/ holds real gallery photos)
                    const isGalleryPhoto = (s) => isCreator(s) ||
                        (s || '').split('?')[0].indexOf('/crealityCloud/common/') >= 0;
                    // folders that NEVER hold a model photo, whatever the scope
                    const isNonPhoto = (s) => {
                        const t = (s || '').split('?')[0];
                        return t.indexOf('/faces/') >= 0 ||
                               t.indexOf('/crealityEmoji/') >= 0 ||
                               t.indexOf('/crealityWebStaticImg/') >= 0 ||
                               t.indexOf('no-folder') >= 0 ||
                               t.toLowerCase().indexOf('/logo') >= 0;
                    };
                    // avatars/comments/reviews/sponsored rails are never photos
                    // — and neither are the slicer PLATE previews (the
                    // "52m | 1 plate | 24.70g" cards inside list-3mf) nor
                    // related-model covers, which are also /comp/model/ images
                    const REJECT = ['avatar', 'comment', 'review', 'adv',
                                    'related', 'recommend', 'sponsor',
                                    'creality-model-cover', 'print-config',
                                    'list-3mf'];
                    // containers that only ever hold promos — applied ONLY by the
                    // page-wide fallback, never against the gallery itself
                    const PROMO = ['banner', 'promo', 'gift', 'coupon',
                                   'activity', 'notice', 'advert', 'float'];
                    const badWith = (el, words) => {
                        let e = el;
                        for (let i = 0; i < 10 && e; i++) {
                            const c = (e.className || '').toString();
                            if (words.some(r => c.indexOf(r) >= 0)) return true;
                            e = e.parentElement;
                        }
                        return false;
                    };
                    const bad = (el) => badWith(el, REJECT);
                    const badPromo = (el) => badWith(el, PROMO);
                    // avatars carry a <=99px resize; gallery thumbs are 240-400px
                    const isTinyVariant = (s) =>
                        /resize[,/]+(?:h|w)_(?:[1-9]|[1-9][0-9]),/i.test(s);
                    // the rules as predicates, so one image can be asked for the
                    // first URL that passes all of them (see goodSrc)
                    const notNonPhoto = (s) => !isNonPhoto(s);
                    const notTiny = (s) => !isTinyVariant(s);
                    const out = [];
                    const how = [];
                    // Every caller filters first; `add` stores the URL WITHOUT
                    // its query, which is the full-size original the CDN serves
                    // (the rendered thumbnail is a resize,w_40 variant).
                    // `how` records WHICH rule found each photo: a run that
                    // reported "0 photo(s)" said nothing about why, because the
                    // evidence of what the page held was never kept.
                    const add = (s, tag) => {
                        const key = (s || '').split('?')[0];
                        if (key && out.indexOf(key) < 0) {
                            out.push(key);
                            how.push(tag || '?');
                        }
                    };
                    // Absolutise a lazy attribute value (data-src is often a bare
                    // path) so a photo found in an attribute downloads like one
                    // found in src.
                    const absu = (v) => {
                        if (!v) return '';
                        const s = v.trim();
                        // no backslashes in this test: scripts/check_gallery_js.js
                        // reads the Python string raw, so an escape would differ
                        if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return s;
                        if (s.indexOf('//') === 0) return 'https:' + s;
                        try { return new URL(s, document.baseURI).href; }
                        catch (e) { return s; }
                    };
                    // EVERY photo URL one element carries. A lazy <img> keeps a
                    // 1x1 transparent gif (or a site placeholder) in src and the
                    // real URL in data-src / srcset / a <picture> <source>:
                    // reading only the FIRST attribute present looked at the
                    // placeholder, failed the folder rule, and three listings on
                    // 2026-10-24 were skipped as "the listing showed 0 photo(s),
                    // 0 usable" while their files downloaded fine. So each image
                    // contributes all of its URLs and the folder rules below
                    // choose which one is the photo.
                    const srcOf = (im) => {
                        const list = [];
                        const push = (v) => { const u = absu(v); if (u) list.push(u); };
                        push(im.currentSrc); push(im.getAttribute('src'));
                        for (const a of ['data-src', 'data-original', 'data-actualsrc',
                                         'data-lazy-src', 'data-image', 'data-url'])
                            push(im.getAttribute(a));
                        const pict = im.closest ? im.closest('picture') : null;
                        const sources = pict ? [...pict.querySelectorAll('source')] : [];
                        for (const el of [im, ...sources]) {
                            const ss = el.getAttribute('srcset')
                                    || el.getAttribute('data-srcset');
                            if (!ss) continue;
                            let best = '', bestW = -1;
                            for (const part of ss.split(',')) {
                                const bits = part.trim().split(/\s+/);
                                if (!bits[0]) continue;
                                const w = parseInt((bits[1] || '1w').replace(/w$/i, ''), 10) || 1;
                                if (w > bestW) { bestW = w; best = bits[0]; }
                            }
                            push(best);
                        }
                        return list;
                    };
                    // the element's URLs that pass a step's folder rules
                    const goodSrc = (im, rules) => {
                        for (const s of srcOf(im)) {
                            if (rules.every(r => r(s))) return s;
                        }
                        return '';
                    };
                    // a CSS background-image is a photo too — some Creality
                    // layouts paint the cover as a div's background, never an <img>
                    const bgSrc = (root) => {
                        const found = [];
                        const els = [...root.querySelectorAll('div,span,a,li,figure')].slice(0, 500);
                        for (const el of els) {
                            let bi = '';
                            try { bi = getComputedStyle(el).backgroundImage || ''; }
                            catch (e) { continue; }
                            if (bi.indexOf('url(') < 0) continue;
                            const m = bi.match(/url\(["']?([^"')]+)["']?\)/);
                            if (m) found.push(absu(m[1]));
                        }
                        return found;
                    };
                    // a small LOADED image is a thumbnail, not a photo — but the
                    // element's naturalWidth describes the bitmap it shows, which
                    // for a lazy image is the placeholder, while the real photo
                    // sits in srcset/data-src. So the size floor only applies when
                    // the URL we chose IS the one being displayed.
                    const displayedSmall = (im, s) => {
                        const nw = im.naturalWidth || 0;
                        if (!nw || nw >= 200) return false;
                        return s === absu(im.getAttribute('src'))
                            || (im.currentSrc && s === absu(im.currentSrc));
                    };
                    // 1) THE STRIP — what the inspector shows: every thumbnail
                    const strip = document.querySelector('.swiper-thumb') ||
                        [...document.querySelectorAll('[class*=thumb]')]
                            .find(e => e.querySelectorAll('img').length >= 2) || null;
                    let stripCount = 0;
                    if (strip) {
                        const imgs = [...strip.querySelectorAll('img')];
                        stripCount = imgs.length;
                        for (const im of imgs) {
                            if (bad(im)) continue;
                            const s = goodSrc(im, [isGalleryPhoto, notNonPhoto, notTiny]);
                            if (s) add(s, 'strip');
                        }
                    }
                    // 2) the gallery SLIDES (full-res twins of the strip
                    // thumbnails), read ONLY from inside the model's own
                    // gallery block — never from the plate/related rails.
                    let galleryRoot = document.querySelector('div.model-gallery');
                    if (!galleryRoot && strip) {
                        // unusual layout: walk up from the strip to the first
                        // ancestor that also holds a gallery slide (div.cover)
                        let e = strip.parentElement;
                        for (let i = 0; i < 6 && e; i++) {
                            if (e.querySelector('div.cover, div.thumb')) {
                                galleryRoot = e; break;
                            }
                            e = e.parentElement;
                        }
                    }
                    if (!galleryRoot) galleryRoot = document.querySelector('.model-content');
                    if (galleryRoot) {
                        for (const im of galleryRoot.querySelectorAll('img')) {
                            if (bad(im)) continue;
                            const s = goodSrc(im, [isGalleryPhoto, notNonPhoto, notTiny]);
                            if (s && displayedSmall(im, s)) continue;
                            // The .cover/.thumb wrapper requirement is GONE: Creality
                            // rolled layouts where the gallery renders photos without
                            // those wrappers (and lazy swipers that show one slide
                            // until the arrow is clicked). The gallery BLOCK itself
                            // (div.model-gallery / .model-content) is the scope — the
                            // plate cards live in .print-config-comp / .list-3mf,
                            // measured OUTSIDE div.model-gallery, and bad() rejects
                            // those containers anyway.
                            if (s) add(s, 'slide');
                        }
                        // a gallery painted as CSS backgrounds, not <img>
                        if (!out.length) {
                            for (const u of bgSrc(galleryRoot)) {
                                if (isGalleryPhoto(u) && !isNonPhoto(u) && !isTinyVariant(u))
                                    add(u, 'css');
                            }
                        }
                    }
                    // 2b) last resort on unusual layouts: any creator photo on
                    // the page that is not a promo/avatar/comment/ad-rail one.
                    // Guarantees the listing is never left with zero photos.
                    if (!out.length) {
                        for (const im of document.querySelectorAll('img')) {
                            if (bad(im) || badPromo(im)) continue;
                            const s = goodSrc(im, [isCreator, notNonPhoto, notTiny]);
                            if (s && displayedSmall(im, s)) continue;
                            if (s) add(s, 'page');
                        }
                        if (!out.length) {
                            for (const u of bgSrc(document)) {
                                if (isCreator(u) && !isNonPhoto(u) && !isTinyVariant(u))
                                    add(u, 'pagecss');
                            }
                        }
                    }
                    // 2c) the page's own declared cover picture. A model page
                    // says what its photo is twice over — in og:image (the card
                    // social shows) and in any image preload — so a listing whose
                    // gallery never rendered is still published with the creator's
                    // own photo instead of being skipped.
                    if (!out.length) {
                        const metas = [...document.querySelectorAll(
                            'meta[property="og:image"], meta[name="twitter:image"], '
                            + 'link[rel="preload"][as="image"], link[rel="image_src"]')];
                        for (const el of metas) {
                            const s = absu(el.getAttribute('content')
                                        || el.getAttribute('href') || '');
                            if (isCreator(s) && !isNonPhoto(s)) add(s, 'meta');
                        }
                    }
                    // 3) the strip's own arrow pages the remaining thumbnails.
                    // On strip-less layouts the COVER swiper's next-arrow is the one
                    // that pages the photos — fall back to it so lazy slides are
                    // still walked.
                    const arrowHost = strip || document.querySelector('.swiper-cover, div.model-gallery');
                    const stripNext = arrowHost
                        ? [...((arrowHost.parentElement || arrowHost).querySelectorAll(
                              '.swiper-button-next, [class*=next]'))]
                            .find(e => e.getBoundingClientRect().width > 6) : null;
                    const nb = stripNext ? (() => {
                        const r = stripNext.getBoundingClientRect();
                        return {x: Math.round(r.x + r.width/2),
                                y: Math.round(r.y + r.height/2)};
                    })() : null;
                    // what the page held in total, so a "0 photos" read is
                    // evidence rather than a dead end: 0 images on the whole page
                    // means the listing really has none, while N images and 0 kept
                    // means a rule here rejected them (see `how` and `dropped`).
                    let creatorImgs = 0;
                    for (const im of document.querySelectorAll('img')) {
                        if (srcOf(im).some(isCreator)) creatorImgs++;
                    }
                    return {urls: out, strip: stripCount,
                            strip_cls: strip ? (strip.className||'').toString().slice(0,44) : '',
                            imgs: document.querySelectorAll('img').length,
                            creator_imgs: creatorImgs,
                            how: how.slice(0, 12),
                            next: nb};
                }
            """

            def _grab(_where):
                """Read the gallery rule and merge any new creator photos."""
                g = page.evaluate(gallery_js)
                added = 0
                urls = g.get("urls", []) or []
                tags = g.get("how", []) or []
                for i, u in enumerate(urls):
                    if u not in gallery_imgs:
                        gallery_imgs.append(u)
                        # which rule found it, kept per photo across every read
                        gallery_how[u] = tags[i] if i < len(tags) else "?"
                        added += 1
                _grab.strip = max(getattr(_grab, "strip", 0), g.get("strip", 0) or 0)
                _grab.page = g.get("imgs", 0) or 0
                _grab.creator = g.get("creator_imgs", 0) or 0
                return g, added

            gallery_imgs, stale = [], 0
            gallery_how: dict = {}
            gallery_scope, gallery_steps = "", 0

            # WAIT FOR STABLE: the gallery is Vue-rendered AFTER the page loads, so a
            # single early read can see 1 photo (the SSR cover) while the strip is still
            # empty — that is exactly how a fetch came back with "one image out of six".
            # The first grab(s) are only believed once the count is the same on two
            # consecutive reads at least ~1.2 s apart (or ~14 s have passed — then we go
            # with the best we have rather than hang).
            import time as _time
            _t0 = _time.time()
            g, _ = _grab("initial")
            gallery_scope = g.get("cls") or gallery_scope
            while _time.time() - _t0 < 14:
                page.wait_for_timeout(1200)
                g2, added = _grab("settle")
                gallery_scope = g2.get("cls") or gallery_scope
                if not added and len(gallery_imgs) == len(g.get("urls") or []):
                    break                      # two identical reads — the set is stable
                g = g2

            def _grab_new(_where, budget_ms):
                """Grab until a NEW creator photo appears or the budget runs out.
                A slide usually paints long before the flat 950 ms the old code
                insisted on for every arrow click."""
                out = {}

                def _once():
                    gg, ad = _grab(_where)
                    out["g"], out["added"] = gg, ad
                    return ad > 0
                _poll(page, budget_ms, 150, _once)
                return out["g"], out.get("added", 0)

            if not gallery_imgs:
                # an empty read is usually a page still rendering; keep reading
                # until a photo appears instead of sleeping the whole 5 s first
                g, _ = _grab_new("empty-retry", 5000)
                gallery_scope = g.get("cls") or gallery_scope

            for _step in range(12):          # click the next-arrow through the set
                gallery_scope = g.get("cls") or gallery_scope
                if not g.get("next"):
                    break
                page.mouse.click(g["next"]["x"], g["next"]["y"])
                g, added = _grab_new("arrow", 950)
                gallery_steps += 1
                stale = stale + 1 if not added else 0
                if stale >= 3:
                    break
            # a full scroll pass catches anything that only loads in view
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            g, _ = _grab_new("scrolled", 1600)
            page.evaluate("window.scrollTo(0, 0)")
            g, _ = _grab_new("back-to-top", 600)
            gallery_scope = g.get("cls") or gallery_scope
            gallery_info = {
                "scope": gallery_scope, "images": len(gallery_imgs),
                "strip_thumbs": getattr(_grab, "strip", 0),
                "matches_strip": len(gallery_imgs) >= (getattr(_grab, "strip", 0) or 0),
                "arrow_clicks": gallery_steps,
                "all_creator": True,   # every one is the model's own photo
                                       # (/crealityCloud/upload/ or /common/)
                # which rule produced each photo, and what the page held in
                # total — so a "0 photos" skip names the cause instead of being
                # a dead end (0 creator images = the listing truly has none;
                # creator images > 0 and none kept = a rule rejected them).
                "found_by": sorted({gallery_how.get(u, "?") for u in gallery_imgs}),
                "page_images": getattr(_grab, "page", 0),
                "creator_images": getattr(_grab, "creator", 0),
                "paths": [u.replace("https://pic2-cdn.creality.com", "")[:80]
                          for u in gallery_imgs[:12]],
            }
            UPLOAD_VERIFY["gallery"] = gallery_info

            _mark("gallery")
            # ---- SECOND licence read, after the page has fully settled (the
            # first read can land while the card is still unmounted; by now the
            # gallery work has given every piece time to render). It is kept only
            # when it NAMES a licence and the first read did not.
            if not lic_probe.get("evidence") and not lic_probe.get("restricted"):
                for _attempt in range(2):
                    try:
                        raw_lic2 = page.evaluate(LICENSE_JS)
                        got2 = json.loads(raw_lic2) if isinstance(raw_lic2, str) else (raw_lic2 or {})
                    except Exception:
                        got2 = {}
                    text2 = " ".join(x for x in [(got2.get("cc_url") or ""),
                                                 (got2.get("card") or "")] if x).strip()
                    probe2 = classify_license(text2, got2.get("icons"))
                    if probe2.get("evidence") or probe2.get("restricted"):
                        lic_d, lic_text, lic_probe = got2, text2, probe2
                        data["license"] = lic_text
                        UPLOAD_VERIFY["license_text"] = lic_text[:600]
                        UPLOAD_VERIFY["license_evidence"] = probe2.get("evidence")
                        lic_icons = list(set(list(lic_icons) + list(got2.get("icons") or [])))[:6]
                        UPLOAD_VERIFY["license_icons"] = lic_icons[:6]
                        break                     # now it is named
                    if _attempt == 0:
                        page.wait_for_timeout(2000)

            _mark("licence")
            # ---- name: the page's own h1 when it is the model name, else the
            # SEO title with its localised tail stripped (see _model_name_from)
            name = _model_name_from(data.get("title", ""), data.get("h1", ""))
            slug = _slug(name)
            description = (data["desc"] or "")[:500]

            # ---- original photos
            dropped: list = []
            image_paths = _download_images(gallery_imgs, slug, strict=False, report=dropped)
            gallery_info["downloaded"] = len(image_paths)
            gallery_info["dropped"] = dropped[:12]     # why each photo was not kept
            UPLOAD_VERIFY["gallery"] = gallery_info

            _mark("photos")
            # ---- the real model file: split-button 'Download 3MF' (this
            # layout) or 'View STL/CAD Files' -> menu item 'Download 3MF'
            def _find(text_re, min_w=40):
                return page.evaluate(FIND_JS, text_re)

            def _find_in_panel(text_re):
                return page.evaluate(FIND_DIALOG_JS, text_re)

            def _left_the_model() -> bool:
                """True when a click took the tab off the model page: the slug is
                gone, or the browser is sitting on a sign-in form."""
                u = page.url or ""
                return (SIGNIN_URL_RE.search(u) is not None
                        or (_slug_of_model(model_url) not in u))

            stl_path = None
            # What this step actually saw, so a model that produced no file is
            # reported with evidence instead of a bare "no usable model file
            # came back from the page" — which is exactly what hid a signed-out
            # browser behind 20 identical skips on 2026-10-23.
            file_try: dict = {"control": "", "step2": "", "page": "",
                              "downloads": 0, "signin": False, "navigated": False}
            # The control's LABEL is in the page's own language, and most layouts
            # are TWO steps: "Voir les fichiers STL/CAO" (View STL/CAD files) opens
            # a dialog listing the printable files, and a control INSIDE that
            # dialog starts the download. The dialog's controls are icons with no
            # text (see MODEL_FILES_JS), so it is worked by structure; an
            # English-layout page that offers "Download 3MF" as a menu item, or
            # starts the download from the first click alone, is still matched by
            # words and by the wait below.
            main = _find(DOWNLOAD_MAIN_RE)
            file_try["control"] = (main or {}).get("t", "")
            if main:
                try:
                    page.mouse.click(main["x"], main["y"])
                except Exception as e:
                    file_try["click_error"] = str(e)[:120]
                page.wait_for_timeout(1200)
                if _left_the_model():
                    file_try["navigated"] = True
                if not downloads and not file_try["navigated"]:
                    # First the dialog read by STRUCTURE (its rows have no text),
                    # then — only when there was no dialog at all — the older
                    # layout's menu item, which DOES carry the site's wording.
                    from uploader import _verify_model_files as _verify
                    stl_path = _files_from_dialog(page, downloads, slug, model_url,
                                                  file_try, _verify)
                    if stl_path is None and not file_try.get("files"):
                        for _ in range(8):
                            item = (_find_in_panel(DOWNLOAD_3MF_RE)
                                    or _find_in_panel(DOWNLOAD_VERB_RE)
                                    or _find_in_panel(r"\b3MF\b"))
                            if item and item.get("t") != main.get("t"):
                                file_try["step2"] = item.get("t", "")
                                try:
                                    page.mouse.click(item["x"], item["y"])
                                except Exception as e:
                                    file_try["click_error"] = str(e)[:120]
                                break
                            page.wait_for_timeout(1000)
                if (stl_path is None and not file_try.get("rejected")
                        and not file_try.get("files")):
                    # Nothing had a chance to wait yet: this is the no-dialog
                    # layout, where the click itself starts the download. A
                    # dialog that WAS read has already been given its own
                    # patience, so it is not waited on twice.
                    for _ in range(45):
                        if downloads:
                            break
                        page.wait_for_timeout(1000)
                    if downloads:
                        d = downloads[0]
                        fname = (d.suggested_filename or (slug + ".3mf")).replace("/", "_")
                        stl_path = os.path.join(DOWNLOADS_DIR, fname)
                        d.save_as(stl_path)
            file_try["downloads"] = len(downloads)
            file_try["page"] = (page.url or "")[:160]
            file_try["signin"] = bool(SIGNIN_URL_RE.search(page.url or ""))
            _mark("file")
        finally:
            try:
                page.close()
            except Exception:
                pass

    from uploader import _verify_model_files
    verify_ok = False
    if stl_path and os.path.exists(stl_path):
        verify_ok = _verify_model_files([stl_path], model_url)

    license_info = classify_license(data["license"], lic_icons)
    if stl_path and not verify_ok:
        raise RuntimeError("The downloaded file from Creality Cloud is not real "
                           "model geometry (3MF/STL check failed) — not uploading junk.")

    return {"name": name, "description": description, "stl_path": stl_path,
            "image_paths": image_paths, "slug": slug, "author": data["author"],
            "license": license_info, "source_url": model_url,
            "file_attempt": file_try,    # what the download step really saw
            "signin_required": bool(file_try.get("signin")),
            "gallery": gallery_info, "read_via": "dom"}   # proof of WHICH images were taken


def _english_site_url(url: str) -> str:
    """Strip any locale prefix off a crealitycloud.com URL, so every page the
    pipeline drives is served in ENGLISH. The account's Chrome renders /fr/
    (stored locale preference) and the model pages' French licence text
    ("Cette œuvre est sous licence…") evaded the English-only licence reader,
    which is how models were being skipped with "no licence was read"."""
    return re.sub(r"(crealitycloud\.com)/[a-z]{2}(?=/|$)", r"\1", url)


def _canon_model_url(url: str) -> str:
    """A pasted model URL — including the /fr/ form the account's Chrome copies
    (stored locale) — becomes the canonical non-locale model-detail URL. The
    slug is the key: locale prefix and any query string are dropped."""
    u = _english_site_url(url)
    m = re.search(r"crealitycloud\.com/model-detail/[^/?#]+", u)
    return "https://" + m.group(0) if m else u


# The download step needs to know when the browser stopped being signed in:
# Creality answers a download from a logged-out session by showing its own login
# page, and 2026-10-23 a whole run skipped 20 models with "no usable model file
# came back from the page" because nothing here could say that out loud.
SIGNIN_URL_RE = re.compile(r"crealitycloud\.com/login(?:[?/#]|$)|/signin(?:[?/#]|$)",
                           re.I)


def _slug_of_model(url: str) -> str:
    """The model-detail slug — the one part of the URL that says the tab is
    still on the model it was sent to read."""
    m = re.search(r"model-detail/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def fetch(url: str) -> dict:
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    # Creality Cloud — the primary source going forward (user's standing rule:
    # "no more MakerWorld, no more Printables, just Creality Cloud links")
    # ANY crealitycloud URL routes here: the account's Chrome copies /fr/... links,
    # and a locale prefix must not fall through to the generic scrape.
    if re.search(r"crealitycloud\.com", url):
        return _fetch_creality(url)

    # Thingiverse
    m = re.search(r"thingiverse\.com/thing:(\d+)", url)
    if m:
        return _fetch_thingiverse(m.group(1), m.group(1))

    # Printables
    m = re.search(r"printables\.com/model/(\d+)", url)
    if m:
        return _fetch_printables(m.group(1), m.group(1))

    # MakerWorld
    m = re.search(r"makerworld\.com/(?:\w+/)?models/(\d+)", url)
    if m:
        return _fetch_makerworld(m.group(1))

    # Generic direct URL
    return _fetch_generic(url)
