"""Read-only probe: what does a Creality page actually ask its own backend for?

WHY. The pipeline learns one model by loading the page, waiting for the SPA to
paint it, scrolling to wake the lazy gallery and reading DOM boxes — 25.3 s per
listing, measured in artifacts/auto_run_20261024-131810.json. The page got that
same data as JSON in a few hundred ms. This script records WHICH calls those
are, with the request bodies the site really sent, so the read path can be
rebuilt on the answers instead of on the pixels.

It then re-asks those calls from inside the tab, because that is the only way to
learn the two things that decide the design: whether a POST replay from a
BACKGROUND tab is accepted and how fast (Chrome throttles hidden tabs), and
whether the site's own code names an endpoint that hands back the model file.

SAFETY. It only reads. It opens pages, records, and closes the tabs it opened.
It never clicks a control, never uploads, never publishes, and refuses to start
while an auto run is in flight — that run's tabs and evidence are not ours to
share a browser with. Every header whose name smells like a credential is
written as <redacted>, and the session token is read and sent only inside the
page, so it never crosses back into Python and cannot land in this capture.

  .venv/Scripts/python.exe -X utf8 scripts/probe_cc_api.py
  .venv/Scripts/python.exe -X utf8 scripts/probe_cc_api.py --models 3 --keyword "cable clips"
  .venv/Scripts/python.exe -X utf8 scripts/probe_cc_api.py --urls https://.../model-detail/x
"""
import glob
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

OUT_DIR = os.path.join(BASE, "debug")
API_RE = re.compile(r"/api/", re.I)
SECRET_RE = re.compile(r"cookie|author|token|sign|session|jwt|secret|api[-_]?key", re.I)
ID_RE = re.compile(r'"(?:id|modelId|_id)"\s*:\s*"?([0-9a-f]{24})"?', re.I)
SHARE_RE = re.compile(r'"shareId"\s*:\s*"?(\d{5,12})"?', re.I)
BODY_CAP = 4096          # a JSON answer we cannot read is worse than no answer
REQ_BODY_CAP = 2000
ORIGIN = "www.crealitycloud.com"

# Headers a replay must not send: names carrying a credential (redacted in the
# capture, so unreplayable verbatim anyway) and names the browser owns.
SKIP_HEADER_RE = re.compile(
    r"cookie|host|origin|referer|content-length|connection|accept-encoding|"
    r"user-agent|sec-|priority", re.I)

REPLAY_JS = """
async (a) => {
    // a = {url, method, body, headers, useToken}
    const h = Object.assign({}, a.headers);
    let tokenLen = 0, tokenSrc = '';
    if (a.useToken) {
        // The SPA keeps its token in this tab. We find it HERE and send it HERE:
        // the value never crosses back to Python, so it cannot end up in a
        // capture file or in chat — only its length and where it was found.
        const cands = [];
        try {
            for (const c of document.cookie.split(';')) {
                const p = c.trim().split('=');
                if (/token/i.test(p[0]) && p[1] && p[1].length > 8)
                    cands.push(['cookie:' + p[0], decodeURIComponent(p[1])]);
            }
        } catch (e) {}
        try {
            for (const st of [localStorage, sessionStorage]) {
                for (let i = 0; i < st.length; i++) {
                    const k = st.key(i);
                    if (!k || !/token|userInfo|login/i.test(k)) continue;
                    const v = String(st.getItem(k) || '');
                    if (v.length > 8 && v.length < 4000)
                        cands.push([(st === localStorage ? 'local:' : 'session:') + k, v]);
                }
            }
        } catch (e) {}
        const fromVal = (v) => {
            // storage values are often wrapped JSON — take the longest token-ish run
            let best = v;
            try {
                const walk = (o) => {
                    if (typeof o === 'string') {
                        if (/^[A-Za-z0-9._~+\\/-]{16,}$/.test(o) && o.length > best.length) best = o;
                        return;
                    }
                    if (o && typeof o === 'object') { for (const k in o) walk(o[k]); }
                };
                walk(JSON.parse(v));
            } catch (e) {}
            return best;
        };
        for (const [src, v] of cands) {
            const t = fromVal(v);
            if (t && t.length > 8 && t.length < 2000) {
                tokenLen = t.length; tokenSrc = src;
                h.__cxy_token_ = t;
                break;
            }
        }
    }
    const opt = {method: a.method, headers: h, credentials: 'include'};
    if (a.method !== 'GET' && a.body) opt.body = a.body;
    const t0 = performance.now();
    let r, text = '';
    try {
        r = await fetch(a.url, opt);
        text = (await r.text()) || '';
    } catch (e) {
        return {error: String(e).slice(0, 160), ms: Math.round(performance.now() - t0),
                token_len: tokenLen, token_src: tokenSrc,
                visibility: document.visibilityState};
    }
    return {ok: r.ok, status: r.status, ms: Math.round(performance.now() - t0),
            len: text.length, token_len: tokenLen, token_src: tokenSrc,
            visibility: document.visibilityState, text: text.slice(0, 4096)};
}
"""


