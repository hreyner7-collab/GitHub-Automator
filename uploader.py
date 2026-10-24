"""
Fast Creality Cloud upload via CDP, driving the user's REAL Chrome
(launched with --remote-debugging-port=9222) so Cloudflare trusts the session
and existing MakerWorld/Creality logins are reused.

upload() is the async entrypoint the dashboard calls. It:
  1. Ensures a model file exists (downloads STL/CAD zip from the open MakerWorld tab if needed)
  2. Opens the Creality "create-model-new" page
  3. Selects the STL/CAD file type, uploads the model file
  4. Advances to step 2, fills the name, uploads the red cover images
  5. STOPS before the final Submit (Category + Copyright agreement + Submit left for the user)
"""
import asyncio
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import time
import urllib.request
import zipfile

from playwright.async_api import async_playwright

import page_model
import timing
# Imported under an alias ON PURPOSE: `upload()` has a long-standing local named
# `verify` (the dict of evidence it returns), and a plain `import verify` would be
# shadowed by it -- making every step_verify call below a silent AttributeError
# that the surrounding try/except would swallow. pyflakes found this.
import verify as step_verify

BASE = os.path.dirname(__file__)
DOWNLOADS = os.path.join(BASE, "downloads")
MODEL_DIR = os.path.join(DOWNLOADS, "model_files")
PROFILE_DIR = os.path.join(BASE, "chrome_debug_profile")
# Use 127.0.0.1 (not "localhost"): localhost can resolve to IPv6 ::1, but the
# browser's debug port listens on IPv4 — that mismatch is the
# "ECONNREFUSED ::1:9222" error.
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
# Chrome first (the user's choice); Edge kept only as a never-used fallback so
# the app still functions on machines without Chrome.
BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
MODEL_EXTS = (".3mf", ".stl", ".step", ".stp", ".obj", ".ply", ".off", ".3ds", ".wrl", ".dae")

# Filled in by _verify_model_files() (and failure-reasons by the download
# fallback): proof about which file was acquired and what is inside it.
# Surfaced in the /upload response so results are checkable, not assumed.
UPLOAD_VERIFY: dict = {}


def _debug_port_up() -> bool:
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
        return True
    except Exception:
        return False


