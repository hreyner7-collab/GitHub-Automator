"""Analytics for the user's Creality Cloud listings: downloads + likes over time.

How it works (no API keys, no cloud services — the logged-in browser IS the API):
  * `refresh()` connects to the user's real Chrome over CDP (the same mechanism
    the uploader uses, so the existing login session is reused), opens the
    Creality Cloud "my models" page and scrapes per-model download & like
    counts.
  * Every scrape is appended to a small JSON snapshot store on disk
    (downloads/_analytics/snapshots.json). Deltas over any period are computed
    by comparing the latest snapshot against the last snapshot before the
    period start (step-back interpolation — correct even if scrapes are
    irregular).

The store is append-only and human-readable; delete the file to reset history.
"""
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, asdict

BASE = os.path.dirname(__file__)
SNAP_DIR = os.path.join(BASE, "downloads", "_analytics")
SNAP_FILE = os.path.join(SNAP_DIR, "snapshots.json")

CLOUD_HOME = "https://www.crealitycloud.com/"
ACCOUNT_BASE = "https://id.creality.com"
POINTS_URL = "https://www.crealitycloud.com/incentive-points"   # Total Points card
SIGNIN_URL = ACCOUNT_BASE   # opening the Accounts Center while logged in
                            # redirects to /account-settings/<own id> — that
                            # redirect IS the login check; while signed out it
                            # shows the login page, whose URL contains 'login'


def _models_url(account_id: str) -> str:
    """The user's own public models tab (verified card DOM: Views/Like chips)."""
    return f"https://www.crealitycloud.com/user/{account_id}/model"


@dataclass
class ModelSnapshot:
    name: str
    url: str
    downloads: int
    likes: int
    ts: float          # unix seconds of the scrape
    views: int = 0


# ── Points store (account-level, one balance per scrape) ─────────────────────

POINTS_FILE = os.path.join(SNAP_DIR, "points.json")