def _redact(headers: dict) -> dict:
    """Names always, values only for the harmless ones."""
    out = {}
    for k, v in (headers or {}).items():
        out[k] = "<redacted>" if SECRET_RE.search(k) else str(v)[:200]
    return out


def _capture(store: list):
    """A Playwright response listener that records API calls and their bodies."""
    def _on(resp):
        try:
            url = resp.url or ""
            if not API_RE.search(url) or ORIGIN not in url:
                return
            req = resp.request
            entry = {"url": url, "method": req.method, "status": resp.status,
                     "req_headers": _redact(req.headers),
                     "req_body": (req.post_data or "")[:REQ_BODY_CAP]}
            ct = (resp.headers or {}).get("content-type", "")
            entry["content_type"] = ct[:80]
            if "json" in ct:
                try:
                    entry["body"] = (resp.text() or "")[:BODY_CAP]
                except Exception as e:
                    entry["body_err"] = str(e)[:100]
            store.append(entry)
        except Exception:
            pass
    return _on


def recent_model_urls(n: int) -> list:
    """Model URLs the pipeline itself already fetched, newest run first.

    Using the user's own listings is not a convenience: those are exactly the
    models that produced a real file and a readable licence, so an endpoint that
    works for them is worth building on.
    """
    urls, seen = [], set()
    logs = sorted(glob.glob(os.path.join(BASE, "artifacts", "auto_run_*.json")),
                  key=os.path.getmtime, reverse=True)
    for log in logs:
        try:
            d = json.load(open(log, encoding="utf-8"))
        except Exception:
            continue
        for it in d.get("items") or []:
            u = it.get("url") or ""
            if "model-detail" not in u or u in seen:
                continue
            if (it.get("state") or "") in ("published", "queued", "failed", "rejected"):
                seen.add(u)
                urls.append(u)
            if len(urls) >= n:
                return urls
    return urls


def run_busy() -> str:
    """Is an auto run in flight? If so, the probe must not share the browser."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/auto/status", timeout=5) as r:
            return str(json.load(r).get("state") or "")
    except Exception as e:
        return "unknown (%s)" % type(e).__name__


def _ids_from(calls: list) -> dict:
    """The model's own ids, taken from what the page already said."""
    out = {"id": "", "shareId": ""}
    for c in calls:
        b = c.get("body") or ""
        if not out["id"]:
            m = ID_RE.search(b)
            if m:
                out["id"] = m.group(1)
        if not out["shareId"]:
            m = SHARE_RE.search(b)
            if m:
                out["shareId"] = m.group(1)
        if out["id"] and out["shareId"]:
            break
    return out


def _replay_headers(captured: dict) -> dict:
    """The public custom headers the site sent, with nothing secret in them."""
    out = {}
    for k, v in (captured or {}).items():
        if SECRET_RE.search(k) or SKIP_HEADER_RE.search(k):
            continue
        if str(v).startswith("<redacted>"):
            continue
        out[k] = str(v)
    out.setdefault("content-type", "application/json")
    out.setdefault("accept", "application/json")
    return out


def _marks(text: str) -> dict:
    """Which of the fields the read path needs are present in this answer."""
    m = re.search(r'"fileKey"\s*:\s*"([^"]{5,90})"', text or "")
    return {
        "has_licence_text": bool(re.search(r"license|Licence|CC-BY|CXY-SL", text or "")),
        "has_name": bool(re.search(
            r'"(?:name|title|modelName|groupName)"\s*:\s*"[^"]{4,}', text or "")),
        "has_author": bool(re.search(r'"(?:nickName|nickname|authorName|userInfo)"', text or "")),
        "has_covers": bool(re.search(r'"(?:covers|editCovers|imageUrl)"\s*:\s*\[', text or "")),
        "has_description": bool(re.search(r'"(?:description|remark|content)"\s*:\s*"', text or "")),
        "has_file_row": bool(re.search(r'"(?:fileName|fileKey|fileSize|fileFormat)"', text or "")),
        "has_file_url": bool(re.search(
            r'"[^"]{0,28}"\s*:\s*"(?:https?)?://[^"]{6,220}(?:\.3mf|\.stl|\.obj|\.cxbin|\.zip)'
            r'|"[^"]{0,28}"\s*:\s*"[^"]*(?:/3mf/|/stl/|\.cxbin)', text or "")),
        "file_key": m.group(1) if m else "",
    }


def _ask(page, url: str, method: str, body: str, headers: dict, use_token: bool) -> dict:
    try:
        return page.evaluate(REPLAY_JS, {"url": url, "method": method, "body": body,
                                         "headers": headers, "useToken": use_token}) or {}
    except Exception as e:
        return {"error": str(e)[:160]}