def ensure_browser() -> bool:
    """Make sure Chrome is running with the remote-debugging port. If not, launch
    it with the saved profile (so MakerWorld/Creality logins persist) and wait
    for the port to come up. Returns True if the debug port is reachable."""
    if _debug_port_up():
        return True
    browser = next((p for p in BROWSER_PATHS if os.path.exists(p)), None)
    if not browser:
        return False
    os.makedirs(PROFILE_DIR, exist_ok=True)
    try:
        subprocess.Popen(
            [browser, f"--remote-debugging-port={CDP_PORT}",
             f"--user-data-dir={PROFILE_DIR}",
             "https://www.crealitycloud.com"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    for _ in range(40):  # wait up to ~20s for the port
        if _debug_port_up():
            return True
        time.sleep(0.5)
    return False


# Back-compat alias for anything that imported the old name.
ensure_edge = ensure_browser

# The cover photos are what the Creality form actually shows, so a model whose
# fetch brought none back cannot be published — the reason is one string, shared
# by the publisher (which refuses here) and by the auto run (which reports it as
# a FETCH failure and moves on instead of walking the model to the form).
NO_COVERS_ERROR = ("No edited images for this model. Run Fetch & Edit first "
                   "so the red images are generated.")


def usable_cover_images(image_paths) -> list:
    """The cover photos this model can really be published with: the ones for
    THIS model that exist on disk (no glob fallback that would pull in a
    previous model's edited images)."""
    return [p for p in (image_paths or []) if p and os.path.exists(p)]


def _clear_model_dir():
    os.makedirs(MODEL_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(MODEL_DIR, "*")):
        try:
            os.remove(old)
        except Exception:
            pass


def _extract_models(path):
    """Return a LIST of real model files (Creality-accepted extensions) from a
    download. If it's a zip, pull out every model file; drop tiny placeholder
    files when bigger ones exist. If it's already a model file, return just it."""
    low = path.lower()
    if not low.endswith(".zip"):
        return [path] if low.endswith(MODEL_EXTS) else []

    _clear_model_dir()
    try:
        z = zipfile.ZipFile(path)
    except Exception:
        return []
    out = []
    for n in z.namelist():
        if n.endswith("/"):
            continue
        if n.lower().endswith(MODEL_EXTS):       # only formats Creality accepts
            try:
                z.extract(n, MODEL_DIR)
                out.append(os.path.join(MODEL_DIR, n))
            except Exception:
                pass
    if not out:
        return []
    sizes = [(p, os.path.getsize(p)) for p in out if os.path.exists(p)]
    biggest = max((s for _, s in sizes), default=0)
    # keep files that are a meaningful fraction of the largest (drops 2–3 KB
    # placeholder stubs while keeping all real parts of a multi-part model)
    kept = [p for p, s in sizes if s >= max(1024, biggest * 0.05)]
    return kept or [p for p, _ in sizes]


def _verify_model_files(files, source_url=""):
    """Best-effort proof that the acquired file(s) are real model geometry for
    THIS model. 3MF/ZIP containers are opened and inspected (required
    3D/3dmodel.model part, part sizes, embedded plate previews, stored object
    names); raw model files are checked for plausible size. Never raises.
    Fills the module-global UPLOAD_VERIFY and returns its overall ok flag."""
    UPLOAD_VERIFY.clear()
    v = UPLOAD_VERIFY
    v["source_url"] = source_url
    m = re.search(r"makerworld\.com/(?:\w+/)?models/(\d+)", source_url or "")
    if not m:
        m = re.search(r"crealitycloud\.com/model-detail/([^/?#]+)", source_url or "")
    v["model_id"] = (m.group(1) if m and not m.group(1).isdigit() else
                     m.group(1) if m else None)
    v["files"] = []
    for p in files or []:
        info = {"name": os.path.basename(p),
                "size_bytes": os.path.getsize(p) if os.path.exists(p) else 0}
        low = p.lower()
        if low.endswith((".3mf", ".zip")):
            try:
                with zipfile.ZipFile(p) as z:
                    names = z.namelist()
                    info["container_ok"] = z.testzip() is None
                    info["model_parts"] = [n for n in names if n.lower().endswith(".model")][:5]
                    plates = [n for n in names
                              if "plate" in n.lower() and n.lower().endswith(".png")]
                    info["embedded_previews"] = plates[:5]
                    info["parts_count"] = len(names)
                    info["largest_part_bytes"] = max((i.file_size for i in z.infolist()), default=0)
                    # A real 3MF must contain the 3D model part and real mesh data.
                    info["geometry_ok"] = bool(info["model_parts"]) and info["largest_part_bytes"] > 10000
                    if not info["geometry_ok"]:
                        # A ZIP OF MODEL FILES is not junk: Creality's own
                        # "download all" answers with <model>_part1.zip that holds
                        # the .3mf files (live 2026-10-09: the dumpling link's
                        # zip held "…kafa beyaz.3mf" + "…alt.3mf" and was thrown
                        # away as "not real model geometry", so the fetch came
                        # back with photos and no model at all). Verify what is
                        # INSIDE instead of refusing the container.
                        inner = [n for n in names
                                 if n.lower().endswith(MODEL_EXTS)
                                 and not n.lower().endswith((".zip", ".rar", ".7z"))]
                        if inner:
                            info["inner_models"] = inner[:5]
                            big = max(inner, key=lambda n: z.getinfo(n).file_size)
                            try:
                                blob = z.read(big)
                            except Exception as e:
                                blob, info["inner_error"] = b"", str(e)[:120]
                            info["inner_largest_bytes"] = len(blob)
                            if big.lower().endswith((".3mf", ".zip")):
                                try:
                                    with zipfile.ZipFile(io.BytesIO(blob)) as zz:
                                        parts = [n for n in zz.namelist()
                                                 if n.lower().endswith(".model")]
                                    info["inner_model_parts"] = parts[:3]
                                except Exception as e:
                                    info["inner_error"] = str(e)[:120]
                            info["geometry_ok"] = bool(
                                info.get("inner_model_parts")
                            ) or (big.lower().endswith((".stl", ".obj", ".step",
                                                        ".stp", ".ply"))
                                  and len(blob) > 10000)
                    # Object names the slicer stored — direct evidence of WHICH model.
                    try:
                        cfg = next((n for n in names
                                    if n.lower().endswith("model_settings.config")), None)
                        if cfg:
                            txt = z.read(cfg).decode("utf-8", "ignore")
                            info["object_names"] = sorted(set(
                                re.findall(r'<object[^>]*\bname="([^"]+)"', txt)))[:8]
                    except Exception:
                        pass
            except Exception as e:
                info["error"] = str(e)[:200]
        else:
            # Raw STL/STEP/etc: plausibility = non-trivial size (extension was
            # already validated against MODEL_EXTS).
            info["geometry_ok"] = info["size_bytes"] > 1024
        v["files"].append(info)
    v["ok"] = bool(v["files"]) and all(f.get("geometry_ok") for f in v["files"])
    return v["ok"]


async def _download_from_makerworld(ctx, source_url=None):
    # Always drive the EXACT model the user fetched. Navigate the MakerWorld tab
    # to source_url first so we never download whatever model happened to be open.
    page = next((p for p in ctx.pages if "makerworld.com" in p.url), None)
    if source_url and "makerworld.com" in source_url:
        if not page:
            page = await ctx.new_page()
        await page.bring_to_front()
        # only navigate if we're not already on this exact model
        m = re.search(r"/models/(\d+)", source_url)
        model_id = m.group(1) if m else ""
        if not model_id or model_id not in page.url:
            await page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
    if not page:
        UPLOAD_VERIFY["last_attempt"] = "no MakerWorld tab open in the debug Chrome"
        return []
    await page.bring_to_front()
    await page.wait_for_timeout(400)

    # Path A: fetch the file through the LOGGED-IN page itself. The same
    # endpoint gets a 403 Cloudflare challenge when called outside the browser
    # (downloader.py's direct request), but from the page's own session it
    # serves the design zip directly. Exact-model by construction (designId)
    # and immune to button-label changes.
    m = re.search(r"/models/(\d+)", page.url)
    if m:
        model_id = m.group(1)
        blob = None
        try:
            blob = await page.evaluate("""
                async (designId) => {
                    const r = await fetch('/api/v1/design-bom-file/download?designId=' + designId,
                                          {credentials: 'include'});
                    if (!r.ok) return null;
                    const buf = await r.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    let bin = '';
                    const CH = 0x8000;
                    for (let i = 0; i < bytes.length; i += CH)
                        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
                    return bin;
                }
            """, model_id)
        except Exception:
            blob = None
        if blob:
            data = blob.encode("latin-1", errors="ignore")
            # A real zip starts with 'PK' and is far larger than an error page.
            if data[:2] == b"PK" and len(data) > 20000:
                zip_path = os.path.join(DOWNLOADS, f"mw_{model_id}_bom.zip")
                with open(zip_path, "wb") as f:
                    f.write(data)
                extracted = _extract_models(zip_path)
                if extracted:
                    return extracted

    # Path B: click the download UI (accepts both current and legacy labels).
    # Locate the download element (it's a DIV, not a button). MakerWorld has used
    # both "Download STL/CAD Files" and a per-profile "Download 3MF" label —
    # accept either (but never a generic "Download", which matches "Download
    # Bill of Materials").
    info = await page.evaluate(r"""
        () => {
            const cands = [...document.querySelectorAll('button,a,div,span,[role=button]')]
                .filter(e => /^Download (STL\/CAD|3MF|CAD)/.test((e.innerText||'').trim()));
            for (const el of cands) {
                el.scrollIntoView({block: 'center'});
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && r.y >= 0 && r.y < window.innerHeight)
                    return {x: r.x + r.width/2, y: r.y + r.height/2,
                            label: (el.innerText||'').trim()};
            }
            return null;
        }
    """)
    if not info:
        UPLOAD_VERIFY["last_attempt"] = "no 'Download STL/CAD' or 'Download 3MF' button found on the page"
        return []

    # Collect EVERY download the page fires (a model can trigger several files),
    # so we can keep the right/largest one instead of whatever lands first.
    downloads = []
    page.on("download", lambda d: downloads.append(d))

    await page.mouse.click(info["x"], info["y"])
    await page.wait_for_timeout(2500)
    # Some models pop a small confirm with an exact "Download All" — click only
    # that (NEVER a generic "Download", which matches "Download Bill of Materials").
    for exact in ["Download All", "Download All Files", "Download Files"]:
        loc = page.get_by_role("button", name=exact, exact=True)
        try:
            if await loc.count() > 0:
                await loc.first.click(timeout=2500)
                break
        except Exception:
            pass

    # Wait for downloads to arrive and finish (up to ~45s total).
    for _ in range(45):
        if downloads:
            await page.wait_for_timeout(2000)   # let remaining/streaming files finish
            break
        await page.wait_for_timeout(1000)
    if not downloads:
        UPLOAD_VERIFY["last_attempt"] = "clicked the download button but no download started within 45s"
        return []

    # Save them all to disk under their real names, then keep the LARGEST.
    saved = []
    for d in downloads:
        try:
            name = (d.suggested_filename or "download.bin").replace("/", "_")
            dest = os.path.join(DOWNLOADS, name)
            await d.save_as(dest)
            if os.path.exists(dest):
                saved.append(dest)
        except Exception:
            pass
    if not saved:
        UPLOAD_VERIFY["last_attempt"] = "download event(s) fired but the file(s) could not be saved"
        return []
    largest = max(saved, key=lambda p: os.path.getsize(p))
    return _extract_models(largest)


# ---------------------------------------------------------------------------
# Licence -> form answers. The create-model form's three radio groups drive the
# CC badge shown right under them (confirmed against the live form):
#   Q1 "Allow adaptations of your work to be shared?"  0=Yes 1=No 2=share-alike
#   Q2 "Allow commercial uses of your work?"           0=Yes 1=No
#   Q3 "Allow sharing or redistributing of your work or its derivatives?"
#                                                      0=Yes 1=No
# Badges confirmed: [0,0,1] CC BY | [0,1,1] CC BY-NC | [2,0,1] CC BY-SA |
#                   [2,1,1] CC BY-NC-SA | [0,0,0] CC0 / Public Domain
# Only a public-domain dedication answers Yes to Q3; every CC licence keeps its
# standard badge, so the re-upload inherits the source's terms exactly.
LICENSE_ANSWERS = {
    "": [0, 0, 1],
    "by": [0, 0, 1],
    "by-sa": [2, 0, 1],
    "by-nc": [0, 1, 1],
    "by-nc-sa": [2, 1, 1],
    "by-nd": [1, 0, 1],
    "by-nc-nd": [1, 1, 1],
    "cc0": [0, 0, 0],
    "zero": [0, 0, 0],
    "publicdomain": [0, 0, 0],
}


def license_answer_indexes(license_raw: str) -> tuple[str, list[int]]:
    """(code, [q1, q2, q3]) — which radio to pick in each licence question so
    the form's badge matches the SOURCE model's licence."""
    t = re.sub(r"\s+", "-", (license_raw or "").lower().replace("_", "-"))
    m = re.search(r"(?:licenses|publicdomain)/([a-z0-9\-]+)", t)
    code = m.group(1).split("/")[0] if m else ""
    if not code:
        m2 = re.search(r"\bcc-?(by(?:-(?:nc|sa|nd)){0,2}|0|zero)\b", t)
        if m2:
            code = m2.group(1)
    code = code.strip("-")
    if code in ("zero", "public-domain", "publicdomain"):
        code = "cc0"
    idx = LICENSE_ANSWERS.get(code)
    if idx is None:
        idx = LICENSE_ANSWERS["by"] if "by" in code else LICENSE_ANSWERS[""]
    return code, list(idx)


def expected_badge_token(code: str) -> str:
    """The CC name the form's badge must read for this source licence."""
    c = (code or "").lower()
    if c in ("cc0", "zero", "publicdomain"):
        return "CC0"
    mods = [p for p in c.split("-") if p in ("nc", "sa", "nd")]
    return "CC BY" + "".join("-" + p.upper() for p in mods)


# The licence badge's own paragraph STARTS with its name ("CC BY-NC This
# license allows reusers..."), so an anchored match is the reliable read. The
# "Model Source" panel also shows a tip that NAMES other licences ("...for
# example licenses such as CC BY-ND and CC BY-NC-ND do not allow for derivative
# works"). A blind smallest-element text search used to grab "CC BY-ND" out of
# that tip and then REFUSE a form whose badge correctly read CC BY-NC. Hence:
# anchor first, and only accept an unanchored match that is not part of an
# enumeration of licence names.
CC_TOKEN = (r"(CC\s?0|CC\s?BY(?:\s*[-/]?\s*(?:NC|SA|ND)){0,2}"
            r"|Public\s+Domain)")


def badge_license_name(text: str) -> str:
    """Pull the badge's CC name out of a licence-card text."""
    t = (text or "").strip()
    if not t:
        return ""
    m = re.match(r"\s*" + CC_TOKEN, t, re.I)
    if m:
        return _norm_cc(m.group(1))
    for m in re.finditer(CC_TOKEN, t, re.I):
        before = t[max(0, m.start() - 30):m.start()]
        if re.search(r"(such as|e\.g\.?|examples?|and|or|,|/)\s*$", before, re.I):
            continue                      # a list of licence names, not the badge
        after = t[m.end():m.end() + 90]
        if re.search(r"no\s+derivatives|do(es)?\s+not\s+allow", after, re.I):
            continue                      # the "as these licences do not allow" tip
        return _norm_cc(m.group(1))
    return ""


# ── the remix-source picker (Creality's "Model Source" search) ───────────────
# The picker is a SEARCH: you type the source model's name and Creality lists
# matching models to click. Two ways this goes wrong, both seen on the live
# form (screenshot: "LEGO - Pallet jack" with red text "The license policy does
# not allow for derivative works or modifications"):
#   · clicking a row marked No-Derivatives — the form itself refuses that
#     source, and it must never be declared;
#   · clicking the WRONG row ("Scale model forklift" for "scale model pallet
#     jack") because the match was made on loose words, so the listing ended up
#     crediting a model it was not derived from.
# SOURCE_ROWS_JS reads every suggestion with its text, its colour-flagged
# warning and its real screen coordinates; pick_source_suggestion() then scores
# them against the source model's OWN name (the title the page gave us) and
# refuses to invent one. Both are plain enough to run offline against a fake
# list (scripts/check_source_pick.py).
SOURCE_ROWS_JS = r"""
    () => {
        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
        // Creality's own warning that this source may not be remixed
        const WARN = /does not allow for derivative|license policy|no derivative|not allowed|does not permit/i;
        const isRed = (c) => {
            const m = (c || '').match(/(\d+)\D+(\d+)\D+(\d+)/);
            return !!m && +m[1] >= 130 && +m[2] <= 115 && +m[3] <= 115;
        };
        const rows = [];
        [...document.querySelectorAll('.origin-item')].forEach((el, i) => {
            const nameEl = el.querySelector('.name') || el;
            const name = clean(nameEl.innerText || '');
            const author = clean((el.querySelector('.author') || {}).innerText || '');
            let red = false, warn = WARN.test(el.innerText || '');
            for (const n of el.querySelectorAll('*')) {
                if (n.children.length) continue;              // leaf text only
                const t = clean(n.textContent || '');
                if (t && isRed(getComputedStyle(n).color)) {
                    red = true;
                    if (WARN.test(t)) warn = true;
                }
            }
            const a = el.querySelector('a[href*="/model-detail/"]');
            const nr = nameEl.getBoundingClientRect ? nameEl.getBoundingClientRect()
                                                   : el.getBoundingClientRect();
            const rr = el.getBoundingClientRect();
            const cls = (el.className || '').toString();
            // CREALITY'S OWN FLAG, read off the live form (inspector step 2): a
            // No-Derivatives row carries class "origin-item flex-center
            // disabled-item" as well as the red sentence inside it.
            const off = /disabled-item/.test(cls);
            const disabled = off || el.getAttribute('aria-disabled') === 'true' || /disabled/.test(cls);
            rows.push({
                i: i, name: name, author: author,
                href: a ? a.getAttribute('href') : '',
                red: red, warn: warn, disabled: disabled, disabled_item: off,
                flagged: red || warn || disabled,
                x: nr.x + Math.min(nr.width ? nr.width / 2 : 60, 140),
                y: nr.y + (nr.height || 20) / 2,
                row_x: rr.x + rr.width / 2, row_y: rr.y + rr.height / 2,
            });
        });
        const input = document.querySelector("input[placeholder*='Paste the URL' i]");
        const chosen = document.querySelector('.origin-item.selected, .origin-item.active');
        return JSON.stringify({rows: rows, input_value: input ? input.value : '',
                               chosen: chosen ? clean(chosen.innerText || '').slice(0, 80) : ''});
    }
"""


async def _settle(page, budget_ms, step_ms, cond):
    """Evaluate the async `cond()` now and then every `step_ms` until it returns
    something true or the budget runs out, and hand back the last value.

    This is the difference between waiting for a CONDITION and waiting for a
    NUMBER. The publish path slept a flat 15.8 s per model on clocks nothing was
    watching — 3500 ms per source search, 2000 ms after the row click, 1200 ms
    per remix-radio try, 500 ms before looking for a crop modal — against a
    measured 40.1 s publish (job 20261024-131810). Every one of those keeps its
    old figure as the cap, so a slow answer still gets the same grace; a fast one
    now costs what it actually takes.
    """
    t0 = time.time()
    got = None
    while True:
        got = await cond()
        if got or (time.time() - t0) * 1000.0 >= budget_ms:
            return got
        await page.wait_for_timeout(step_ms)


def _norm_words(s: str) -> list[str]:
    return [w for w in re.sub(r"[^0-9a-zA-Z\u0400-\u04ff]+", " ", (s or "").lower()).split() if w]


def source_score(row_name: str, source_name: str, slug: str,
                 row_href: str = "") -> int:
    """How well one picker row matches the model we actually downloaded.

    0 means "this is NOT the source model" and must never be clicked. The bar
    is deliberately high: the source's own TITLE has to match (it is the title
    of the very page we downloaded from, and Creality's search matches titles —
    including a non-English one like "\u0414\u043e\u043c\u043a\u0440\u0430\u0442 \u0434\u043b\u044f \u043f\u043e\u0434\u044a\u0435\u043c\u0430 \u043f\u043e\u0434\u0434\u043e\u043d\u043e\u0432", which
    shares no words with the slug "scale-model-pallet-jack"). Loose word overlap
    is NOT enough — that is how "Scale model forklift" got clicked for "scale
    model pallet jack" and the listing credited a model it did not use.
    """
    n = " ".join(_norm_words(row_name))
    if not n:
        return 0
    score = 0
    if slug and row_href:
        row_slug = re.search(r"model-detail/([^/?#]+)", row_href)
        if row_slug and row_slug.group(1) == slug:
            return 100                          # the picker told us outright
    src = " ".join(_norm_words(source_name))
    if src:
        if n == src:
            score = 100
        elif src in n or n in src:
            score = 90                          # a truncated title still matches
        else:
            tw = set(w for w in _norm_words(source_name) if len(w) > 2)
            hits = sum(1 for w in tw if w in n)
            if tw and hits == len(tw):
                score = 80                      # every title word is there
            elif len(tw) >= 3 and hits >= len(tw) - 1:
                score = 70                      # one word lost to truncation
            # anything else: not the source model
    if score:
        sw = set(w for w in _norm_words(re.sub(r"[-_]", " ", slug or "")) if len(w) > 2)
        if sw:
            sl = sum(1 for w in sw if w in n)
            if sl == len(sw):
                score += 10
            elif sl:
                score += 5
    return score


def _norm_text(s: str) -> str:
    """A name/title as one lower-case word string, punctuation dropped, so
    "Smartphone  stand" and "smartphone-stand" compare equal. Cyrillic kept."""
    return " ".join(_norm_words(s))


def _slug_of(href: str) -> str:
    """The model slug a picker/registered-source link points at, else ""."""
    m = re.search(r"model-detail/([^/?#]+)", href or "")
    return m.group(1) if m else ""


def declares_our_source(clicked: dict | None, registered_text: str,
                        registered_href: str, source_url: str, slug: str,
                        source_name: str) -> dict:
    """Is the source card the form is now showing REALLY the model we copied?

    This is the check that stops a listing crediting the wrong creator — the
    shape of an infringement report. It used to pass on the loosest possible
    evidence ("the first word of the clicked row appears in the card"), so a
    look-alike row could register and the upload still submitted (live run
    2026-10-09: `remix_registered_href` came back empty for all five models, so
    nothing ever proved WHICH model was credited).

    Confirmed only by the source's own identity, any one of:
      · the registered source LINK is the source model's slug;
      · the source model's URL is printed on the form;
      · the registered source NAMES the source model (its page title);
      · the row we clicked is a CERTAIN match AND the registered source shows
        that row's name (tolerating one word lost to a 160-char truncation).

    Returns {declared, why}; `why` is a sentence for the run log either way.
    """
    if slug and _slug_of(registered_href) == slug:
        return {"declared": True,
                "why": "the registered source links the source model (%s)" % slug}
    reg_raw = (registered_text or "")
    url = re.sub(r"^https?://(www\.)?", "", (source_url or "").lower()).rstrip("/")
    if url and url in reg_raw.lower():
        return {"declared": True, "why": "the source URL is printed on the form"}
    reg = _norm_text(reg_raw)
    if not reg:
        return {"declared": False, "why": "nothing is registered as the source"}
    src = _norm_text(source_name)
    if len(src) > 3 and src in reg:
        return {"declared": True,
                "why": "the registered source names the source model"}
    name = _norm_text((clicked or {}).get("name") or "")
    if name and (clicked or {}).get("certain"):
        words = [w for w in name.split() if len(w) > 2]
        here = [w for w in words if w in reg]
        if words and (len(here) == len(words)
                      or (len(words) >= 3 and len(here) >= len(words) - 1)):
            return {"declared": True,
                    "why": ("the registered source is the source-model row we "
                            "clicked (%s)" % (clicked or {}).get("name"))}
    return {"declared": False,
            "why": ("the form shows %r, which does not identify the source "
                    "model (slug %s / title %r)"
                    % (reg_raw[:80] or "nothing", slug or "-", source_name or "-"))}


def pick_source_suggestion(rows, source_name: str = "", slug: str = "",
                           min_score: int = 80) -> dict:
    """Choose WHICH suggestion to click, or none at all.

    Never returns a row Creality flagged (red "does not allow for derivative
    works" text, or a disabled row), and never returns a row that does not
    really look like the source model — declaring the wrong model would credit
    a design we did not use. The default bar is a CERTAIN match (80+: the row's
    link is the source slug, or its title really is the source's title); a row
    that merely loses a word of a long title (70) is not enough to click.
    Returns {pick, score, certain, flagged: [...], rows: [...]}.
    """
    scored = sorted(((source_score(r.get("name", ""), source_name, slug,
                                   r.get("href", "")), r)
                     for r in (rows or [])), key=lambda t: -t[0])
    clean = next(((s, r) for s, r in scored
                  if s >= min_score and not r.get("flagged")), None)
    flagged = [r for s, r in scored if s >= min_score and r.get("flagged")]
    # the picked row travels WITH its certainty: the registered-source check must
    # be able to tell "this row really is the source model" from "this row was a
    # plausible match", and the coordinates are needed to click it.
    pick = dict(clean[1]) if clean else None
    if pick is not None:
        pick["certain"] = clean[0] >= CERTAIN_SCORE
        pick["score"] = clean[0]
    return {"pick": pick,
            "score": (clean[0] if clean else 0),
            "certain": bool(pick and pick["certain"]),
            "flagged": flagged,
            "rows": [r.get("name", "") for r in (rows or [])]}


# a score at or above this is the source model, not a look-alike: 100 = the
# row's link is the source slug / its title is the source's title exactly,
# 90 = one title contains the other, 80 = every title word is present. 70 (one
# word of a 3+ word title lost) is NOT certain and is never clicked.
CERTAIN_SCORE = 80


def remix_refusal(cand, declared: bool, why: str, mark_text: str, result: dict,
                  source_name: str, slug: str) -> str:
    """The sentence to raise when this source may not be submitted, else "".

    The only bar is `declared`: the form must be showing OUR source model,
    proven by its own identity (registered slug / URL printed / title) inside
    declares_our_source(). Which suggestion row was clicked matters only as
    extra evidence, never as the reason to refuse — the picker's title search
    returns nothing for a non-English source title, and job 20261024-131810
    refused a model whose URL had registered on the form correctly.
    """
    if declared:
        return ""
    if not cand:
        if result.get("flagged"):
            return ("Creality's picker marks the source model No-Derivatives "
                    "(%s) — it cannot be declared as a remix"
                    % ", ".join(r.get("name", "")
                                for r in result["flagged"])[:160])
        return ("Creality's picker did not offer a clean match for the source "
                "model (%s), and pasting its URL did not register it either; "
                "shown: %s"
                % (source_name or slug,
                   ", ".join(result.get("rows", [])[:6])[:200]))
    return ("the form does not show OUR source model — clicked %r, the "
            "registered source reads %r (%s), so submitting would credit a "
            "model this listing was not made from"
            % (cand.get("name", ""), (why or "")[:80], why or "-"))


async def _registered_mark(work) -> dict:
    """What the form is showing as the chosen source.

    Two places, BOTH measured on the live form (2026-10-10):
      · `.origin-choose-mark` — the registered-source card some builds render
        beside the still-open suggestion list (`.origin-choose-list`).
      · `div.origin-list > div.origin-item.flex-center` — the pill the CURRENT
        form renders once a source row is clicked: `div.origin-name` holds the
        model name and `div.url` holds its full crealitycloud URL. This is the
        area the dashboard's own per-link verdict shows after a successful
        declare; the old code read ONLY `.origin-choose-mark`, found nothing,
        and the run refused with "Remix Models radio never revealed the source
        input" while the form was actually holding the declared source.
    Returns {text, href, in_list} — href is the FULL URL of the registered
    source when it is known (the origin-list pill renders its URL as text)."""
    try:
        raw = await work.evaluate(r"""
            () => {
                const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
                const out = {text: '', href: '', in_list: false};
                // 1) the pill the current form renders after a source is
                //    clicked: div.origin-list > .origin-item carries BOTH the
                //    model name and its full URL as a text row.
                const li = document.querySelector('div.origin-list .origin-item');
                if (li) {
                    const urlEl = li.querySelector('.url');
                    let href = urlEl ? clean(urlEl.innerText).slice(0, 200) : '';
                    if (href && !/^https?:\/\//i.test(href)) href = '';
                    out.href = href;
                    out.text = clean((li.querySelector('.origin-name') || li).innerText)
                        .replace(/https?:\/\/\S+$/, '').slice(0, 160);
                    out.in_list = !!li.querySelector('.el-icon-delete, .delete, [class*=delete]');
                    // a deleted/moving row would end with 'delete' text — prune
                    if (!out.href && out.text.toLowerCase().endsWith('delete'))
                        out.text = out.text.slice(0, -6).trim();
                    return JSON.stringify(out);
                }
                // 2) the older registered-source card beside the open list.
                const mark = document.querySelector('.origin-choose-mark');
                if (!mark) return JSON.stringify(out);
                out.in_list = !!mark.querySelector('.origin-item');
                const card = [...mark.querySelectorAll(':scope > *')]
                    .find(e => e !== mark.querySelector('.origin-choose-list'));
                const a = mark.querySelector('a[href*="/model-detail/"]');
                out.text = clean((card || mark).innerText).slice(0, 160);
                if (a) out.href = a.getAttribute('href');
                return JSON.stringify(out);
            }
        """)
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


def badge_from_icons(names) -> str:
    """Read a CC name off the badge's icon strip (image alt / filename).

    The strip is a few small images ("CC", "BY", "NC"), so when the badge's
    paragraph cannot be read this still names the licence. Refuses to guess
    from a lone CC mark or from a shared sprite."""
    blob = " ".join([n for n in (names or []) if n]).lower()
    blob = re.sub(r"\.(svg|png|webp|jpg|jpeg|gif)\b", " ", blob)
    blob = re.sub(r"[^a-z0-9]+", " ", blob)      # cc_by-nc.svg -> cc by nc
    if re.search(r"\bcc\s?0\b|\bcc ?zero\b|\bpublic domain\b", blob):
        return "CC0"
    marks = set(re.findall(r"\b(cc|by|nc|sa|nd)\b", blob))
    if "cc" not in marks or not (marks & {"by", "nc", "sa", "nd"}):
        return ""
    if "sa" in marks and "nd" in marks:
        return ""                          # contradictory -> not one badge
    return "CC BY" + "".join("-" + m.upper() for m in ("nc", "sa", "nd")
                             if m in marks)


# JS that reads the licence card's badge, scoped to the LICENCE CARD: the
# smallest block that holds both the adaptations question and the
# "This work is licensed under" caption. That keeps the Model Source panel's
# tip ("...licenses such as CC BY-ND and CC BY-NC-ND do not allow for
# derivative works") out of the reading — that sentence is what a page-wide
# search mistook for the badge, so a correct CC BY-NC form was refused.
# Kept as a module constant so the SAME code can be replayed offline against a
# saved copy of the real card (scripts/check_license_capture.py).
LICENSE_BADGE_JS = r"""
    () => {
        const Q1 = 'Allow adaptations of your work to be shared?';
        const CAP = 'This work is licensed under';
        const has = (e, s) => ((e.innerText || '').indexOf(s) !== -1);
        const cards = [...document.querySelectorAll('div,section,form,fieldset')]
            .filter(e => has(e, Q1) && has(e, CAP));
        cards.sort((a, b) => ((a.innerText || '').length - (b.innerText || '').length));
        const root = cards[0] || document.body;
        // the badge name has its own paragraph and STARTS with it
        const TOK = /^\s*(CC\s?0|CC\s?BY(?:\s*[-/]?\s*(?:NC|SA|ND)){0,2}|Public\s+Domain)/i;
        let badge = '';
        for (const e of root.querySelectorAll('div,span,p,section,td,li,a,b')) {
            const t = (e.innerText || '').trim();
            if (t && TOK.test(t) && (!badge || t.length < badge.length)) badge = t;
        }
        // the icon strip beside it names the same licence
        let icons = [];
        try {
            icons = [...root.querySelectorAll('img')]
                .map(i => ((i.alt || '') + ' ' + (i.src || '').split('/').pop()).trim())
                .filter(Boolean).slice(0, 8);
        } catch (e) {}
        return JSON.stringify({
            in_card: !!cards.length,
            badge: badge.slice(0, 300),
            icons: icons,
            card: (root.innerText || '').replace(/\s+/g, ' ').slice(0, 400),
        }).slice(0, 1400);
    }
"""


def _norm_cc(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).upper()


# ---------------------------------------------------------------------------
# Photos -> form slots. Measured on the live create-model form:
#   * the two REQUIRED cover slots are the first two image inputs of the cover
#     card, in document order: "Web cover ratio 4:3" then "App cover ratio 3:4".
#     They are independent slots, so sending photo 1 to BOTH (what the old
#     index-guessing code did) is why the listing showed the same image twice.
#   * the "Model Images (x/9)" gallery is the `multiple` input inside
#     .upload-cover-last. Its trigger reads "Image", not "Upload", so the old
#     text-based lookup never found it and the gallery stayed empty — the
#     counter read 0/9 after a 6-photo upload.
# Every photo is used once, in the creator's order: the covers take the first
# two (the app cover only when there IS a second photo) and the gallery takes
# the whole set in order, which is how the SOURCE listing looks.
# ---------------------------------------------------------------- consent ---
# The create-model form ends with "I have read and agree to Copyright
# Statement." and Submit stays blocked until it is ticked. Measured on the live
# form over CDP (2026-10-08), the row is Element UI's checkbox:
#
#   label.el-checkbox                         the row (gains is-checked)
#     span.el-checkbox__input                 the circle, 16x16 — click here
#       input.el-checkbox__original[type=checkbox]
#       span.el-checkbox__inner
#     span.el-checkbox__label
#       "I have read and agree to " + <strong>Copyright Statement.</strong>
#       div.tips > span "Please check the box to agree before submitting"
#
# THE BUG THIS SOLVES: while the box is UNTICKED — exactly when the finder has
# to act — that warning is a child of the label, so the label's own text is 93
# chars ("...Copyright Statement. Please check the box to agree before
# submitting") and the old finder's `< 90` length cap filtered EVERY match out:
# found=false, no candidates, nothing clicked. The box only got ticked when a
# locator fallback happened to click the row; then the warning disappeared, the
# text fell to 45 chars and the same finder suddenly worked (which is also how
# a submit could end up reported against "agree to"). The finder below matches
# the live DOM: it finds the row by its real structure (label.el-checkbox
# carrying the phrase, with a text-only fallback for other page builds and no
# cap the warning text can push out), reads the tick from the row's own
# checkbox input (plus is-checked classes), and clicks the visible circle with
# a REAL mouse click — proven live to toggle the input false -> true -> false.
_CONSENT_HELPER = r"""
    const CONSENT_PHRASE = /I have read and agree/i;
    const consentRowFor = () => {
        const labs = [...document.querySelectorAll('label.el-checkbox,.el-checkbox')]
            .filter(e => CONSENT_PHRASE.test(e.textContent || ''));
        if (labs.length)
            return labs.sort((a, b) => (a.textContent || '').length -
                                       (b.textContent || '').length)[0];
        const all = [...document.querySelectorAll('div,span,p,label,section')]
            .filter(e => CONSENT_PHRASE.test(e.textContent || ''));
        const minimal = all.filter(e => ![...e.children]
            .some(c => CONSENT_PHRASE.test(c.textContent || '')));
        return (minimal.length ? minimal : all)
            .sort((a, b) => (a.textContent || '').length -
                            (b.textContent || '').length)[0] || null;
    };
    const consentChecked = (row) => {
        if (!row) return false;
        const inp = row.querySelector('input[type=checkbox]') ||
                    (row.parentElement
                        ? row.parentElement.querySelector('input[type=checkbox]') : null);
        if (inp && inp.checked) return true;
        const els = [row, row.parentElement,
                     row.querySelector('.el-checkbox__input,.el-checkbox__inner,' +
                                       '.el-radio__inner,[class*=circle]')];
        for (const el of els) {
            if (!el) continue;
            const c = (el.className || '').toString();
            if (/(^|[-_ ])(is-)?checked|(^|[-_ ])selected|(^|[-_ ])agree/.test(c))
                return true;
        }
        return false;
    };
"""

CONSENT_JS = ("() => { " + _CONSENT_HELPER + r"""
    return new Promise((res) => {
        const row = consentRowFor();
        if (!row) { res(JSON.stringify({found: false})); return; }
        row.scrollIntoView({block: 'center'});
        requestAnimationFrame(() => requestAnimationFrame(() => {
            const input = row.querySelector('input[type=checkbox]') ||
                          row.querySelector('input');
            const circle = row.querySelector('.el-checkbox__input,.el-checkbox__inner,' +
                                             '.el-radio__inner,[class*=circle]');
            const point = (el) => {
                if (!el) return null;
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) return null;
                return {x: Math.round(r.x + r.width / 2),
                        y: Math.round(r.y + r.height / 2),
                        w: Math.round(r.width), h: Math.round(r.height),
                        cls: (el.className || '').toString().slice(0, 48)};
            };
            const pts = {};
            if (circle) pts.circle = point(circle);
            const pr = point(row);
            if (pr) pts.label = pr;
            res(JSON.stringify({
                found: true,
                row: ((row.innerText || row.textContent || ''))
                     .replace(/\s+/g, ' ').trim().slice(0, 110),
                row_cls: (row.className || '').toString().slice(0, 70),
                input_html: input ? input.outerHTML.replace(/\s+/g, ' ').slice(0, 140) : '',
                checked: consentChecked(row),
                pts: pts,
            }));
        }));
    });
}""")

CONSENT_STATE_JS = ("() => { " + _CONSENT_HELPER + r"""
    return consentChecked(consentRowFor());
}""")


# Submit outcome reading. The form's own static label reads "I have read and
# agree to Copyright Statement.", so a page-wide 'agree to' match used to call a
# merely-slow submit a rejection ("Creality rejected the submission (agree to)").
# Only Creality's own blocker toast counts, and it is handled by re-ticking the
# box and pressing Submit once more.
SUBMIT_AGREE_BLOCK_RE = re.compile(
    r"please check the box to agree|agree before submitting|please agree to", re.I)
SUBMIT_REJECT_RE = re.compile(
    r"submit failed|upload failed|failed to|not allowed|"
    r"please (?:fill|select|complete|add|check)|is required|cannot be empty", re.I)


# JS fallback when no real mouse click lands on the circle: dispatch a click on
# the control itself (the hidden input, the styled circle, or the row).
CONSENT_CLICK_JS = ("() => { " + _CONSENT_HELPER + r"""
    const row = consentRowFor();
    if (!row) return null;
    if (consentChecked(row)) return true;
    const targets = [row.querySelector('input[type=checkbox]'),
                     row.querySelector('.el-checkbox__input,.el-checkbox__inner,' +
                                       '.el-radio__inner,[class*=circle]'),
                     row];
    for (const t of targets) {
        if (!t) continue;
        try { t.click(); } catch (e) {}
        if (consentChecked(row)) return true;
    }
    return consentChecked(row);
}""")

# Locate the step-2 Submit control: a styled <div class="submit">, not a real
# <button>, so it is found by its text + size and clicked via translated page
# coordinates (iframe offset + element center). Used by step 9 (to verify the
# footer point is reachable before the tick) and by step 10 (to press it).
SUBMIT_JS = r"""
    () => {
        const els = [...document.querySelectorAll('button,div,span,a,[role=button]')];
        const cand = els.filter(e => {
            if ((e.innerText||'').trim() !== 'Submit') return false;
            const r = e.getBoundingClientRect();
            return r.width>40 && r.width<400 && r.height>20 && r.height<120;
        });
        if (!cand.length) return null;
        cand.sort((a,b)=>{const ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect();
            return ra.width*ra.height - rb.width*rb.height;});
        const e = cand[0]; e.scrollIntoView({block:'center'});
        const r = e.getBoundingClientRect();
        return {x: r.x + r.width/2, y: r.y + r.height/2};
    }
"""


# What the form LOOKS like after a failed Submit: which wizard step it went back
# to, which field Creality put in its error state, and any message it printed.
# A bounce with no message is the failure mode the authorised publish hit — it
# must be read back, not guessed at.
REJECTION_JS = r"""
    () => {
        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
        const vis = (e) => !!(e.offsetWidth || e.offsetHeight);
        let step = null;
        // the wizard's active step is its green badge (rgb(26, 198, 134))
        const badge = [...document.querySelectorAll('div,span,li')]
            .filter(e => /^[12]$/.test(clean(e.textContent))
                && getComputedStyle(e).backgroundColor.replace(/\s/g, '') === 'rgb(26,198,134)')[0];
        if (badge) step = parseInt(clean(badge.textContent), 10);
        const flagged = [];
        for (const e of document.querySelectorAll('.err-box,.el-form-item.is-error,[class*="err-"]')) {
            const cls = (e.className || '').toString();
            if (!/err-/.test(cls)) continue;
            // the FLAGGED element names its own field (Creality renders the
            // field's title inside the card that carries err-box — an outer
            // .el-form-item label would name the whole section instead)
            let label = clean((e.innerText || '').split('\n')[0]).slice(0, 60);
            if (!label) {
                const h = e.querySelector('h1,h2,h3,h4,[class*=title]');
                if (h) label = clean(h.textContent);
            }
            if (!label) {
                const item = e.closest('.el-form-item');
                if (item) {
                    const l = item.querySelector('.el-form-item__label,[class*=label]');
                    if (l) label = clean(l.textContent);
                }
            }
            flagged.push({cls: cls.slice(0, 60), label: label.slice(0, 60)});
        }
        const messages = [...document.querySelectorAll(
                '.el-form-item__error,.el-message,.el-notification,div.tips')]
            .filter(vis).map(e => clean(e.innerText)).filter(Boolean).slice(0, 5);
        return {step: step, flagged: flagged.slice(0, 5), messages: messages,
                url: location.href, text_head: clean(document.body.innerText).slice(0, 200)};
    }
"""


def describe_submit_failure(step=None, flagged=(), net=(), submit_step=2,
                            fallback="") -> str:
    """One sentence saying what CREALITY did with the Submit press — the field it
    flagged, the create request's response, and the step it went back to — instead
    of only "never confirmed". A bare step number is NOT a rejection on its own:
    the sentence is only built when there is real evidence (a flagged field, a
    create response, or the form no longer being on the step Submit was pressed
    from). Pure so it can be checked without a browser."""
    flagged, net = list(flagged or []), list(net or [])
    bounced = step is not None and step != submit_step
    if not (flagged or net or bounced):
        return fallback
    bits = []
    if bounced:
        bits.append("the form bounced back to step %s" % step)
    for f in flagged[:3]:
        bits.append("field %r is in Creality's error state (%s)"
                    % ((f.get("label") or "?"), (f.get("cls") or "?")))
    for n in net[-2:]:
        body = (n.get("body") or "").replace("\n", " ")[:200]
        bits.append("%s %s -> HTTP %s%s"
                    % (n.get("method") or "?", (n.get("url") or "")[:80],
                       n.get("status") or "?", (" " + body) if body else ""))
    return ("Creality rejected the submission: " + "; ".join(bits)
            + ". Nothing was published.")


def cover_attach_plan(plan: dict, zones: dict) -> list:
    """Which form zone gets which photo(s), in order. The two cover slots are
    INDEPENDENT inputs, so with a single photo the same file fills both covers
    (image_plan() already returns app = web for that case) — the old guard
    compared the FILE and skipped the app slot, leaving the required 3:4 cover
    empty. Zones that do not exist (-1/None) are skipped."""
    def real(z):
        return z is not None and z >= 0
    out = []
    if plan.get("web") and real(zones.get("web")):
        out.append(("web", [plan["web"]]))
    if plan.get("app") and real(zones.get("app")) \
            and zones.get("app") != zones.get("web"):
        out.append(("app", [plan["app"]]))       # same file is fine — separate slot
    if plan.get("gallery") and real(zones.get("gallery")):
        out.append(("gallery", list(plan["gallery"])))
    return out


def dedupe_images(paths) -> list[str]:
    """Drop repeats by file CONTENT (not just name), keeping the original
    order — the upload used to carry the same photo in two slots."""
    seen, out = set(), []
    for p in (paths or []):
        if not p:
            continue
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as fh:
                key = hashlib.sha1(fh.read()).hexdigest()
        except Exception:
            key = os.path.basename(p).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def image_plan(paths, gallery_max: int = 9) -> dict:
    """Which photo fills which slot: web cover 4:3, app cover 3:4, and the
    Model Images gallery (the whole set, in the creator's original order).

    Every file is also made uploadable first: the form's image inputs accept
    only .jpeg/.png/.jpg/.webp/.gif, so a gallery photo served as .avif (or any
    other odd format) is converted rather than silently dropped by the form.
    """
    imgs = dedupe_images(paths)
    converted = []
    try:
        from downloader import to_uploadable       # lazy: no import cycle
        fixed = []
        for p in imgs:
            q = to_uploadable(p)
            if q and q != p:
                converted.append([os.path.basename(p), os.path.basename(q)])
            if q:
                fixed.append(q)
            else:
                UPLOAD_VERIFY.setdefault("image_errors", []).append(
                    "unreadable image dropped: " + os.path.basename(p))
        imgs = fixed
    except Exception as e:                          # never block the upload path
        UPLOAD_VERIFY.setdefault("image_errors", []).append(
            "format check skipped: " + str(e)[:120])
    web = imgs[0] if imgs else None
    app = imgs[1] if len(imgs) > 1 else web      # same shot is unavoidable with 1
    return {"all": imgs, "web": web, "app": app,
            "gallery": imgs[:gallery_max],
            "converted": converted,
            "dropped": max(0, len(imgs) - gallery_max)}


def _stop_pressed(should_stop) -> bool:
    """True when the run's Stop was pressed (the hook is optional)."""
    return bool(should_stop and should_stop())


def _stopped_result() -> dict:
    """What a publish that stopped before Submit reports: nothing was submitted,
    so the caller leaves the model undecided and never records it."""
    return {"success": False, "stopped": True,
            "error": "run stopped before Submit — nothing was submitted",
            "verify": dict(UPLOAD_VERIFY)}


# Playwright reads a set_input_files payload off disk and STREAMS the bytes to
# the browser, capped at 50 MB unless the browser shares the driver's machine —
# and a CDP connection to the account's own Chrome always reads as "not
# co-located". A 73.55 MB 3MF on 2026-10-24 was refused with "Cannot transfer
# files larger than 50Mb to a browser not co-located with the server": a
# transport limit, not Creality's, since the same file uploads by hand. CDP's
# DOM.setFileInputFiles passes the file PATHS to the browser process instead of
# the bytes, so it carries no size cap.
ATTACH_CDP_CAP_S = 50 * 1024 * 1024


def attach_needs_cdp(paths, cap: int = ATTACH_CDP_CAP_S) -> bool:
    """True when the payload is big enough that Playwright would refuse it."""
    try:
        return sum(os.path.getsize(p) for p in paths) >= cap
    except OSError:
        return True          # a size we cannot read: take the path-based route


def file_input_nodes(root: dict, wants=("stl", "step", "3mf", "obj")) -> list:
    """The model-file <input type=file> nodes of a DOM.getDocument tree.

    Read with pierce:true, so a form living in the legacy same-origin iframe is
    walked like one on the page. CDP hands attributes back as a flat
    ["name", "value", ...] list, so pairs are zipped here."""
    found = []
    stack = [root]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        for key in ("children", "pierceChildren"):
            stack.extend(n.get(key) or [])
        if (n.get("nodeName") or "").upper() != "INPUT":
            continue
        a = n.get("attributes") or []
        attrs = {}
        for i in range(0, len(a) - 1, 2):
            attrs[str(a[i]).lower()] = str(a[i + 1])
        if attrs.get("type", "").lower() != "file":
            continue
        accept = (attrs.get("accept") or "").lower()
        if n.get("nodeId") is not None and any(w in accept for w in wants):
            found.append(n["nodeId"])
    return sorted(found)


async def _attach_files_by_path(ctx, page, paths, wants=("stl", "step", "3mf", "obj")) -> str:
    """Give the browser the file paths through CDP — no 50 MB streaming cap."""
    cdp = await ctx.new_cdp_session(page)
    try:
        doc = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        nodes = file_input_nodes(doc.get("root") or {}, wants)
        if not nodes:
            raise RuntimeError("no model-file input in the DOM tree")
        await cdp.send("DOM.setFileInputFiles",
                       {"files": [os.path.abspath(p) for p in paths],
                        "nodeId": nodes[0]})
        return "cdp paths, input node %d, %d file(s)" % (nodes[0], len(list(paths)))
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


async def upload(name: str, description: str, stl_path: str, image_paths: list[str],
                 source_url: str = "", dry_run: bool = False,
                 license_raw: str = "", should_stop=None) -> dict:
    # Per-step timings. main.py times the publish as a whole, so without these
    # marks a 40 s publish is one number with nothing to point at — which is
    # how the slow step stayed hidden while the census blamed "the form".
    _mark = timing.Steps("publish")

    # Auto-launch Chrome with the debug port if it isn't already running, so
    # the user never has to start it by hand (and never sees ECONNREFUSED).
    # Both calls BLOCK (a 2s port probe; up to 20s while Chrome is launched).
    # Awaiting them from this async function froze the entire dashboard -- every
    # request, not only the publish -- for as long as Chrome took to come up.
    if not await asyncio.to_thread(_debug_port_up):
        if not await asyncio.to_thread(ensure_browser):
            return {"success": False, "error": "Could not start Chrome automatically. "
                    "Open Chrome once via the debug launcher, log into MakerWorld + Creality, then retry."}
        # give the freshly-launched browser a moment to settle
        await asyncio.sleep(2)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            return {"success": False, "error": f"Could not attach to Chrome on {CDP_URL}. ({e})"}
        ctx = browser.contexts[0]

        # 0) Sweep the form tabs a previous publish left behind. Every publish
        #    opens a fresh tab below and only closes it when the submit worked,
        #    so a failure (or a stopped run) used to leave its half-filled form
        #    open for good: 20 of them at once is a Chrome that runs out of
        #    memory long before the run is through. The user's own tabs are never
        #    touched — only the create-model form this pipeline opened.
        try:
            for _stale in list(ctx.pages):
                try:
                    if "create-model" in (_stale.url or ""):
                        await _stale.close()
                except Exception:
                    pass
        except Exception:
            pass

        _mark("browser")
        # 1) Model files — for THIS model only. Prefer a file the fetch already
        #    produced; otherwise download the exact model from its source URL.
        #    No "grab any leftover file" fallback — that caused the wrong (old)
        #    model to be re-uploaded and tripped Creality's duplicate check.
        model_files = [stl_path] if (stl_path and os.path.exists(stl_path)) else []
        if model_files and model_files[0].lower().endswith((".zip", ".rar", ".7z")):
            # Creality's own "download all" answers with a ZIP of model files
            # (<model>_part1.zip holding the .3mf files). The form takes model
            # files, not archives, so unpack them here — uploading the .zip
            # itself is a file the form refuses.
            unpacked = _extract_models(model_files[0])
            if unpacked:
                model_files = unpacked
        if not model_files:
            # Try twice — the MakerWorld download can transiently time out.
            for _attempt in range(2):
                model_files = await _download_from_makerworld(ctx, source_url)
                if model_files:
                    break
        if not model_files:
            why = UPLOAD_VERIFY.get("last_attempt")
            return {"success": False, "error": "Couldn't get the model file(s) for THIS model. "
                    "Open its MakerWorld page (logged in) or paste a direct file, then retry. "
                    "(I no longer fall back to a previously-downloaded model.)",
                    "acquire_detail": why}
        # Upload every real part, largest first (so the primary geometry leads).
        model_files = sorted(model_files, key=lambda p: os.path.getsize(p), reverse=True)

        # Proof of what we're about to publish — which file, from which model,
        # and whether it contains real 3D geometry. Reported in the response
        # instead of assumed.
        _verify_model_files(model_files, source_url)
        verify = dict(UPLOAD_VERIFY)

        if dry_run or os.environ.get("UPLOAD_DRY_RUN") == "1":
            # Stop right before anything touches Creality. Everything up to and
            # including model-file acquisition has run for real.
            return {"success": True, "dry_run": True, "verify": verify,
                    "model_files": [os.path.basename(p) for p in model_files],
                    "message": "Dry run: model file(s) acquired and verified; nothing was uploaded."}

        # Red cover images — only the ones for THIS model (no glob fallback that
        # would pull in a previous model's edited images). The check is one
        # function so the run can apply the SAME rule before it ever hands a
        # model to the publisher (models 5 and 6 of the user's own run were
        # carried all the way here just to die on this line).
        reds = usable_cover_images(image_paths)
        if not reds:
            return {"success": False, "error": NO_COVERS_ERROR}

        if _stop_pressed(should_stop):
            return _stopped_result()
        _mark("files")
        # 2) ALWAYS a fresh tab on a known-good URL. Never reuse an existing
        #    crealitycloud tab: reusing is what drove "random links to older
        #    models" — the old code picked the user's half-filled form tab and
        #    navigated it away (measured 2026-10-13). The leftovers are closed
        #    too, so nothing from a previous attempt stays around.
        page = await ctx.new_page()
        await page.bring_to_front()
        # the ENGLISH form address, forced with the /en/ prefix: the account's
        # Chrome otherwise comes up in the account/geo locale (measured live
        # 2026-10-13: the plain URL rendered the FRENCH form — "Modèles
        # modifiés", "Collez l'URL", button "Suivant" — and the run died with
        # 'Remix Models radio never revealed the source input'. The /en/ probe
        # (scripts/check_form_lang.py) returns the English form: "Remix Models",
        # "Paste the URL", button "Next".
        await page.goto("https://www.crealitycloud.com/en/create-model-new?source=12",
                        wait_until="domcontentloaded", timeout=40000)
        # if the site still dropped us on a non-English form, force /en/ once
        # more and wait for the English step-1 wording before filling anything.
        try:
            for _ in range(20):
                body = await page.evaluate("() => document.body.innerText.slice(0, 4000)")
                if not re.search(r"Modèles modifiés|Collez l'URL|Suivant", body or ""):
                    break
                await page.goto("https://www.crealitycloud.com/en/create-model-new?source=12",
                                wait_until="domcontentloaded", timeout=40000)
                await page.wait_for_timeout(2000)
        except Exception:
            pass

        # The form used to be rendered inside an iframe (flowprint/create-model);
        # the site now renders it directly on the page (create-model-new redirects
        # to create-model). Support BOTH: prefer the main page's own file inputs,
        # fall back to the legacy iframe. Poll up to ~30s.
        work = None           # Playwright Page or Frame that hosts the form
        for _ in range(120):
            try:
                if await page.locator("input[type=file]").count() > 0:
                    work = page
                    break
                iframe = next((f for f in page.frames
                               if "flowprint/create-model" in f.url), None)
                if iframe and await iframe.locator("input[type=file]").count() > 0:
                    work = iframe
                    break
            except Exception:
                pass
            await page.wait_for_timeout(250)
        is_frame = work is not page and work is not None
        if work is None:
            # Most common cause: not logged into Creality in this Chrome profile.
            body = ""
            try:
                body = (await page.evaluate("() => document.body.innerText")).lower()
            except Exception:
                pass
            if "login" in page.url.lower() or "sign in" in body or "log in" in body:
                return {"success": False, "error": "You're not logged into Creality Cloud in the "
                        "Chrome window. Log in there once (the profile remembers it), then retry."}
            return {"success": False, "error": "Creality's upload form didn't load (the page may be "
                    "slow or showing a Cloudflare check). Wait a few seconds and retry."}

        if _stop_pressed(should_stop):
            return _stopped_result()
        _mark("form_open")
        # 3) Select the "STL/CAD files..." radio. The measured live form (October
        #    13, debug/repro_remix.jpg): get_by_text() never resolves this label —
        #    it is plain div text next to an .el-radio — and the form's default is
        #    the "Print Configuration File" radio, whose required Print-Settings
        #    slot then blocks Next FOREVER, so step 2 never appears and the publish
        #    dies far downstream with 'Remix Models radio never revealed the source
        #    input'. Drive the radio through the DOM and VERIFY it took the checked
        #    state; alternate means until .el-radio.is-checked carries our text.
        _stl_texts = re.compile(r"STL/CAD files or other types of 3MF file"
                                r"|Fichiers STL/CAO ou autres types de fichier 3MF", re.I)
        try:
            _stl_ok = await work.evaluate(
                r"""(args) => {
                    const clean = (s) => (s || '').trim();
                    const want = args[0];
                    const labels = [...document.querySelectorAll('label.el-radio, .el-radio, div.el-radio')];
                    const hit = labels.find(e => want.test(clean(e.innerText)));
                    if (!hit) return 'no-radio';
                    const target = hit.closest('label') || hit;
                    target.click();
                    return 'clicked:' + clean(hit.innerText).slice(0, 30);
                }""", [_stl_texts.pattern if hasattr(_stl_texts, 'pattern') else str(_stl_texts)])
        except Exception:
            _stl_ok = "eval-failed"
        if not str(_stl_ok).startswith("clicked"):
            # last resort: click the label's own coordinates through the DOM box
            try:
                bb = await work.get_by_text(_stl_texts, exact=False).first.bounding_box()
                if bb:
                    await page.mouse.click(bb["x"] + bb["width"] / 2,
                                           bb["y"] + bb["height"] / 2)
                    _stl_ok = "mouse-click"
            except Exception:
                pass
        for _ in range(20):                      # ~6s: did the radio CHECK?
            try:
                st = await work.evaluate(r"""() => {
                    const clean = (s) => (s || '').trim();
                    const hit = [...document.querySelectorAll('label.el-radio, .el-radio, div.el-radio')]
                        .find(e => /STL\\/CAD/i.test(clean(e.innerText)));
                    return hit && hit.classList.contains('is-checked') ? 'checked' : 'not';
                }""")
                if st == 'checked':
                    break
            except Exception:
                pass
            await page.wait_for_timeout(300)

        _mark("radio")
        # 4) Attach the model file(s) to the model-files input. Upload ALL real
        #    parts at once; if the input rejects multiples, fall back to the
        #    largest single file so the primary geometry still gets in.
        #    A payload over Playwright's 50 MB streaming cap goes through CDP as
        #    file PATHS instead, which is how a 73 MB part gets attached at all.
        total_mb = round(sum(os.path.getsize(p) for p in model_files) / 1e6, 1)
        attach_how = ""
        try:
            model_input = work.locator("input[type=file][accept*='.step'], input[type=file][accept*='.stl']").first
            if attach_needs_cdp(model_files):
                try:
                    attach_how = await _attach_files_by_path(ctx, page, model_files)
                except Exception as cdp_err:
                    return {"success": False,
                            "error": ("Could not attach the %.1f MB model file(s) by path: %s"
                                      % (total_mb, str(cdp_err)[:180])),
                            "verify": dict(UPLOAD_VERIFY)}
            else:
                try:
                    await model_input.set_input_files(model_files)
                    attach_how = "playwright, %d file(s)" % len(model_files)
                except Exception:
                    await model_input.set_input_files(model_files[0])
                    attach_how = "playwright, largest file only"
        except Exception as e:
            return {"success": False, "error": f"Could not attach the model file: {e}"}
        UPLOAD_VERIFY["attach"] = attach_how
        # Wait only until the file name registers (fast poll, not a fixed sleep)
        stem = os.path.splitext(os.path.basename(model_files[0]))[0][:18]
        registered = False
        for _ in range(40):
            try:
                if await work.evaluate(f"() => document.body.innerText.includes({stem!r})"):
                    registered = True
                    break
            except Exception:
                pass
            await page.wait_for_timeout(300)
        UPLOAD_VERIFY["attach_registered"] = registered
        if not registered:
            UPLOAD_VERIFY["attach_note"] = (
                "the form never showed the file name %r after %s — the attach "
                "itself is what to look at, not the later steps" % (stem, attach_how))

        if _stop_pressed(should_stop):
            return _stopped_result()
        _mark("attach")
        # 5) Next -> step 2. The Next control is a <div class="step-one-btn submit">
        #    inside the iframe (not a real <button>). After the click, VERIFY that
        #    step 2 (the name field) actually appeared: a Next click that silently
        #    no-ops (a required Print-Settings slot still red, a toast) used to
        #    surface only 40 s later as 'Remix Models radio never revealed the
        #    source input', blaming the wrong step entirely.
        for sel in [".step-one-btn.submit", ".step-one-btn"]:
            try:
                loc = work.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=4000)
                    break
            except Exception:
                pass
        else:
            try:
                await work.get_by_text("Next", exact=True).last.click(timeout=4000)
            except Exception:
                pass
        nm = work.locator("input[placeholder*='model name' i]").first
        step2_up = False
        try:
            await nm.wait_for(state="visible", timeout=10000)
            step2_up = True
        except Exception:
            pass
        if not step2_up:
            for _ in range(3):                   # 3 more Next presses with pauses
                try:
                    await work.locator(".step-one-btn.submit").first.click(timeout=4000)
                except Exception:
                    pass
                try:
                    await nm.wait_for(state="visible", timeout=8000)
                    step2_up = True
                    break
                except Exception:
                    pass
        if not step2_up:
            # report what the form itself complained about, so the failure names
            # the real blocker (the red Print-Settings slot, a network blip...)
            why = ""
            try:
                body = await work.evaluate("() => document.body.innerText")
                mm = re.search(r"[^.]{0,120}(?:required|please|must|fill|upload)[^.]{0,120}",
                               body or "", re.I)
                why = mm.group(0).strip()[:160] if mm else "(no complaint read)"
            except Exception:
                pass
            return {"success": False, "error":
                    "The publish form never left step 1 after Next "
                    "(STL/CAD radio: %s; the form says: %s). Nothing was uploaded."
                    % (_stl_ok, why)}

        _mark("next")
        # 6) Name — the field is up (step2_up), fill it. A listing without a
        #    name is exactly what Creality refuses at Submit with "Please
        #    fill" and the name field's err-box (measured 2026-10-13), so the
        #    fill is verified in the DOM before anything else runs.
        try:
            await nm.click(timeout=4000)
            await nm.fill(name or "Untitled Model")
            filled = await work.evaluate(
                """() => {
                    const el = document.querySelector(
                        \"input[placeholder*='model name' i]\");
                    return el ? (el.value || '').trim() : '';
                }""")
            if not filled:
                await nm.fill(name or "Untitled Model")
        except Exception:
            pass

        if _stop_pressed(should_stop):
            return _stopped_result()
        _mark("name")
        # 7) Cover images + the "Model Images" gallery, addressed BY LABEL and
        #    read back from the form's own counter.
        #      * the two REQUIRED cover slots are the first two image inputs of
        #        the cover card, in document order: web 4:3 then app 3:4. They
        #        are independent, so photo 1 goes to web and photo 2 to app —
        #        the old code sent photo 1 to both, which is what put the same
        #        image in two slots.
        #      * the gallery is the `multiple` input inside .upload-cover-last.
        #        Its trigger reads "Image" (not "Upload"), so the old text-based
        #        lookup never found it: the gallery stayed empty and the listing
        #        was missing most of the creator's photos.
        #    Every selected photo is uploaded, once, in the creator's order.
        plan = image_plan(reds)
        reds = plan["all"]

        ZONE_JS = """
            () => {
                const all = [...document.querySelectorAll('input[type=file]')];
                const wrap = [...document.querySelectorAll(
                    '.model-cover-content > .upload-model-card_wrapper')];
                const ratios = wrap.length
                    ? [...wrap[0].querySelectorAll('input[type=file]')] : [];
                const galBox = document.querySelector('.upload-cover-last');
                const gal = galBox
                    ? [...galBox.querySelectorAll('input[type=file]')] : [];
                const multi = gal.find(i => i.multiple) || gal[0] || null;
                return JSON.stringify({
                    web: ratios.length ? all.indexOf(ratios[0]) : -1,
                    app: ratios.length > 1 ? all.indexOf(ratios[1]) : -1,
                    gallery: multi ? all.indexOf(multi) : -1,
                });
            }
        """

        async def _zones() -> dict:
            try:
                return json.loads(await work.evaluate(ZONE_JS) or "{}")
            except Exception:
                return {}

        async def _confirm_btn():
            return work.get_by_role("button", name="Confirm").last

        async def _wait_for(btn, state, ms):
            """Wait up to `ms` for the crop modal's button to appear/vanish. The
            same grace the old fixed sleeps gave, but it returns the moment the
            modal is actually there — this ran once per cover slot, so ~10 times
            a model, and a slot that crops instantly used to pay it anyway."""
            try:
                await btn.wait_for(state=state, timeout=ms)
                return True
            except Exception:
                return False

        async def _clear_crop_modals(rounds=10):
            """Cover slots open a cropping modal; confirm whatever is showing.
            The modal can take a moment to appear, so keep looking for a short
            while after the last one is dismissed."""
            misses = 0
            btn = await _confirm_btn()
            await _wait_for(btn, "visible", 500)
            for _ in range(rounds):
                clicked_now = False
                try:
                    if await btn.is_visible():
                        await btn.click(timeout=2500)
                        clicked_now = True
                except Exception:
                    pass
                if clicked_now:
                    misses = 0
                    await _wait_for(btn, "hidden", 700)
                    continue
                misses += 1
                if misses >= 3:
                    break
                await _wait_for(btn, "visible", 400)

        async def _attach(idx, files):
            if idx is None or idx < 0 or not files:
                return False
            try:
                await work.locator("input[type=file]").nth(idx).set_input_files(files)
            except Exception as e:
                UPLOAD_VERIFY.setdefault("image_errors", []).append(str(e)[:140])
                return False
            await _clear_crop_modals()
            return True

        async def _gallery_count() -> int:
            """The form's own "Model Images (x/9)" counter — the read-back."""
            try:
                n = await work.evaluate("""
                    () => {
                        const m = (document.body.innerText || '')
                            .match(/Model Images\\s*\\((\\d+)\\/9\\)/);
                        return m ? parseInt(m[1], 10) : -1;
                    }
                """)
                return int(n)
            except Exception:
                return -1

        zones = await _zones()
        gal_before = await _gallery_count()
        for _zone, _files in cover_attach_plan(plan, zones):
            await _attach(zones.get(_zone), _files)   # web 4:3, app 3:4, gallery
        gal_after = await _gallery_count()
        if plan["gallery"] and gal_after != -1 \
                and (gal_after - gal_before) < len(plan["gallery"]):
            # the form did not take them all — retry only what is still missing
            missing = plan["gallery"][max(0, gal_after - gal_before):]
            await _attach((await _zones()).get("gallery"), missing)
            gal_after = await _gallery_count()
        gallery_ok = (not plan["gallery"] or gal_after == -1
                      or (gal_after - gal_before) >= len(plan["gallery"]))
        UPLOAD_VERIFY["images"] = {
            "photos_available": len(reds),
            "cover_web": os.path.basename(plan["web"] or ""),
            "cover_app": os.path.basename(plan["app"] or ""),
            "covers_differ": bool(plan["web"] and plan["app"] != plan["web"]),
            "gallery_sent": len(plan["gallery"]),
            "gallery_counter": [gal_before, gal_after],
            "gallery_ok": gallery_ok,
            "converted": plan.get("converted", []),
            "dropped_over_9": plan["dropped"],
            "zones": zones,
        }
        _im = UPLOAD_VERIFY["images"]
        img_note = ("%d photo(s) in the creator's order — web cover %s, app cover %s, "
                    "%d in the gallery%s%s"
                    % (len(reds), _im["cover_web"], _im["cover_app"],
                       _im["gallery_sent"],
                       (" (%d over the 9-image limit)" % _im["dropped_over_9"])
                       if _im["dropped_over_9"] else "",
                       (" [%d converted to a format the form accepts]"
                        % len(_im["converted"])) if _im["converted"] else ""))

        if _stop_pressed(should_stop):
            return _stopped_result()
        _mark("covers")
        # 8) "Auto-Generated" — Creality's AI fills Category + Tags + Description
        #    from the cover image. This removes the manual Category requirement.
        try:
            ag = work.get_by_role("button", name="Auto-Generated")
            if await ag.count() == 0:
                ag = work.get_by_text("Auto-Generated", exact=False)
            await ag.first.click(timeout=4000)
            # Wait until the Category field is populated (poll up to ~20s)
            for _ in range(40):
                await page.wait_for_timeout(500)
                filled = await work.evaluate("""
                    () => {
                        const inp = [...document.querySelectorAll('input')]
                            .find(i => /category/i.test(i.placeholder||'') ||
                                       /keywords to search or select a category/i.test(i.placeholder||''));
                        return inp ? !!inp.value : false;
                    }
                """)
                # also detect category tag chips appearing
                chips = await work.evaluate("() => document.querySelectorAll('.el-tag, .ant-tag, [class*=tag-item]').length")
                if filled or chips > 0:
                    break
        except Exception:
            pass

        _mark("ai_fill")
        # 8b) Description — use the REAL model description (from the source page),
        #     not Creality's AI text. Auto-Generated fills an AI description above;
        #     here we replace it with the actual description when we have one.
        real_desc = (description or "").strip()
        if len(real_desc) >= 20:
            try:
                set_ok = await work.evaluate("""
                    (text) => {
                        // Creality's description editor is a textarea or a
                        // contenteditable rich-text box. Find it near a
                        // "Description"/"Introduction" label and replace its content.
                        const ta = [...document.querySelectorAll('textarea')]
                            .find(t => /desc|introduc/i.test((t.placeholder||'') + (t.name||'')))
                            || [...document.querySelectorAll('textarea')].pop();
                        if (ta) {
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLTextAreaElement.prototype, 'value').set;
                            setter.call(ta, text);
                            ta.dispatchEvent(new Event('input', {bubbles:true}));
                            ta.dispatchEvent(new Event('change', {bubbles:true}));
                            return 'textarea';
                        }
                        const ce = [...document.querySelectorAll('[contenteditable=true]')]
                            .find(e => e.offsetParent !== null);
                        if (ce) {
                            ce.focus();
                            ce.innerHTML = '';
                            ce.textContent = text;
                            ce.dispatchEvent(new Event('input', {bubbles:true}));
                            return 'contenteditable';
                        }
                        return 'not-found';
                    }
                """, real_desc)
                print(f"[uploader] description set via: {set_ok}")
            except Exception as e:
                print(f"[uploader] description override failed: {e}")

        _mark("description")
        # 8c) Model Source — declare the remix HONESTLY. A re-upload of
        # someone else's model must never claim originality: select the
        # "Remix Models" radio and register the source model's URL in
        # Creality's source field ("Paste the URL or directly search for
        # models within Creality Cloud").
        if source_url:
            try:
                # The Model Source block lives in section.second-step, which is
                # display:none until the form has LEFT step 1 (files). If the
                # Next click at step 5 never took, clicking a hidden radio can
                # never reveal anything — the run died here with "Remix Models
                # radio never revealed the source input". Demand a VISIBLE
                # step 2 first: click the step-one submit again and wait for the
                # section to show, else this publish cannot be declared.
                sec_visible = await work.evaluate(
                    """() => {
                        const s = document.querySelector('section.second-step')
                                  || document.querySelector('.second-step');
                        return !!s && s.offsetParent !== null;
                    }""")
                if not sec_visible:
                    # ONE read can answer "is step 2 laid out?", and if it says yes
                    # the three 2.5s waits below are skipped entirely. When the read
                    # is unavailable the old loop runs exactly as it did, so this
                    # can only be faster or identical -- never worse.
                    try:
                        _pm = await step_verify.observe(work)
                        if _pm and step_verify.step_two_open(_pm).ok:
                            sec_visible = True
                            UPLOAD_VERIFY["step2_seen_by"] = "one page read"
                    except Exception:
                        pass
                if not sec_visible:
                    for _ in range(3):
                        try:
                            await work.locator(".step-one-btn.submit").first.click(timeout=4000)
                        except Exception:
                            pass
                        # wait for step 2 to be laid out — the same 2500 ms of
                        # grace, but it ends the moment the section is visible
                        # instead of sleeping it and only then looking
                        try:
                            await page.wait_for_function(
                                """() => !!document.querySelector(
                                       'section.second-step') &&
                                       document.querySelector('section.second-step').offsetParent !== null""",
                                timeout=2500)
                        except Exception:
                            pass
                        if await work.evaluate(
                                """() => !!document.querySelector(
                                       'section.second-step') &&
                                       document.querySelector('section.second-step').offsetParent !== null"""):
                            break
                    sec_visible = await work.evaluate(
                        """() => { const s = document.querySelector('section.second-step');
                                   return !!s && s.offsetParent !== null; }""")
                    if not sec_visible:
                        raise RuntimeError(
                            "the publish form never reached step 2, so the "
                            "model's source cannot be declared")
                # the search box, in the form's language: English 'Paste the
                # URL…' or French "Collez l'URL ou recherchez directement…"
                src_input = work.locator(
                    'input[placeholder*="Paste the URL" i], '
                    'input[placeholder*="Collez l\'URL" i], '
                    'input[placeholder*="recherchez directement" i]').first
                # Element UI radio is temperamental: click the VISIBLE
                # label.el-radio that carries the Remix text THROUGH THE DOM,
                # verify the radio actually took the checked state, and fall
                # back to real mouse coordinates on the label's bounding box.
                # (The old locator-based .first.click() resolved to an invisible
                # text node of the same wording and just timed out — the radio
                # was never checked, so the source input never appeared.)
                async def _radio_checked():
                    return await work.evaluate(r"""() => {
                        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
                        const hit = [...document.querySelectorAll('label.el-radio, .el-radio, div.el-radio')]
                            .filter(e => /Remix Models|Modèles modifiés/i.test(clean(e.innerText)))
                            .find(e => e.classList.contains('is-checked'));
                        return !!hit;
                    }""")

                revealed = False
                for _round in range(6):
                    _state = await work.evaluate(r"""() => {
                        const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
                        const labels = [...document.querySelectorAll('label.el-radio, .el-radio, div.el-radio')]
                            .filter(e => /Remix Models|Modèles modifiés/i.test(clean(e.innerText)));
                        if (!labels.length) return 'no-radio';
                        const hit = labels.find(e => e.offsetWidth || e.offsetHeight) || labels[0];
                        if (hit.classList.contains('is-checked')) return 'already-checked';
                        const r = hit.getBoundingClientRect();
                        hit.click();
                        return JSON.stringify({clicked: true, x: r.x + r.width / 2, y: r.y + r.height / 2,
                                               text: clean(hit.innerText).slice(0, 30)});
                    }""")
                    try:
                        info = json.loads(_state) if isinstance(_state, str) and _state.startswith("{") else _state
                    except Exception:
                        info = _state
                    # ask whether the radio took the checked state until it does,
                    # up to the 1200 ms this slept before checking once
                    checked = await _settle(page, 1200, 150, _radio_checked)
                    if checked:
                        # the radio took the checked state — the paste box shows
                        # with it. Do NOT break before confirming: a checked
                        # radio with an unconfirmed box used to fall through to
                        # 'never revealed' and kill a form that was actually
                        # open (measured 2026-10-13).
                        revealed = True
                        break
                    if isinstance(info, dict) and info.get("x"):
                        # the DOM click did not take: a real mouse click on the
                        # radio's own coordinates
                        try:
                            await page.mouse.click(info["x"], info["y"])
                        except Exception:
                            pass
                        if await _settle(page, 1000, 150, _radio_checked):
                            revealed = True
                            break
                    # the verdict comes from the DOM, not from a Playwright
                    # locator: locate() on 'input[placeholder*=...]' resolved a
                    # hidden twin elsewhere on the page and reported the box
                    # invisible even while the screenshot SHOWS it open in the
                    # Remix card (measured 2026-10-13). offsetWidth/Height on the
                    # element INSIDE the Remix block is the truth.
                    try:
                        shown = await work.evaluate(r"""() => {
                            const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
                            // the Remix block: the .el-radio whose text is Remix,
                            // walked up to the card that also holds the paste box
                            const radio = [...document.querySelectorAll('label.el-radio, .el-radio')]
                                .filter(e => /Remix Models|Modèles modifiés/i.test(clean(e.innerText)))
                                .find(e => e.classList.contains('is-checked'));
                            if (!radio) return false;
                            let card = radio.parentElement;
                            for (let i = 0; i < 5 && card; i++) {
                                const inp = card.querySelector("input[placeholder*='Paste the URL' i], input[placeholder*='Collez l' i]");
                                if (inp && (inp.offsetWidth || inp.offsetHeight)) return true;
                                card = card.parentElement;
                            }
                            // fallback: any visible paste box at all near the radio
                            const any = [...document.querySelectorAll("input[placeholder*='Paste the URL' i]")]
                                .some(e => e.offsetWidth || e.offsetHeight);
                            return !!any;
                        }""")
                        if shown:
                            revealed = True
                            break
                    except Exception:
                        pass
                if not revealed:
                    try:
                        await page.screenshot(path=os.path.join(
                            BASE, "debug", "remix_reveal_failed.png"))
                    except Exception:
                        pass
                    raise RuntimeError("Remix Models radio never revealed the "
                                       "source input after 6 attempts")
                try:
                    # the paste box is what the radio was supposed to reveal: wait
                    # for it, rather than sleeping 600 ms and hoping
                    await src_input.wait_for(state="visible", timeout=600)
                except Exception:
                    pass
                # The paste box is a SEARCH, and the model must be CLICKED from
                # the list it shows. The source model's own TITLE is searched
                # for (the URL alone never registers, and slug words miss a
                # non-English title). Red "does not allow for derivative works"
                # rows are never clicked, a row that does not really look like
                # the source is never clicked, and if no such row exists NOTHING
                # is clicked — a wrong or missing source must never be submitted.
                ms = re.search(r"model-detail/([^/?#]+)", source_url)
                slug = ms.group(1) if ms else ""
                words = [w for w in re.split(r"[-_]", slug) if len(w) > 3]

                async def _read_rows() -> dict:
                    try:
                        raw = await work.evaluate(SOURCE_ROWS_JS)
                        return json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        return {}

                last: dict = {}
                result: dict = {}

                async def _rows_now():
                    nonlocal last
                    last = await _read_rows()
                    return bool(last.get("rows"))

                # titles only — the URL is pasted on its own afterwards, where
                # Enter registers it instead of feeding a search list.
                for query in [q for q in (name, " ".join(words)) if q]:
                    await src_input.fill("")
                    await src_input.fill(query)
                    # the picker lists the moment its search answers: poll for the
                    # first row instead of sleeping 3500 ms per query (two queries
                    # a model), with the same figure kept as the cap
                    await _settle(page, 3500, 300, _rows_now)
                    if not last.get("rows"):
                        # no dropdown yet — a search box in this form often needs
                        # Enter before it lists anything
                        try:
                            await src_input.press("Enter")
                        except Exception:
                            pass
                        await _settle(page, 2500, 250, _rows_now)
                    result = pick_source_suggestion(last.get("rows"), name, slug)
                    if result["pick"] or result["flagged"]:
                        break

                clicked_name = ""
                cand = result.get("pick")
                # The paste box takes the source model's own URL as well as a
                # search ("Paste the URL or directly search for models within
                # Creality Cloud"). When the title search offered nothing
                # certain, pasting the exact URL is STRONGER evidence than any
                # look-alike row, so try it before refusing.
                if cand is None and source_url:
                    async def _mark_shown():
                        return bool((await _registered_mark(work)).get("href"))
                    try:
                        await src_input.fill(source_url)
                        await src_input.press("Enter")
                        # the source card appears once the site has taken the URL:
                        # ask for it instead of sleeping the whole 3 s
                        await _settle(page, 3000, 200, _mark_shown)
                    except Exception:
                        pass
                if cand:
                    async def _chosen_or_marked():
                        r = await _read_rows()
                        return bool(r.get("chosen")) or bool(await _registered_mark(work))
                    await page.mouse.click(cand["x"], cand["y"])
                    clicked_name = cand.get("name", "")
                    # same 2000 ms of grace the code slept, but it ends when the
                    # row is actually highlighted or the source card is up
                    await _settle(page, 2000, 200, _chosen_or_marked)
                    # if the row highlight did not take, click it through the DOM.
                    # The registered source is shown in `.origin-choose-mark`
                    # (live-form markup: origin-choose > origin-choose-mark >
                    # origin-choose-list > origin-item).
                    after = await _read_rows()
                    marked = await _registered_mark(work)
                    if not after.get("chosen") and not marked:
                        try:
                            await work.locator(".origin-item").nth(cand["i"]).click(
                                timeout=4000)
                            await page.wait_for_timeout(1500)
                        except Exception:
                            pass

                # ---- did a source actually register, and is it OUR source?
                # The question is not "did something register" but "is the
                # model the form now credits the model we copied": a listing
                # that credits a different creator is what gets reported as
                # infringement. `declares_our_source` answers it from the
                # source's own identity (slug link / URL / title / the certain
                # row we clicked) and nothing weaker.
                declared = False
                registered = ""
                mark_text = ""
                why = ""
                for _ in range(12):
                    await _read_rows()
                    # the REGISTERED source card (never a row of the open
                    # suggestion list): its link, if any, tells us WHICH model
                    # the form now credits; `.origin-choose-mark` is where the
                    # live form shows it.
                    mark = await _registered_mark(work)
                    registered = mark.get("href") or ""
                    mark_text = mark.get("text") or ""
                    v = declares_our_source(cand, mark_text, registered,
                                            source_url, slug, name)
                    why = v["why"]
                    if v["declared"]:
                        declared = True
                        break
                    await page.wait_for_timeout(500)
                UPLOAD_VERIFY["remix_source_why"] = why
                UPLOAD_VERIFY["remix_source_declared"] = declared
                UPLOAD_VERIFY["remix_source_url"] = source_url
                UPLOAD_VERIFY["remix_suggestion_clicked"] = clicked_name
                UPLOAD_VERIFY["remix_registered_href"] = registered
                UPLOAD_VERIFY["remix_suggestions_seen"] = result.get("rows", [])[:8]
                UPLOAD_VERIFY["remix_suggestions_refused"] = [r.get("name")
                                                              for r in result.get("flagged", [])][:8]
                UPLOAD_VERIFY["remix_declared_by"] = (
                    "row click" if declared and cand else
                    "url paste" if declared else "")
                refuse = remix_refusal(cand, declared, why, mark_text, result,
                                       name, slug)
                if refuse:
                    raise RuntimeError(refuse)
            except Exception as e:
                UPLOAD_VERIFY["remix_source_declared"] = False
                UPLOAD_VERIFY["remix_source_error"] = str(e)[:500]
                # A re-upload MUST declare its source: never fill in the licence
                # answers and submit a form that credits nobody.
                raise RuntimeError("Refusing to publish without declaring the "
                                   "source model (Remix Models): " + str(e)[:500])

        # 8d) New-listing LICENSE — must match the SOURCE license (the form's
        # three questions drive the CC badge: adaptations, commercial, and
        # the public-domain dedication). Refused ND sources never get here.
        try:
            # the single source of truth for "which licence -> which answers"
            code, (q1_idx, q2_idx, q3_idx) = license_answer_indexes(license_raw)
            questions = [
                ("Allow adaptations of your work to be shared?", q1_idx),
                ("Allow commercial uses of your work?", q2_idx),
                ("Allow sharing or redistributing of your work or its derivatives?", q3_idx),
            ]
            clicked = []
            for q_text, want_idx in questions:
                done = False
                for _attempt in range(3):
                    done = await work.evaluate("""
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
                    """, [q_text, want_idx])
                    if done:
                        break
                    # maybe collapsed — scroll the question into view and retry
                    try:
                        h = work.get_by_text(q_text, exact=True).first
                        await h.scroll_into_view_if_needed(timeout=3000)
                        await page.wait_for_timeout(600)
                    except Exception:
                        break
            # VERIFY from the real radio states inside each question block
            verified = await work.evaluate("""
                (questions) => questions.map(([qText]) => {
                    const heads = [...document.querySelectorAll('div,span,h3,label,p')]
                        .filter(e => (e.innerText||'').trim() === qText);
                    for (const h of heads) {
                        let box = h.parentElement;
                        for (let i = 0; i < 6 && box; i++) {
                            const radios = [...box.querySelectorAll('input[type=radio]')];
                            if (radios.length >= 2)
                                return radios.findIndex(r => r.checked);  // 0=Yes,1=No,2=SA
                            box = box.parentElement;
                        }
                    }
                    return -1;
                })
            """, questions)
            # ...AND read the CC badge the form shows under those answers: that
            # badge is the thing a human would look at to confirm the listing
            # inherits the source licence's terms.
            badge_payload = await work.evaluate(LICENSE_BADGE_JS)
            want_badge = expected_badge_token(code)
            try:
                cap = json.loads(badge_payload) if badge_payload else {}
            except Exception:
                cap = {"badge": badge_payload or ""}
            got_badge, badge_from = "", ""
            for src in ("badge", "icons", "card"):
                val = cap.get(src)
                got = badge_from_icons(val) if src == "icons" else badge_license_name(val or "")
                if got:
                    got_badge, badge_from = got, src
                    break
            UPLOAD_VERIFY["license_answers"] = {
                "wanted": [q1_idx, q2_idx, q3_idx],
                "source_code": code or "unknown",
                "clicked": clicked, "radio_index_verified": verified,
                "badge": (cap.get("badge") or "").replace("\n", " ")[:300],
                "badge_parsed_from": badge_from,
                "badge_in_card": bool(cap.get("in_card")),
                "badge_license": got_badge,
                "badge_expected": want_badge,
                "badge_ok": bool(got_badge) and _norm_cc(got_badge) == _norm_cc(want_badge),
                "license_card": (cap.get("card") or "")[:400],
            }
            # nothing sleeps here: the radio states were just read back from the
            # DOM and 8e refuses to submit unless they are right, so the 600 ms
            # this used to spend was paid on every model for no decision
        except Exception as e:
            UPLOAD_VERIFY["license_answers"] = {"error": str(e)[:200]}

        # 8e) Refuse to publish when the form does not carry the SOURCE licence.
        #     The three radio answers ARE the form state and the badge is derived
        #     from them (proved on the live form), so radio answers that read
        #     back exactly as needed are authoritative; the badge is a second
        #     witness that may only disagree when the radios could not be read.
        la = UPLOAD_VERIFY.get("license_answers") or {}
        verified = la.get("radio_index_verified")
        radios_read = (isinstance(verified, list) and len(verified) == 3
                       and -1 not in verified)
        radios_ok = bool(radios_read and verified == la.get("wanted"))
        la["radios_read"] = radios_read
        la["radios_ok"] = radios_ok
        UPLOAD_VERIFY["license_answers"] = la
        if radios_read and not radios_ok:
            return {"success": False, "url": page.url, "verify": dict(UPLOAD_VERIFY),
                    "error": "License answers on the form are %s but the source "
                             "license needs %s — not submitting."
                             % (verified, la.get("wanted"))}
        if la.get("badge_license") and la.get("badge_ok") is False and not radios_ok:
            try:
                await page.screenshot(path=os.path.join(DOWNLOADS, "_shots",
                                                      "license_mismatch.png"),
                                      full_page=True)
            except Exception:
                pass
            return {"success": False, "url": page.url, "verify": dict(UPLOAD_VERIFY),
                    "error": "License badge on the form reads '%s' but the source "
                             "license needs '%s' — not submitting."
                             % (la.get("badge_license"), la.get("badge_expected"))}

        if _stop_pressed(should_stop):
            return _stopped_result()
        _mark("source")
        # 9) Tick "I have read and agree to Copyright Statement." — Submit is
        #    blocked until this is ticked. The live row is Element UI's real
        #    checkbox (label.el-checkbox > span.el-checkbox__input + hidden
        #    input.el-checkbox__original), and WHILE UNTICKED the label carries
        #    the "Please check the box to agree before submitting" warning as a
        #    child, so the row's text is 93 chars — any narrow text filter
        #    (the old `< 90`) excluded the only row it existed to click.
        #    CONSENT_JS now finds the row structurally, reports the circle's
        #    own click point, and the tick is read back from the row's input.
        #    The click itself is a REAL mouse click; coordinates from the form
        #    frame need the iframe offset, exactly like the Submit click.
        #    The Model Source picker can still leave its card over the footer at
        #    this moment (measured live: `div.origin-choose-mark` was the hit at
        #    the consent circle, so the first tick click was absorbed and only
        #    the retry landed). _clear_picker_overlay() dismisses that leftover
        #    and verifies the point is reachable BEFORE the first tick click, so
        #    the first click is the one that ticks; step 10 uses the same guard
        #    on the Submit click.
        async def _click_point(x, y) -> bool:
            try:
                if is_frame:
                    fbox = await (await work.frame_element()).bounding_box()
                    await page.mouse.click(fbox["x"] + x, fbox["y"] + y)
                else:
                    await page.mouse.click(x, y)
                await page.wait_for_timeout(320)
                return True
            except Exception:
                return False

        async def _topmost_at(x, y) -> dict:
            """What a real mouse click at (x, y) would actually hit.

            The Model Source picker leaves its card over the fixed footer after
            the source is registered (measured live: `div.origin-choose-mark`
            was the hit at the consent circle), and a click aimed at the footer
            lands on that leftover instead. The `in_consent` / `is_submit` /
            `in_footer` flags tell _clear_picker_overlay what the point is
            really over.
            """
            try:
                ox, oy = x, y
                if is_frame:
                    fbox = await (await work.frame_element()).bounding_box()
                    ox, oy = fbox["x"] + x, fbox["y"] + y
                return await page.evaluate(
                    "([x, y]) => { const e = document.elementFromPoint(x, y);"
                    " return e ? {tag: e.tagName.toLowerCase(),"
                    " cls: String(e.className || '').slice(0, 90),"
                    " in_footer: !!e.closest('footer'),"
                    " in_consent: !!e.closest('label.el-checkbox'),"
                    " is_picker: !!e.closest('.origin-choose-mark'),"
                    " is_submit: !!e.closest('.submit')} : null; }",
                    [ox, oy]) or {}
            except Exception:
                return {}

        async def _clear_picker_overlay(x, y, kind, record, dismiss_at=None) -> bool:
            """Make a real click at (x, y) reach the footer control it is for.

            The Model Source picker leaves its registered-source card (and any
            open suggestion list) sitting over the fixed footer — measured on
            the live form: elementFromPoint at the consent circle returned
            `div.origin-choose-mark`, the first tick click was absorbed
            (`tried: [["round1:circle", false], ...]`) and the same leftover was
            over the footer when a real run pressed Submit and Creality never
            confirmed it. Escape closes the open suggestion list; when the card
            still covers the point, one real click dismisses it (its own
            click-outside — the click the live form itself took), and the point
            is verified again. Returns True when the click would reach `kind`
            ('consent' or 'submit').

            `dismiss_at` is where that dismissing click is made when it is not
            the point being checked: the Submit check passes the consent point,
            so the dismissal always lands on the picker's card and never on a
            suggestion row.

            The dismissing click is made ONLY while the thing under the point is
            the picker's own card (`is_picker`). Anywhere else — over a
            suggestion row, a link, or the consent label itself — a click would
            be a real press of that control, so the walk stops and reports the
            point as unreachable instead of guessing.
            """
            def reaches(hit) -> bool:
                if not hit:
                    return False
                return bool(hit.get("in_consent") if kind == "consent"
                            else hit.get("is_submit"))

            dx, dy = dismiss_at if dismiss_at else (x, y)
            hit = await _topmost_at(x, y)
            record.append([kind, "topmost", hit.get("tag"), hit.get("cls")])
            if reaches(hit):
                return True
            for _ in range(2):
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                await page.wait_for_timeout(250)
                hit = await _topmost_at(x, y)
                record.append([kind, "after-escape", hit.get("tag"), hit.get("cls")])
                if reaches(hit):
                    return True
                # Only the picker's own card is safe to click: anything else
                # under the point is a control a click would really press.
                if not hit.get("is_picker"):
                    break
                if (dx, dy) != (x, y) and not (await _topmost_at(dx, dy)).get("is_picker"):
                    record.append([kind, "dismiss-skipped", "not-picker", ""])
                    break
                # the leftover is what a click meets — one real click dismisses
                # it; the point is then re-verified, never assumed
                if not await _click_point(dx, dy):
                    break
                hit = await _topmost_at(x, y)
                record.append([kind, "after-click", hit.get("tag"), hit.get("cls")])
                if reaches(hit):
                    return True
            return reaches(await _topmost_at(x, y))

        async def _consent_info() -> dict:
            try:
                return json.loads(await work.evaluate(CONSENT_JS) or "{}")
            except Exception:
                return {}

        async def _tick_consent() -> bool:
            """Tick the agreement row and report whether it read back ticked.

            Waits (bounded) for the row to be present, clicks the visible
            circle with a real mouse click, and reads the tick back from the
            row's own checkbox input. A JS click is the fallback when no mouse
            click can land (and the old label locator the last resort).
            """
            ok = False
            try:
                info: dict = {}
                for _ in range(25):              # the footer row can arrive late
                    info = await _consent_info()
                    if info.get("found"):
                        break
                    await page.wait_for_timeout(400)
                ok = bool(info.get("checked"))
                UPLOAD_VERIFY["consent"] = {
                    "found": bool(info.get("found")),
                    "row": info.get("row", ""),
                    "row_cls": info.get("row_cls", ""),
                    "input_html": info.get("input_html", ""),
                    "points": {k: (v or {}).get("cls", "") for k, v in
                               (info.get("pts") or {}).items()},
                }
                # 9a) The Model Source picker can still sit over the footer once
                #     the source is registered: a click aimed at the circle then
                #     hits its leftover. Clear it and verify BOTH footer regions
                #     — the consent row AND the Submit button — BEFORE the first
                #     tick click, so ROUND 1 is the click that ticks.
                reach = UPLOAD_VERIFY["consent"].setdefault("reach", [])
                pts0 = info.get("pts") or {}
                p0 = pts0.get("circle") or pts0.get("label")
                try:
                    spoint = await work.evaluate(SUBMIT_JS)
                except Exception:
                    spoint = None
                if p0 and p0.get("x") is not None:
                    UPLOAD_VERIFY["consent"]["reachable"] = \
                        await _clear_picker_overlay(p0["x"], p0["y"],
                                                    "consent", reach)
                if spoint and spoint.get("x") is not None:
                    UPLOAD_VERIFY["submit_reachable"] = \
                        await _clear_picker_overlay(
                            spoint["x"], spoint["y"], "submit", reach,
                            dismiss_at=((p0["x"], p0["y"])
                                        if p0 and p0.get("x") is not None
                                        else None))

                for _round in range(3):
                    if ok:
                        break
                    info = await _consent_info()
                    if not info.get("found"):
                        break
                    if info.get("checked"):      # a dismissal click may already
                        ok = True                # have ticked it — accept that
                        break
                    targets = [(t, p) for t, p in (info.get("pts") or {}).items()
                               if p and p.get("x") is not None]
                    if not targets:
                        break
                    tag, pt = targets[0]          # circle first (dict order)
                    if not await _click_point(pt["x"], pt["y"]):
                        continue
                    for _ in range(6):            # let the form settle, then judge
                        try:
                            ok = bool(await work.evaluate(CONSENT_STATE_JS))
                        except Exception:
                            ok = False
                        if ok:
                            break
                        await page.wait_for_timeout(150)
                    UPLOAD_VERIFY.setdefault("consent", {}).setdefault("tried", []).append(
                        ["round%d:%s" % (_round + 1, tag), ok])
                    if ok:
                        break
                # JS fallback when no real mouse click can land on the circle
                if not ok:
                    try:
                        ok = bool(await work.evaluate(CONSENT_CLICK_JS))
                        UPLOAD_VERIFY.setdefault("consent", {})["js_click"] = ok
                    except Exception:
                        ok = False
                # last resort: click the row element itself (handles the iframe)
                if not ok:
                    for sel in ("label:has-text('I have read and agree')",
                                "div:has-text('I have read and agree')"):
                        if ok:
                            break
                        try:
                            await work.locator(sel).last.click(force=True, timeout=3000)
                            await page.wait_for_timeout(350)
                            ok = bool(await work.evaluate(CONSENT_STATE_JS))
                        except Exception:
                            continue
                UPLOAD_VERIFY.setdefault("consent", {})["checked"] = ok
                UPLOAD_VERIFY["copyright_agreed"] = ok
            except Exception as e:
                UPLOAD_VERIFY["copyright_agreed"] = False
                UPLOAD_VERIFY.setdefault("image_errors", []).append(
                    "agreement click failed: " + str(e)[:140])
            return ok

        agreed = False
        try:
            info = await _consent_info()
            agreed = bool(info.get("checked"))
            UPLOAD_VERIFY["consent"] = {"found": bool(info.get("found")),
                                        "row": info.get("row", ""),
                                        "row_cls": info.get("row_cls", ""),
                                        "input_html": info.get("input_html", "")}
            if not agreed:
                agreed = await _tick_consent()
            UPLOAD_VERIFY["copyright_agreed"] = agreed
        except Exception as e:
            agreed = await _tick_consent()
            UPLOAD_VERIFY["copyright_agreed"] = agreed
            UPLOAD_VERIFY.setdefault("image_errors", []).append(
                "agreement check failed: " + str(e)[:140])

        await page.wait_for_timeout(500)
        os.makedirs(os.path.join(DOWNLOADS, "_shots"), exist_ok=True)
        try:
            await page.screenshot(path=os.path.join(DOWNLOADS, "_shots", "before_submit.png"),
                                  full_page=True)
        except Exception:
            pass

        _mark("consent")
        # 10) Submit. The Submit control is a styled <div class="submit">, not a
        #     real <button>, so locate it in the iframe and click via translated
        #     page coordinates (iframe offset + element center). Before the
        #     click, the point is verified to be reachable: the Model Source
        #     picker's leftover can still sit over the footer, and a real run
        #     that pressed Submit into it left Creality without a click
        #     (_clear_picker_overlay dismisses it, like step 9 does).
        #     Set CREALITY_NO_SUBMIT=1 to fill everything but stop before publishing
        #     (used for verifying the form without creating a listing).
        submitted = False
        if os.environ.get("CREALITY_NO_SUBMIT") == "1":
            # `no_submit` is the marker that says "the form was filled, nothing
            # was published" — the caller must NOT record this as a publish
            # (it did that once and a model the user never uploaded became
            # locked in the already-published config).
            return {"success": True, "no_submit": True, "url": page.url,
                    "verify": dict(UPLOAD_VERIFY),
                    "message": "Filled everything but did NOT submit (CREALITY_NO_SUBMIT=1)."}
        # ── the last chance to catch a wrong form, from ONE read ────────────
        # Read the whole form back before pressing Submit and record what it says
        # (name applied, licence rows ticked, consent, covers on the form).
        # This is a RECORD, not a new hard gate: a check that fails here is
        # written into the upload's evidence, and the submit proceeds exactly as
        # before, so adding the check cannot break a publish that works today.
        try:
            _pm_pre = await step_verify.observe(work)
        except Exception:
            _pm_pre = None
        try:
            _checks = {
                "step2_open": step_verify.step_two_open(_pm_pre),
                "name_applied": step_verify.name_applied(_pm_pre, name),
                "covers_present": step_verify.covers_present(_pm_pre,
                                                             len(image_paths or [])),
                "consent": step_verify.consent_ticked(_pm_pre),
            }
            UPLOAD_VERIFY["step_checks"] = step_verify.record(_checks)
            UPLOAD_VERIFY["step_checks_summary"] = step_verify.summarise(_checks)
            UPLOAD_VERIFY["page_model"] = page_model.summary(_pm_pre)
        except Exception as e:
            UPLOAD_VERIFY["step_checks"] = {"error": str(e)[:160]}

        try:
            try:
                target = await work.evaluate(SUBMIT_JS)   # module-level constant
            except Exception:
                target = None
            outcome = None      # "success" | "duplicate" | "error" | "needs_consent"
            reason = ""
            clicked_submit = False

            async def _click_submit() -> bool:
                nonlocal target, clicked_submit
                try:                       # re-locate: the form may have re-rendered
                    fresh = await work.evaluate(SUBMIT_JS)
                    if fresh:
                        target = fresh
                except Exception:
                    pass
                if not target:
                    return False
                # The picker's leftover can be over the Submit button too: a real
                # run pressed Submit into it and Creality never saw the click.
                # Verify the point is reachable and dismiss the leftover first
                # — never click Submit through an overlay.
                try:
                    reach = UPLOAD_VERIFY.setdefault("submit_reach", [])
                    await _clear_picker_overlay(target["x"], target["y"],
                                                "submit", reach)
                    fresh = await work.evaluate(SUBMIT_JS)   # the form may move
                    if fresh:
                        target = fresh
                except Exception:
                    pass
                try:
                    if is_frame:
                        fe = await work.frame_element()
                        fbox = await fe.bounding_box()
                        await page.mouse.click(fbox["x"] + target["x"],
                                               fbox["y"] + target["y"])
                    else:
                        await page.mouse.click(target["x"], target["y"])
                    clicked_submit = True
                    return True
                except Exception:
                    return False

            # ── What Creality itself does with the press ──────────────────────
            # A silent bounce leaves its only trace in the create request's own
            # response, so POST/PUT responses are captured (status + body) while
            # Submit is pressed; after a failure the form is read back too (which
            # wizard step it went to, which field Creality put in its error state).
            # Both go into UPLOAD_VERIFY and the returned error — the authorised
            # publish failed with "never confirmed" and no way to see why.
            net_hits: list = []
            _NET_RE = re.compile(r"create|publish|upload|save|submit|draft|model", re.I)

            async def _grab_response(resp):
                try:
                    try:
                        body = ((await resp.text()) or "")[:400]
                    except Exception as e:
                        body = "<body unavailable: %s>" % str(e)[:60]
                    net_hits.append({"method": resp.request.method,
                                     "url": (resp.url or "")[:160],
                                     "status": resp.status, "body": body})
                except Exception:
                    pass

            def _on_response(resp):
                try:
                    if resp.request.method in ("POST", "PUT", "PATCH") \
                            and _NET_RE.search(resp.url or ""):
                        asyncio.create_task(_grab_response(resp))
                except Exception:
                    pass
            try:
                page.on("response", _on_response)
            except Exception:
                pass

            AGREE_BLOCK = SUBMIT_AGREE_BLOCK_RE
            REJECT = SUBMIT_REJECT_RE
            if target:
                for attempt in (1, 2):
                    if should_stop and should_stop():
                        # Stop was pressed before Submit: nothing is pressed
                        return {"success": False, "stopped": True,
                                "error": "run stopped before Submit — nothing was submitted",
                                "verify": dict(UPLOAD_VERIFY)}
                    if not await _click_submit():
                        break
                    for _ in range(100):
                        # same ~40 s of grace the 40 x 1000 loop gave, but the
                        # answer is noticed in 400 ms instead of up to a second
                        await page.wait_for_timeout(400)
                        # Most reliable success signal: navigation to the model page.
                        if "model-detail" in page.url:
                            outcome = "success"; break
                        body = (await page.evaluate("() => document.body.innerText")) or ""
                        low = body.lower()
                        # Creality's duplicate / originality block (do not bypass)
                        mdup = re.search(r"already.{0,12}(uploaded|exist|published)|"
                                         r"has been uploaded|duplicate|cannot upload|"
                                         r"repeat|已上传|重复|已存在", low)
                        if mdup:
                            outcome = "duplicate"; reason = mdup.group(0); break
                        if AGREE_BLOCK.search(body):
                            outcome = "needs_consent"
                            reason = AGREE_BLOCK.search(body).group(0)
                            break
                        merr = REJECT.search(body)
                        if merr:
                            outcome = "error"; reason = merr.group(0); break
                        if "/create-model" not in page.url:
                            outcome = "success"; break
                        if re.search(r"submitted successfully|under review|"
                                     r"published successfully", low):
                            outcome = "success"; break
                    if outcome != "needs_consent":
                        break
                    # the agreement box was not really ticked: tick it again and
                    # press Submit once more before reporting anything
                    UPLOAD_VERIFY.setdefault("consent", {})["retry"] = True
                    agreed = await _tick_consent()
                    if not agreed:
                        break
                    await page.wait_for_timeout(800)
                    outcome = None
                submitted = (outcome == "success")
        except Exception as e:
            reason = str(e)
        _mark("submit")

        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass
        for _ in range(20):                 # let the body reads finish
            if net_hits:
                break
            try:
                await page.wait_for_timeout(150)
            except Exception:
                break
        rejection: dict = {}
        if not submitted:
            try:
                rejection = await work.evaluate(REJECTION_JS) or {}
            except Exception as e:
                rejection = {"error": str(e)[:140]}
            rejection["network"] = net_hits[-4:]
            UPLOAD_VERIFY["submit_rejection"] = rejection

        try:
            await page.screenshot(path=os.path.join(DOWNLOADS, "_shots", "after_submit.png"))
        except Exception:
            pass

        if submitted:
            published_url = page.url
            img_note_done = img_note
            # The publish worked: this form tab has no further use, and leaving
            # it open is what piled 20 tabs onto the run. Any path that still
            # needs the human (a failure, a form left for a manual Submit) keeps
            # its tab, because its own message points at it.
            try:
                await page.close()
            except Exception:
                pass
            return {"success": True, "url": published_url,
                    "verify": dict(UPLOAD_VERIFY),
                    "message": "Published to your Creality Cloud account with "
                               + img_note_done + ". Check 'My Designs'."}
        if outcome == "needs_consent" or not UPLOAD_VERIFY.get("copyright_agreed", False):
            return {"success": False, "url": page.url, "needs_consent": True,
                    "error": "Creality is still asking for the 'I have read and agree to "
                             "Copyright Statement.' tick, so nothing was submitted. "
                             "Tick that circle in the browser and press Submit, or tell "
                             "me and I'll adjust the click."}
        if outcome == "duplicate":
            return {"success": False, "url": page.url, "duplicate": True,
                    "error": "Creality blocked this — the model already exists on the platform "
                             "(their duplicate/originality check). It can't be re-published."}
        if outcome == "error":
            extra = describe_submit_failure(rejection.get("step"),
                                            rejection.get("flagged"),
                                            rejection.get("network"), fallback="")
            return {"success": False, "url": page.url,
                    "verify": dict(UPLOAD_VERIFY),
                    "error": (f"Creality rejected the submission "
                              f"({reason or 'see the form for the missing field'}). "
                              + extra).strip()}
        if not clicked_submit:
            return {"success": True, "url": page.url,
                    "message": "Form fully filled (model, name, " + img_note
                               + ", AI category/tags, copyright). The Submit control "
                                 "was not found, so click Submit in the browser — see "
                                 "before_submit.png."}
        # Submit WAS pressed and Creality never confirmed it: report what Creality
        # itself did (the step it bounced to, the field it flagged, the create
        # request's response) instead of only saying "never confirmed".
        why = describe_submit_failure(rejection.get("step"), rejection.get("flagged"),
                                      rejection.get("network"),
                                      fallback="Submit was pressed but Creality never "
                                               "confirmed the submission and showed no "
                                               "reason on the form")
        return {"success": False, "url": page.url,
                # the form's own report, not only the sentence built from it:
                # whether the remix source registered, and what the field reads
                # now, is what separates "Creality refused" from "we never told
                # it whose model this is". Without this the run log says the
                # field was in an error state and nothing about why.
                "verify": dict(UPLOAD_VERIFY),
                "error": why + ". The form is still open (see "
                         "downloads/_shots/after_submit.png)."}