def load_points() -> list[dict]:
    """[{ts: float, points: int}] oldest-first (empty on any problem)."""
    try:
        with open(POINTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def append_points(ts: float, points: int) -> bool:
    """Append a balance reading; skips duplicates of the last value within the
    same hour (the balance rarely changes — don't grow the file needlessly).
    Returns True when a reading was stored."""
    os.makedirs(SNAP_DIR, exist_ok=True)
    hist = load_points()
    if hist:
        last = hist[-1]
        if last["points"] == points and ts - last["ts"] < 3600:
            return False
    hist.append({"ts": ts, "points": int(points)})
    with open(POINTS_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=1)
    return True


def points_stats(now: float | None = None) -> dict:
    """Points earned today / 7d / 30d / year-to-date plus the current total.
    Same step-back semantics as the per-model stats: the latest reading at or
    before each window start is the baseline (or the first known reading when
    tracking starts inside the window)."""
    hist = sorted(load_points(), key=lambda p: p["ts"])
    if not hist:
        return {"current": 0, "totals": {}, "last_point": None}
    now = now or time.time()
    day0 = now - (now % 86400)
    starts = {"today": day0, "7d": now - 7 * 86400,
              "30d": now - 30 * 86400,
              "ytd": day0 - _days_since_jan1(now) * 86400}
    current = hist[-1]["points"]

    def earned_since(t0: float) -> int:
        """Sum of the rises inside the window, starting from the baseline reading.
        A drop is a counter reset on the site: it restarts the count from the new
        value, so gains on either side of a reset are both kept."""
        prior = [p for p in hist if p["ts"] < t0]
        prev = prior[-1]["points"] if prior else hist[0]["points"]
        earned = 0
        for p in hist:
            if p["ts"] < t0:
                continue
            if p["points"] >= prev:
                earned += p["points"] - prev
            prev = p["points"]
        return earned

    totals = {k: earned_since(t0) for k, t0 in starts.items()}
    return {"current": current, "totals": totals, "last_point": hist[-1]["ts"]}


# ── Store ─────────────────────────────────────────────────────────────────────

def load_snapshots() -> list[ModelSnapshot]:
    try:
        with open(SNAP_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [ModelSnapshot(**r) for r in raw]
    except Exception:
        return []


def append_snapshots(items: list[ModelSnapshot]) -> int:
    """Append new (name, ts) deduped snapshots; returns how many were added."""
    if not items:
        return 0
    os.makedirs(SNAP_DIR, exist_ok=True)
    existing = load_snapshots()
    seen = {(s.name, round(s.ts, 3)) for s in existing}
    added = [s for s in items if (s.name, round(s.ts, 3)) not in seen]
    if not added:
        return 0
    with open(SNAP_FILE, "w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in existing + added], f, indent=1)
    return len(added)


def _series_per_model(snaps: list[ModelSnapshot]) -> dict[str, list[ModelSnapshot]]:
    out: dict[str, list[ModelSnapshot]] = {}
    for s in sorted(snaps, key=lambda s: s.ts):
        out.setdefault(s.name, []).append(s)
    return out


def _value_before(series: list[ModelSnapshot], ts: float) -> tuple[int, int, int] | None:
    """Latest (downloads, likes, views) at or before `ts` (step-back). When tracking
    history starts INSIDE the window (no snapshot precedes it), the first known
    value is the baseline — so a window older than the history still counts
    every download observed within it instead of reading a fake 0."""
    prior = [s for s in series if s.ts <= ts]
    if prior:
        s = prior[-1]
        return s.downloads, s.likes, s.views
    return series[0].downloads, series[0].likes, series[0].views


def period_stats(snaps: list[ModelSnapshot], now: float | None = None) -> dict:
    """Per-model and total deltas for today / 7d / 30d / year-to-date, plus the
    current absolute totals. Deltas never go below zero (a counter reset on the
    site would otherwise read as negative growth)."""
    now = now or time.time()
    day0 = now - (now % 86400)                    # UTC day boundary
    starts = {"today": day0, "7d": now - 7 * 86400,
              "30d": now - 30 * 86400,
              "ytd": now - (now % 86400) - _days_since_jan1(now) * 86400}
    per_model = {}
    current = {"downloads": 0, "likes": 0, "views": 0}
    for name, series in _series_per_model(snaps).items():
        latest = series[-1]
        current["downloads"] += latest.downloads
        current["likes"] += latest.likes
        current["views"] += latest.views
        row = {"downloads": latest.downloads, "likes": latest.likes,
               "views": latest.views, "url": latest.url}
        for key, t0 in starts.items():
            before = _value_before(series, t0)
            d = (latest.downloads - before[0], latest.likes - before[1],
                 latest.views - before[2]) if before else (0, 0, 0)
            row[key] = {"downloads": max(0, d[0]), "likes": max(0, d[1]),
                        "views": max(0, d[2])}
        per_model[name] = row
    clean_totals = {key: {"downloads": 0, "likes": 0, "views": 0} for key in starts}
    for row in per_model.values():
        for key in starts:
            clean_totals[key]["downloads"] += row[key]["downloads"]
            clean_totals[key]["likes"] += row[key]["likes"]
            clean_totals[key]["views"] += row[key]["views"]
    return {"current": current, "per_model": per_model, "totals": clean_totals,
            "last_scrape": max((s.ts for s in snaps), default=None)}


def _days_since_jan1(now: float) -> int:
    import datetime
    dt = datetime.datetime.fromtimestamp(now)
    return (dt - dt.replace(month=1, day=1, hour=0, minute=0, second=0,
                            microsecond=0)).days


def chart_series(snaps: list[ModelSnapshot], days: int = 30) -> dict:
    """Cumulative downloads/likes/points at (up to) daily points for the last
    `days` days — step-interpolated from snapshots — plus the raw scrape
    timestamps. Shapes: {t: [...], downloads: [...], likes: [...], points: [...]}"""
    now = time.time()
    t0 = now - days * 86400
    per_model = _series_per_model(snaps)
    phist = load_points()
    ts_points = sorted({round(now - i * (days / 40.0) * 86400, 3) for i in range(41)}
                       | {s.ts for s in snaps if s.ts >= t0}
                       | {p["ts"] for p in phist if p["ts"] >= t0})
    ts_points = [t for t in ts_points if t >= t0] or [t0, now]
    dl, lk, pt = [], [], []
    for t in ts_points:
        d_sum = l_sum = 0
        for series in per_model.values():
            v = _value_before(series, t)
            if v:
                d_sum += v[0]
                l_sum += v[1]
        prior = [p for p in phist if p["ts"] <= t]
        dl.append(d_sum)
        lk.append(l_sum)
        pt.append(prior[-1]["points"] if prior else
                  (phist[0]["points"] if phist else 0))
    return {"t": ts_points, "downloads": dl, "likes": lk, "points": pt}


# ── Sign-in + login-state (reuses the debug Chrome over CDP) ─────────────────

async def open_signin() -> dict:
    """Open the OFFICIAL Creality sign-in page in the user's debug Chrome (the
    profile that uploads and analytics share — one login, remembered forever)."""
    from uploader import CDP_URL, _debug_port_up, ensure_browser
    # Both of these BLOCK (up to 2s for the port probe and 20s while Chrome is
    # launched). Calling them straight from an async def freezes the whole
    # dashboard for that long -- every request waits, not just this one. This is
    # the same defect class the project hit before (see PLAN.md: "/fetch and
    # /health blocked the event loop").
    if not await asyncio.to_thread(_debug_port_up):
        if not await asyncio.to_thread(ensure_browser):
            return {"ok": False, "error": "Could not start Chrome automatically."}
        await asyncio.sleep(2)
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            return {"ok": False, "error": f"Could not attach to Chrome ({e})"}
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(1500)
        # Click the site's own 'Sign in' control: it runs the OAuth round trip
        # through id.creality.com and AUTO-CONTINUES past the Select-Account
        # consent screen when the Account Center already has a session — that
        # consent click is exactly what was missing before (the cloud site kept
        # saying 'sign in' even though id.creality.com was logged in).
        target = await page.evaluate("""
            () => {
                const el = [...document.querySelectorAll('a,button,div,span')]
                    .find(e => (e.innerText||'').trim().toLowerCase() === 'sign in'
                               && e.getBoundingClientRect().width > 0);
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {x: r.x + r.width/2, y: r.y + r.height/2};
            }
        """)
        result = {"ok": True, "url": page.url}
        if target:
            await page.mouse.click(target["x"], target["y"])
            # wait out the id.creality.com bounce (up to ~30s; the user may be
            # typing their password — do NOT touch the tab during this)
            for _ in range(30):
                await page.wait_for_timeout(1000)
                if "crealitycloud.com" in page.url and "id.creality" not in page.url:
                    break
            result = {"ok": True, "url": page.url,
                      "consent_auto": "id.creality" not in page.url}
        await page.bring_to_front()
        return result


_login_cache: dict = {"ts": 0.0, "result": None}


async def login_status() -> dict:
    """Is the debug Chrome's session logged into Creality Cloud?

    Verified check: opening id.creality.com redirects to
    /account-settings/<account id> when logged in, and its URL contains
    'login' when signed out. A throwaway background tab does the check and is
    closed — the user's own tabs are never navigated or touched. Cached 45s.
    The returned account id feeds the models-page URL for refresh()."""
    now = time.time()
    if now - _login_cache["ts"] < 45 and _login_cache["result"]:
        return _login_cache["result"]
    from uploader import CDP_URL, _debug_port_up
    if not await asyncio.to_thread(_debug_port_up):   # blocking probe: keep it off the loop
        result = {"logged_in": False, "browser_up": False}
        _login_cache.update(ts=now, result=result)
        return result
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            return {"logged_in": False, "browser_up": True, "error": str(e)}
        ctx = browser.contexts[0]
        probe = await ctx.new_page()
        try:
            await probe.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=30000)
            await probe.wait_for_timeout(2000)
            url = probe.url
            if "/account-settings/" in url:
                account_id = url.rstrip("/").rsplit("/", 1)[-1]
                result = {"logged_in": True, "browser_up": True,
                          "account_id": account_id}
            elif "login" in url.lower() or "signin" in url.lower():
                result = {"logged_in": False, "browser_up": True}
            else:
                # unknown landing — require positive evidence either way
                body = (await probe.evaluate("() => document.body.innerText") or "").lower()
                result = {"logged_in": False, "browser_up": True,
                          "ambiguous": True, "page_head": body[:120]}
        except Exception as e:
            result = {"logged_in": False, "browser_up": True, "error": str(e)}
        finally:
            await probe.close()
    _login_cache.update(ts=time.time(), result=result)
    return result


# ── Downloads sidecar (per-model, cached on disk) ─────────────────────────────

DL_CACHE = os.path.join(SNAP_DIR, "downloads.json")


def download_counts() -> dict[str, int]:
    """url -> downloads, from the sidecar cache (never raises)."""
    try:
        with open(DL_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_download_counts(d: dict[str, int]):
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(DL_CACHE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)


async def fetch_download_counts(max_models: int = 0) -> dict:
    """Read title='Downloads' from each model-detail page (cards don't show it).
    Updates the sidecar and returns how many models were read. With the store
    at ~119 models and ~3.5s per page, a full pass costs ~7min — done at most
    once per hour and parallelized 3 tabs at a time."""
    from uploader import CDP_URL, _debug_port_up
    if not await asyncio.to_thread(_debug_port_up):   # blocking probe: keep it off the loop
        return {"ok": False, "read": 0, "error": "Chrome not running"}
    snaps = load_snapshots()
    latest: dict[str, tuple[str, float]] = {}
    for s in snaps:
        key = s.url.split("?")[0]
        if key not in latest or s.ts > latest[key][1]:
            latest[key] = (s.name, s.ts)
    urls = list(latest)          # keys are the normalized model URLs
    if max_models:
        urls = urls[:max_models]
    if not urls:
        return {"ok": False, "read": 0, "error": "no snapshots yet — refresh first"}
    from playwright.async_api import async_playwright
    dls = download_counts()
    read = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        sem = asyncio.Semaphore(3)

        async def read_one(u: str):
            nonlocal read
            async with sem:
                page = await ctx.new_page()
                try:
                    await page.goto(u, wait_until="domcontentloaded", timeout=30000)
                    # wait for the stats strip to render (titled chips, verified)
                    try:
                        await page.wait_for_selector("[title=Views]", timeout=8000)
                    except Exception:
                        pass
                    r = await page.evaluate("""
                        () => {
                            // The page lists the MAIN model's stats first, then
                            // related models — scope to the first group only.
                            const views = document.querySelector('[title=Views]');
                            if (!views) return null;        // stats never rendered
                            const dl = document.querySelector('[title=Downloads]');
                            const num = t => {
                                const m = (t||'').match(/[\\d.,]+/);
                                return m ? parseInt(m[0].replace(/,/g,''), 10) : 0;
                            };
                            // an empty downloads chip means genuinely 0
                            return {dl: num(dl ? dl.textContent : '0')};
                        }
                    """)
                    if r is not None:
                        dls[u.split("?")[0]] = int(r["dl"])
                        read += 1
                except Exception:
                    pass
                finally:
                    await page.close()

        await asyncio.gather(*(read_one(u) for u in urls))
    _save_download_counts(dls)
    return {"ok": read > 0, "read": read, "total": len(dls)}


# ── Scraping (reuses the logged-in Chrome session over CDP) ──────────────────

_NUM_RE = re.compile(r"([\d.,]+)\s*([kKmM]?)")


def _parse_count(txt: str) -> int:
    m = _NUM_RE.search(txt or "")
    if not m:
        return 0
    val = float(m.group(1).replace(",", ""))
    mult = {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)
    return int(val * mult)


async def refresh() -> dict:
    """Scrape the user's Creality models via the logged-in Chrome (CDP) and
    append a snapshot. Returns {ok, added, models, error?}."""
    from uploader import CDP_URL, _debug_port_up, ensure_browser

    if not await asyncio.to_thread(_debug_port_up):
        if not await asyncio.to_thread(ensure_browser):
            return {"ok": False, "error": "Chrome is not running with the debug port. "
                    "Click 'Sign in to Creality Cloud' once — after that, updates "
                    "are read automatically from your logged-in Chrome session."}
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            _note_refresh(False, error=str(e))
            return {"ok": False, "error": f"Could not attach to Chrome ({e})"}
        ctx = browser.contexts[0]
        # NEVER reuse (or navigate away) a page with an upload form on it: the
        # uploader drives create-model step by step, and stealing its tab was
        # what made an in-flight publish die with "Target page … has been
        # closed" / "step 2 never reached". Analytics opens its OWN page.
        page = next((p for p in ctx.pages if "crealitycloud" in p.url
                     and "create-model" not in p.url), None) \
            or await ctx.new_page()

        # Signed-out users land on a login page — say so before scraping.
        if "id.creality.com" in page.url and "login" in page.url.lower():
            _note_refresh(False, error="not logged in (login page open)")
            return {"ok": False, "error": "Not logged into Creality Cloud in the "
                    "Chrome window — use 'Sign in to Creality Cloud', then it "
                    "updates automatically."}

        # Which account? The Accounts Center URL carries it (verified: opening
        # id.creality.com while logged in redirects to /account-settings/<id>).
        account_id = None
        st = await login_status()
        if st.get("logged_in"):
            account_id = st.get("account_id")
        if not account_id:
            _note_refresh(False, error="not logged in")
            return {"ok": False, "error": "Not logged into Creality Cloud in the "
                    "Chrome window — use 'Sign in to Creality Cloud', then it "
                    "updates automatically."}


        # The user's own public models tab lists every design as
        # section.creality-model-comp cards whose stat chips carry title
        # attributes (verified against the live site): title="Views" /
        # title="Like". Downloads are NOT shown on cards — only on
        # model-detail pages (title="Downloads") — so they are fetched
        # per-model in `download_counts()` and merged here.
        models = []
        try:
            await page.goto(_models_url(account_id), wait_until="domcontentloaded",
                            timeout=40000)
            await page.wait_for_timeout(3000)
            # scroll the models tab until the card count stops growing (2 stable rounds)
            stable, last = 0, -1
            for _ in range(60):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(350)
                n = await page.evaluate(
                    "() => document.querySelectorAll('section.creality-model-comp').length")
                if n == last:
                    stable += 1
                    if stable >= 2:
                        break
                    last = n
                else:
                    stable, last = 0, n
            models = await page.evaluate("""
                () => {
                    const num = t => {
                        const m = (t||'').match(/([\\d.,]+)\\s*([kKmM]?)/);
                        if (!m) return 0;
                        const v = parseFloat(m[1].replace(/,/g,''));
                        return Math.round(v * ({'k':1e3,'m':1e6}[m[2].toLowerCase()] || 1));
                    };
                    const out = [];
                    for (const sec of document.querySelectorAll('section.creality-model-comp')) {
                        const a = sec.querySelector('a[href*="model-detail"]');
                        if (!a) continue;
                        const name = (sec.querySelector('.model-info-title')?.textContent
                                      || sec.querySelector('img')?.alt || '').trim();
                        let views = 0, likes = 0;
                        for (const el of sec.querySelectorAll('.number-views-layout')) {
                            const key = (el.getAttribute('title') || '').toLowerCase();
                            const val = num(el.querySelector('span')?.textContent);
                            if (key.startsWith('view')) views = val;
                            else if (key.startsWith('like')) likes = val;
                        }
                        out.push({ name: name.slice(0, 120),
                                   url: 'https://www.crealitycloud.com' + a.getAttribute('href'),
                                   downloads: 0,
                                   likes: likes,
                                   views: views });
                    }
                    return out;
                }
            """)
        except Exception as e:
            _note_refresh(False, error=f"profile scrape failed: {e}")
            return {"ok": False, "error": f"Couldn't read your models page ({e})"}

        # merge the cached per-model DOWNLOAD counts (model-detail pages only)
        dls = download_counts()
        for m in models:
            key = m["url"].split("?")[0]
            m["downloads"] = dls.get(key, 0)

        if not models:
            body = ""
            try:
                body = (await page.evaluate("() => document.body.innerText")).lower()
            except Exception:
                pass
            if "login" in page.url.lower() or "log in" in body or "sign in" in body:
                _note_refresh(False, error="not logged in")
                return {"ok": False, "error": "Not logged into Creality Cloud in the "
                        "Chrome window — use 'Sign in to Creality Cloud', then it "
                        "updates automatically."}
            n_links = await page.evaluate(
                "() => document.querySelectorAll('a[href*=\"model-detail\"]').length")
            _note_refresh(False, error="no models parsed")
            return {"ok": False,
                    "error": "Couldn't read any models from your page "
                             f"({_models_url(account_id)}). "
                             "(layout may have changed, or the account has no "
                             "public models).",
                    "verified_dom": {"model_cards": 0, "model_links": int(n_links),
                                     "page_head": body[:150]}}

        ts = time.time()
        snaps = [ModelSnapshot(name=m["name"], url=m["url"], downloads=m["downloads"],
                               likes=m["likes"], ts=ts, views=m.get("views", 0))
                 for m in models]
        added = append_snapshots(snaps)

        # account-level points balance (verified DOM: .points-web-header lists
        # the Total Points number as its first pure-number leaf)
        points_info: dict = {}
        try:
            await page.goto(POINTS_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector(".points-web-header", timeout=8000)
            except Exception:
                pass
            pts = await page.evaluate(r"""
                () => {
                    const norm = s => (s||'').replace(/\s+/g,' ').trim();
                    const scope = document.querySelector('.points-web-header')
                                  || document.querySelector('.my-points-page');
                    const root = scope || document;
                    const nums = [...root.querySelectorAll('*')]
                        .filter(el => !el.children.length && /^[0-9][0-9,]*$/.test(norm(el.innerText)))
                        .map(el => norm(el.innerText));
                    return nums.length ? parseInt(nums[0].replace(/,/g,''), 10) : null;
                }
            """)
            if pts is not None:
                stored = append_points(time.time(), int(pts))
                points_info = {"total": int(pts), "stored": bool(stored)}
            else:
                points_info = {"error": "points number not found on page"}
        except Exception as e:
            points_info = {"error": str(e)[:150]}

        _note_refresh(True, added=added, models=len(snaps))
        return {"ok": True, "added": added, "models": len(models),
                "downloads_known": len(dls), "points": points_info}


# ── Auto-updates ──────────────────────────────────────────────────────────────

AUTO_INTERVAL = 3600          # re-read stats every hour while Chrome is up
_auto_task: asyncio.Task | None = None
_last_refresh: dict = {"ts": None, "ok": None, "added": 0, "models": 0, "error": None}


def _note_refresh(ok: bool, added: int = 0, models: int = 0, error: str | None = None):
    _last_refresh.update({"ts": time.time(), "ok": ok, "added": added,
                          "models": models, "error": error})


async def _auto_loop():
    """Hourly snapshot refresh. Never launches Chrome by itself: if the user's
    debug Chrome isn't running there is nothing to read from — it just waits
    for the next tick (a random browser window popping up hourly would be
    worse than a missed datapoint)."""
    while True:
        try:
            from uploader import _debug_port_up
            if await asyncio.to_thread(_debug_port_up):
                r = await refresh()
                if not r.get("ok"):
                    _note_refresh(False, error=r.get("error"))
                else:
                    await fetch_download_counts()   # keep the downloads sidecar current
        except Exception as e:
            _note_refresh(False, error=str(e))
        await asyncio.sleep(AUTO_INTERVAL)


def start_auto():
    """Idempotently start the background refresher (called at app startup)."""
    global _auto_task
    if _auto_task is None or _auto_task.done():
        _auto_task = asyncio.get_event_loop().create_task(_auto_loop())


def auto_status() -> dict:
    return {"auto_interval_s": AUTO_INTERVAL, "last_refresh": _last_refresh}