def _replay(page, calls: list, ids: dict, deep: bool = False) -> dict:
    """Re-ask, from inside the tab, the JSON endpoints this page already called.

    Creality answers every one of those with POST plus a JSON body, so what is
    still unknown is whether a POST replay from a background tab is accepted —
    first with cookies plus the public __cxy_ headers, then, if refused, with the
    token the SPA itself holds — how long it takes, and whether the answer
    carries the licence, the gallery and a file we can actually fetch.
    """
    js = [c for c in calls if c["method"] in ("GET", "POST")
          and "json" in c.get("content_type", "") and c.get("status") == 200
          and (c.get("body") or "")]
    scored = []
    for c in js:
        b = c["body"]
        scored.append((sum(1 for v in _marks(b).values() if v is True), len(b), c))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    picked = [c for _, _, c in scored[:2]]
    if not picked:
        return {"tried": 0, "note": "no JSON answers from this page to replay"}

    rows = []
    for c in picked:
        url, method, body = c["url"], c["method"], (c.get("req_body") or "")
        head = _replay_headers(c.get("req_headers"))
        r1 = _ask(page, url, method, body, head, False)
        row = {"endpoint": re.sub(r"https?://[^/]+", "", url).split("?")[0],
               "method": method, "req_body": body[:300],
               "first_ms": r1.get("ms"), "status": r1.get("status"),
               "ok": r1.get("ok"), "len": r1.get("len"),
               "visibility": r1.get("visibility"), "error": r1.get("error")}
        row.update(_marks(r1.get("text") or ""))
        if not r1.get("ok"):
            # cookies + public headers were not enough — try with the token the
            # page holds, and record only its length.
            r2 = _ask(page, url, method, body, head, True)
            row.update({"cookie_only_status": r1.get("status"),
                        "with_token_status": r2.get("status"),
                        "with_token_ok": r2.get("ok"),
                        "with_token_ms": r2.get("ms"),
                        "token_len": r2.get("token_len"),
                        "token_src": r2.get("token_src"),
                        "body_head": (r2.get("text") or r2.get("error") or
                                      r1.get("text") or "")[:260]})
            if r2.get("ok"):
                row.update(_marks(r2.get("text") or ""))
        else:
            row["body_head"] = (r1.get("text") or "")[:260]
        row["needs_token"] = bool(not r1.get("ok") and row.get("with_token_ok"))
        rows.append(row)

    # Background-tab throttling, measured not assumed: five timed repeats from a
    # tab we never bring to front, then the same call once with it surfaced. If
    # Chrome throttles the hidden tab, that is a fact the design has to respect
    # (surface the reader tab for the moment of the call), not something to guess.
    rep = {"tried": False}
    if deep and rows:
        best = scored[0][2]
        use_token = not rows[0].get("ok")
        times, r = [], {}
        for _ in range(5):
            r = _ask(page, best["url"], best["method"], best.get("req_body") or "",
                     _replay_headers(best.get("req_headers")), use_token)
            times.append(r.get("ms"))
            time.sleep(0.3)
        rep = {"tried": True,
               "endpoint": re.sub(r"https?://[^/]+", "", best["url"]).split("?")[0],
               "background_ms": times, "status": r.get("status"),
               "visibility": r.get("visibility")}
        page.bring_to_front()
        time.sleep(0.2)
        r = _ask(page, best["url"], best["method"], best.get("req_body") or "",
                 _replay_headers(best.get("req_headers")), use_token)
        rep["foreground_ms"] = r.get("ms")
        rep["foreground_status"] = r.get("status")
    return {"tried": len(rows), "rows": rows, "throttle": rep, "ids": ids}


def _scan_download_endpoints(page) -> dict:
    """Does the site's own code name an endpoint that returns the model file?

    We are not going to invent a URL: this reads the JavaScript bundles the page
    already loaded and lists every /api/ path in them whose name mentions
    download or file. A name found there is the site's own, and can then be asked
    for a model we are already reading.
    """
    try:
        scripts = page.evaluate(
            """() => performance.getEntriesByType('resource')
                    .map(r => r.name)
                    .filter(u => /\\.js(\\?|$)/.test(u))
                    .slice(0, 14)""") or []
    except Exception as e:
        return {"error": str(e)[:160]}
    found = {}
    for u in scripts:
        r = page.evaluate("""async (u) => {
                try {
                    const res = await fetch(u);
                    const t = await res.text();
                    return {ok: res.ok,
                            hits: (t.match(/\\/api\\/[A-Za-z0-9_\\/\\-.]{3,90}/g) || [])};
                } catch (e) { return {error: String(e).slice(0, 80)}; }
            }""", u) or {}
        for h in (r.get("hits") or []):
            if re.search(r"download|file", h, re.I):
                found[h] = found.get(h, 0) + 1
    top = sorted(found, key=lambda k: -found[k])[:40]
    return {"scripts": len(scripts), "endpoints": top,
            "counts": {k: found[k] for k in top}}


def main() -> int:
    n = 5
    keyword = "cable clips"
    urls_arg = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--models":
            n = int(args[i + 1]); i += 2
        elif args[i] == "--keyword":
            keyword = args[i + 1]; i += 2
        elif args[i] == "--urls":
            i += 1
            while i < len(args) and args[i].startswith("http"):
                urls_arg.append(args[i]); i += 1
        else:
            i += 1

    state = run_busy()
    if state not in ("idle", "done"):
        print("NOT PROBING: the dashboard reports auto state '%s' — a run is in the "
              "browser. Wait for it to finish." % state)
        return 3

    urls = urls_arg or recent_model_urls(n)
    if not urls:
        print("No model URLs to probe: pass --urls <model-detail URL> ...")
        return 2
    urls = urls[:n]
    print("probing %d model pages + 1 search page (read-only):" % len(urls))
    for u in urls:
        print("   ", u)

    from playwright.sync_api import sync_playwright
    from discover import CREALITY_SEARCH          # the same search URL the run uses
    from uploader import ensure_browser, CDP_URL
    if not ensure_browser():
        print("Chrome with the debug port is not available — nothing was opened.")
        return 4

    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "models": [], "search": {},
           "note": "read-only capture; secret headers redacted; token never leaves "
                   "the page"}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]

        for idx, url in enumerate(urls):
            calls: list = []
            page = ctx.new_page()
            page.on("response", _capture(calls))
            opened = ""
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_function(
                        """() => { const h = document.querySelector('h1');
                                   return !!((h && (h.innerText||h.textContent)||'').trim())
                                          && document.images.length >= 3; }""", timeout=12000)
                except Exception:
                    pass
                # the gallery and some panels load on view: scroll so their calls
                # fire, exactly as the current fetch path does, or the capture
                # would miss the very endpoint we are looking for.
                seen = 0
                for _ in range(4):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    try:
                        page.wait_for_function("(n) => document.images.length > n",
                                               timeout=1500, arg=seen)
                    except Exception:
                        pass
                    n_now = page.evaluate("() => document.images.length") or 0
                    if n_now == seen:
                        break
                    seen = n_now
                opened = page.url
                deep = (idx == 0)
                row = {
                    "requested": url, "opened": opened,
                    "h1": page.evaluate("() => { const h = document.querySelector('h1');"
                                        " return h ? (h.innerText||'').trim() : ''; }"),
                    "canonical": page.evaluate(
                        "() => { const l = document.querySelector('link[rel=canonical]');"
                        " return l ? l.href : ''; }"),
                    "ids_in_html": page.evaluate(
                        """() => ({ids: (document.documentElement.innerHTML
                                          .match(/[0-9a-f]{24}/g) || []).slice(0, 6),
                                   share: (document.documentElement.innerHTML
                                           .match(/shareId[^0-9]{0,8}(\\d{5,12})/) || [])[1] || ''})"""),
                    "calls": calls,
                    "replay": _replay(page, calls, _ids_from(calls), deep=deep),
                }
                if deep:
                    row["download_endpoints"] = _scan_download_endpoints(page)
                out["models"].append(row)
                print("  %s -> %d api calls, %d replayed"
                      % (opened[:60], len(calls), row["replay"].get("tried") or 0))
            except Exception as e:
                out["models"].append({"requested": url, "error": str(e)[:200], "calls": calls})
                print("  FAILED %s: %s" % (url, str(e)[:120]))
            finally:
                try:
                    page.close()           # only the tab this probe opened
                except Exception:
                    pass

        # one search page: the POST body it sends is the template that replaces
        # the scroll-harvest in Stage 3.
        scalls: list = []
        spage = ctx.new_page()
        spage.on("response", _capture(scalls))
        try:
            spage.goto(CREALITY_SEARCH.format(q=keyword.replace(" ", "+")),
                       wait_until="domcontentloaded", timeout=60000)
            spage.wait_for_timeout(6000)
            out["search"] = {"keyword": keyword, "opened": spage.url, "calls": scalls,
                             "replay": _replay(spage, scalls, {}, deep=False)}
            print("  search -> %d api calls" % len(scalls))
        except Exception as e:
            out["search"] = {"keyword": keyword, "error": str(e)[:200], "calls": scalls}
        finally:
            try:
                spage.close()
            except Exception:
                pass

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "api_capture_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("saved -> %s" % out_path)
    print("now read it: .venv/Scripts/python.exe -X utf8 scripts/read_api_capture.py %s"
          % os.path.relpath(out_path, BASE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
