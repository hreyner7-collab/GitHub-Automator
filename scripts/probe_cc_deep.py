"""Stage 0, part two: the questions the first probe could not answer.

The first probe (scripts/probe_cc_api.py) settled the big one — the page's own
JSON answers carry name, author, licence, gallery and file rows, and a
same-origin POST is accepted in ~130-400 ms. What is still open, and what
decides the design:

  1. THE DOCUMENT ROUTE. The model page ships a `window.__NUXT__` payload. If
     the fields we publish on are already in the document, one page load with no
     scroll and no SPA wait is the cheapest read of all — so this loads with
     wait_until=domcontentloaded, walks the payload, and TIMES it.
  2. THE API SURFACE, FROM THE SITE'S OWN CODE. The bundles contain the call
     shapes (`P("/api/cxy/v3/model/3mfDownload",{id:t})`). Reading them beats
     guessing request bodies, so this dumps every /api/ path that mentions a
     model, with the argument list the site passes next to it.
  3. THE FILE. Can 3mfDownload hand back something whose bytes we can fetch, and
     does the result really look like a 3MF (zip magic "PK")?
  4. THROTTLING, honestly. The first probe's tab was still `visible`, so the
     number was not a background number. This reports visibilityState and
     hasFocus alongside every timing and says which it actually measured.

SAFETY. Read-only. It opens tabs it closes again, clicks nothing, uploads
nothing, publishes nothing, and refuses to run while an auto run is in flight.
The download endpoints are only touched when --download is passed explicitly,
and only for a model this account has already fetched before, because fetching
that file is exactly what the current click-based step does. Any URL returned is
recorded with its query string reduced to parameter NAMES: a signed CDN URL is a
credential and must not sit in a scratch file. The session token never leaves
the page (see probe_cc_api.REPLAY_JS).

  .venv/Scripts/python.exe -X utf8 scripts/probe_cc_deep.py
  .venv/Scripts/python.exe -X utf8 scripts/probe_cc_deep.py --download
  .venv/Scripts/python.exe -X utf8 scripts/probe_cc_deep.py --download <model-url>
"""
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))

from probe_cc_api import (ORIGIN, _ask, _capture, _replay_headers,  # noqa: E402
                          recent_model_urls, run_busy)

# the fields a publish is decided on, by the names the site's JSON uses
WANT = ("groupName", "name", "license", "licenseName", "nickName", "userId",
        "covers", "pcCovers", "appCovers", "description", "remark", "fileKey",
        "fileName", "fileSize", "fileFormat", "totalFileSize", "modelId",
        "shareId", "urlAlias", "copyright", "isOriginal", "modelSource")
API_SHAPE_JS = r"""
async (a) => {
    // Pull every "/api/..." string the site's own code passes to its request
    // helper, together with the argument list that follows it. That is the
    // request body, written by them, not invented by us.
    const res = await fetch(a.url);
    const t = await res.text();
    const rx = /"\/api\/[A-Za-z0-9_\/\-.]{4,80}"\s*,\s*\(?[^;]{0,150}/g;
    const hits = [];
    let m;
    while ((m = rx.exec(t)) !== null && hits.length < 300) hits.push(m[0]);
    // a file row carries fileKey "model/<md5>.cxbin" — if the site builds a URL
    // from it, the code that does so is the shortest path to the bytes
    const cx = [];
    let i = -1;
    while ((i = t.indexOf('cxbin', i + 1)) !== -1 && cx.length < 8)
        cx.push(t.slice(Math.max(0, i - 90), i + 40).replace(/\s+/g, ' '));
    return {ok: res.ok, len: t.length, hits, cx};
}
"""
WALK_JS = r"""
(a) => {
    // Read the page's own SSR object rather than parsing its text: Nuxt hoists
    // the string values into function arguments, so a regex over the HTML finds
    // keys with no values. The object is already the decoded answer, and we walk
    // it here so only a small summary crosses back to Python.
    const root = window.__NUXT__ || (window.useNuxtData && null) || null;
    if (!root) return {missing: true, href: location.href};
    const seen = {};
    const covers = {};
    const descs = [];
    const DESC_RX = /desc|remark|content|intro|detail/i;
    let nodes = 0;
    const walk = (o, depth) => {
        if (nodes > 60000 || depth > 9 || o === null || o === undefined) return;
        if (Array.isArray(o)) {
            nodes += o.length;
            for (let i = 0; i < Math.min(o.length, 40); i++) walk(o[i], depth + 1);
            return;
        }
        if (typeof o !== 'object') return;
        nodes += 1;
        // the node that describes the model itself is the one carrying groupName;
        // its id is what the file endpoints want, and the payload does not call
        // it "modelId"
        if (o.groupName && !seen.modelNodeId) {
            const cand = o.id || o.modelId || o.modelGroupId || '';
            if (/^[0-9a-f]{24}$/.test(String(cand))) seen.modelNodeId = String(cand);
        }
        for (const k in o) {
            const v = o[k];
            if (a.want.indexOf(k) >= 0 && (typeof v === 'string' || typeof v === 'number'
                                           || v === null)) {
                if (seen[k] === undefined || String(seen[k]).length < 1)
                    seen[k] = v === null ? '' : String(v).slice(0, 90);
            }
            if ((k === 'covers' || k === 'pcCovers' || k === 'appCovers'
                 || k === 'editCovers') && Array.isArray(v)) {
                const urls = v.map(x => (x && (x.url || x.originUrl || x.surl)) || '')
                              .filter(u => typeof u === 'string' && u.length > 12);
                if (urls.length > (covers[k] || []).length) covers[k] = urls.slice(0, 12);
            }
            // "description" is in the wanted list but may be empty on this model;
            // the site could be calling it remark/intro/content/detailDescription,
            // so list every long-ish text field we meet and pick the name later
            if (DESC_RX.test(k) && typeof v === 'string' && v.trim().length >= 40
                && descs.length < 12) {
                descs.push([k, v.trim().slice(0, 90)]);
            }
            walk(v, depth + 1);
        }
    };
    walk(root, 0);
    return {nodes, fields: seen, covers, descs,
            top: Object.keys(root).slice(0, 20)};
}
"""


def _url_shape(u: str) -> str:
    """Host + path + the NAMES of the query params, never their values."""
    if not u:
        return ""
    q = re.search(r"\?([^#]*)", u)
    names = sorted({m.group(1) for m in re.finditer(r"([A-Za-z_]{2,20})=", q.group(1))}) \
        if q else []
    return "%s?[%s]" % (re.sub(r"\?.*", "", u)[:130], ",".join(names))


def _payload(page, label: str) -> dict:
    """Walk the loaded page's own SSR object and return a small summary."""
    try:
        w = page.evaluate(WALK_JS, {"want": list(WANT)}) or {}
    except Exception as e:
        return {"error": "walk failed: %s" % str(e)[:140]}
    w["source"] = label
    return w


def _api_shapes(page) -> dict:
    """Request shapes for model-related endpoints, taken from the site's code."""
    scripts = page.evaluate(
        """() => performance.getEntriesByType('resource').map(r => r.name)
               .filter(u => /\\.js(\\?|$)/.test(u)).slice(0, 24)""") or []
    shapes = {}
    cx = []
    for u in scripts:
        r = page.evaluate(API_SHAPE_JS, {"url": u}) or {}
        for h in (r.get("hits") or []):
            path = re.match(r'"(/api/[^"]+)"', h).group(1)
            if not re.search(r"model|3mf|file", path, re.I):
                continue
            arg = h[h.index('"', h.index(path) + len(path)) + 1:][:150]
            shapes.setdefault(path, arg.strip().lstrip(",(").strip())
        cx += [s for s in (r.get("cx") or []) if s not in cx][:4]
    return {"scripts": len(scripts), "count": len(shapes),
            "shapes": dict(list(shapes.items())[:80]), "cxbin": cx[:8]}


def _file_rows(page, mid: str, headers: dict) -> dict:
    """Ask for the file list of this model by id — the body shape is known."""
    body = json.dumps({"modelId": mid, "page": 1, "pageSize": 10})
    r = _ask(page, "https://%s/api/cxy/v3/model/fileListPage" % ORIGIN, "POST", body,
             headers, False)
    text = r.get("text") or ""
    return {"ms": r.get("ms"), "status": r.get("status"),
            "code": (re.search(r'"code"\s*:\s*(-?\d+)', text) or [None, None])[1],
            "ids": re.findall(r'"id"\s*:\s*"([0-9a-f]{24})"', text)[:6],
            "fileKeys": re.findall(r'"fileKey"\s*:\s*"([^"]{6,90})"', text)[:6],
            "names": re.findall(r'"fileName"\s*:\s*"([^"]{2,80})"', text)[:6],
            "formats": re.findall(r'"fileFormat"\s*:\s*"([^"]{1,10})"', text)[:6],
            "head": text[:200]}


def _bytes(page, url: str) -> dict:
    """Fetch the URL the download endpoint gave and prove it is a real file."""
    got = page.evaluate("""async (u) => {
        const t0 = performance.now();
        try {
            const r = await fetch(u, {credentials: 'include'});
            const b = await r.arrayBuffer();
            const head = new Uint8Array(b.slice(0, 8));
            return {ok: r.ok, status: r.status, ms: Math.round(performance.now() - t0),
                    bytes: b.byteLength, head: [...head].map(x => x.toString(16).padStart(2,'0')),
                    ct: r.headers.get('content-type') || '',
                    disp: (r.headers.get('content-disposition') || '').slice(0, 80)};
        } catch (e) { return {error: String(e).slice(0, 140)}; }
    }""", url) or {}
    return got


def _three_df_ids(page, mid: str, headers: dict) -> dict:
    """The id 3mfDownload wants may not be the id fileListPage gave.

    A .stl-only listing produced code 517 ('the item does not exist'), which reads
    as 'no such 3MF', not 'not allowed' — so the id, not the auth, is the suspect.
    These are the site's own enumeration endpoints; each is a read and each is
    asked with the model group id we already have.
    """
    tries = [("/api/cxy/v3/model/3mfPrinterList", {"modelGroupId": mid}),
             ("/api/cxy/v3/model/3mfList",
              {"page": 1, "pageSize": 10, "modelGroupId": mid}),
             ("/api/cxy/v3/model/fileList", {"modelId": mid})]
    out = {}
    for path, body in tries:
        r = _ask(page, "https://%s%s" % (ORIGIN, path), "POST", json.dumps(body),
                 headers, False)
        text = r.get("text") or ""
        name = path.rsplit("/", 1)[-1]
        out[name] = {"ms": r.get("ms"), "status": r.get("status"),
                     "code": (re.search(r'"code"\s*:\s*(-?\d+)', text) or [None, None])[1],
                     "ids": sorted(set(re.findall(r'"id"\s*:\s*"([0-9a-f]{24})"', text)))[:8],
                     "formats": sorted(set(re.findall(r'"fileFormat"\s*:\s*"([^"]{1,8})"',
                                                      text)))[:6],
                     "keys": sorted(set(re.findall(r'"(\w+)"\s*:', text)))[:30],
                     "head": re.sub(r"https?://\S+", "<URL>", text)[:300]}
    return out


def _bytes_remote(url: str) -> dict:
    """Fetch a signed URL from Python, because the CDN sends no CORS header.

    3mfDownload's downloadUrl points at an OSS bucket with an Expires/Signature
    query, which is a self-contained grant: no cookie, no token. An in-tab fetch
    is refused by CORS ('Failed to fetch'), so the browser is the wrong tool for
    this one hop. The URL is used in memory only — the capture stores its shape,
    never the signature.
    """
    import requests
    t0 = time.perf_counter()
    try:
        with requests.get(url, stream=True, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            it = r.iter_content(262144)
            first = next(it, b"") or b""
            n = len(first)
            body = first[:4096]
            for chunk in it:
                n += len(chunk)
                if len(body) < 4096:
                    body += chunk[:4096 - len(body)]
            out = {"ok": r.ok, "status": r.status_code, "bytes": n,
                   "head": first[:8].hex(), "ct": r.headers.get("content-type", ""),
                   "disp": (r.headers.get("content-disposition") or "")[:60],
                   "ms": round((time.perf_counter() - t0) * 1000)}
            if not r.ok:
                # OSS says exactly why (SignatureDoesNotMatch, AccessDenied, ...)
                out["why"] = re.sub(r"<[^>]+>", " ",
                                    body.decode("utf-8", "replace")).strip()[:220]
            return out
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _remote_bytes(url: str) -> dict:
    """Try the signed URL as given, then with the key's %2F sent as a real slash.

    OSS signs the unescaped object key; a client that forwards '%2F' verbatim is
    signing something else and gets 401. Both forms are cheap, so try both and say
    which one the bucket accepted.
    """
    path, _, query = url.partition("?")
    alt = re.sub(r"%2F", "/", path, flags=re.I) + ("?" + query if query else "")
    got = _bytes_remote(url)
    got["variant"] = "as-given"
    if got.get("ok"):
        return got
    if alt != path + ("?" + query if query else ""):
        second = _bytes_remote(alt)
        second["variant"] = "slash-decoded"
        if second.get("ok"):
            return second
        got["alt"] = second
    return got


FILE_EXT_RE = re.compile(r'\.(?:3mf|stl|obj|zip|cxbin|gcode)(?:\?|$)', re.I)


def _file_urls(page, mid: str, ids: list, headers: dict) -> dict:
    """Can the file be obtained without a click? Two of the site's own reads.

    Learned so far: fileListPage answers on cookies alone in ~130 ms and gives
    the file row (id, fileKey model/<md5>.cxbin, size, format). 3mfDetail on the
    3mfList id answers code 0 but with `"filekey": ""` and only gallery covers —
    so its URLs are NOT the file and must not count as one. 3mfDownload is the
    endpoint whose whole job is handing back the file, so any URL it returns is
    a candidate; from 3mfDetail only a URL with a model-file extension counts.
    memberDownload is deliberately NOT tried: for a paid listing it could consume
    a trial, which is an account change, not a read.

    `ids` is every 24-hex id seen for this model; the loop stops the moment a real
    file URL appears, because each ask is a request on the user's session.
    """
    out = {}
    asked = 0
    for fid in [i for i in ids if i][:4]:
        cands = [("3mfDetail", {"modelGroupId": mid, "id": fid}, False),
                 ("3mfDownload", {"id": fid}, True),
                 ("3mfDownload", {"modelGroupId": mid, "id": fid}, True)]
        for path, body, use_tok in cands:
            if asked >= 9:
                return out
            asked += 1
            key = "%s %s%s" % (fid[-6:], path, "+token" if use_tok else "")
            r = _ask(page, "https://%s/api/cxy/v3/model/%s" % (ORIGIN, path), "POST",
                     json.dumps(body), headers, use_tok)
            text = r.get("text") or ""
            # a signed URL copied out of a rendered string often carries XML
            # entities; leaving them in strips the Signature and OSS then answers
            # "AccessDenied / bucket acl" instead of a signature mismatch
            plain = text.replace("&amp;", "&").replace("\\u0026", "&")
            every = re.findall(r'"(https?://[^"]{10,2000})"', plain)
            urls = every if path == "3mfDownload" else [u for u in every
                                                        if FILE_EXT_RE.search(u)]
            fk = re.search(r'"file[kK]ey"\s*:\s*"([^"]{0,120})"', text)
            row = {"req": body, "ms": r.get("ms"), "status": r.get("status"),
                   "token_len": r.get("token_len"), "token_src": r.get("token_src"),
                   "code": (re.search(r'"code"\s*:\s*(-?\d+)', text) or [None, None])[1],
                   "msg": (re.search(r'"msg"\s*:\s*"([^"]{0,90})"', text) or [None, ""])[1],
                   "filekey": fk.group(1) if fk else "",
                   "amp": text.count("&amp;"),
                   "url_len": len(urls[0]) if urls else 0,
                   "keys": sorted(set(re.findall(r'"(\w+)"\s*:', text)))[:40],
                   "url_shapes": [_url_shape(u) for u in urls[:4]],
                   "rel": re.findall(r'"([\w/\-.]{6,120}(?:\.3mf|\.stl|\.obj|\.cxbin|\.zip))"',
                                     text)[:6],
                   "head": re.sub(r"https?://\S+", "<URL>", text)[:400]}
            out[key] = row
            if urls:
                # in-tab only makes sense on our own origin; the file lives on a
                # CDN bucket, so the hop that can work is the Python one
                if ORIGIN in urls[0]:
                    row["bytes"] = _bytes(page, urls[0])
                    row["bytes"]["url_shape"] = _url_shape(urls[0])
                else:
                    row["remote"] = _remote_bytes(urls[0])
                    row["remote"]["url_shape"] = _url_shape(urls[0])
                return out
    return out


def _state(page) -> dict:
    try:
        return page.evaluate("() => ({vis: document.visibilityState, "
                             "focus: document.hasFocus(), url: location.href})") or {}
    except Exception as e:
        return {"error": str(e)[:100]}


def main() -> int:
    state = run_busy()
    if state not in ("idle", "done"):
        print("NOT PROBING: dashboard auto state '%s' — a run owns the browser." % state)
        return 3
    want_dl = "--download" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    url = args[0] if args else (recent_model_urls(1) or [None])[0]
    if not url:
        print("No model URL to probe.")
        return 2

    from playwright.sync_api import sync_playwright
    from uploader import ensure_browser, CDP_URL
    if not ensure_browser():
        print("Chrome with the debug port is not available — nothing was opened.")
        return 4

    out = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "model": url,
           "download_endpoint_called": want_dl}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        calls: list = []
        page = ctx.new_page()
        page.on("response", _capture(calls))
        filler = None
        try:
            t0 = time.perf_counter()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            t_dom = time.perf_counter() - t0
            # the SSR payload is an inline script at the end of <body>: it is
            # there before the SPA boots, so read it now, not after a wait
            ssr_ms = round((time.perf_counter() - t0) * 1000)
            out["boot"] = {"dom_ms": round(t_dom * 1000),
                           "title": page.title(), "state": _state(page)}
            print("1) document ready in %.1f s  title=%r  %s"
                  % (t_dom, out["boot"]["title"][:60], out["boot"]["state"]))

            out["nuxt_at_dom"] = _payload(page, "at DOMContentLoaded")
            f = (out["nuxt_at_dom"] or {}).get("fields") or {}
            print("   __NUXT__ at DOMContentLoaded: %s"
                  % (out["nuxt_at_dom"].get("missing") and "NOT PRESENT YET"
                     or "%d nodes, top keys %s" % (out["nuxt_at_dom"].get("nodes") or 0,
                                                   out["nuxt_at_dom"].get("top"))))
            for k in WANT[:21]:
                if k in f:
                    print("      %-14s %s" % (k, f[k]))
            print("   covers: %s" % {k: len(v) for k, v in
                                     (out["nuxt_at_dom"].get("covers") or {}).items()})
            dl = (out["nuxt_at_dom"] or {}).get("descs") or []
            print("   long text fields in the payload: %s"
                  % (["%s=%r" % (k, v[:60]) for k, v in dl][:4] or "NONE — the "
                     "description is not in the SSR payload"))
            print("   (read after DOMContentLoaded only: %d ms total)" % ssr_ms)

            # let the SPA boot so its own calls are captured, but report how long
            # that took too — that is the cost the API route skips
            t1 = time.perf_counter()
            try:
                page.wait_for_function(
                    """() => { const h = document.querySelector('h1');
                               return !!((h && (h.innerText||'').trim()) &&
                                       document.images.length >= 3); }""", timeout=20000)
            except Exception:
                out["boot"]["rendered"] = False
            out["boot"]["render_ms"] = round((time.perf_counter() - t1) * 1000)
            out["boot"]["h1"] = page.evaluate(
                "() => { const h = document.querySelector('h1');"
                " return h ? (h.innerText||'').trim() : ''; }")
            out["boot"]["api_calls"] = len(calls)
            print("   SPA painted in another %.1f s: h1=%r, %d api calls"
                  % (out["boot"]["render_ms"] / 1000, out["boot"]["h1"][:48], len(calls)))

            if not calls:
                # A warm load can answer the whole model from the SSR payload
                # without calling the API once — that is exactly the cheap read we
                # want, but it leaves no headers to replay. Reload to get them.
                page.reload(wait_until="domcontentloaded")
                try:
                    page.wait_for_function(
                        "() => performance.getEntriesByType('resource')"
                        ".filter(r => /\\/api\\//.test(r.name)).length > 0", timeout=15000)
                except Exception:
                    pass
                out["boot"]["api_calls_after_reload"] = len(calls)
                print("   reload -> %d api calls" % len(calls))

            hdr = {}
            for c in sorted(calls, key=lambda c: 0 if "/api/cxy/" in c["url"] else 1):
                if c.get("req_headers"):
                    hdr = _replay_headers(c.get("req_headers"))
                    break
            if hdr:
                out["hdr_from_call"] = re.sub(r"https?://[^/]+", "", c["url"])[:70]
                print("   headers taken from %s -> %s"
                      % (out["hdr_from_call"], sorted(hdr)))
            if not hdr:
                # no call captured: fall back to the public set the site always
                # sends, without any value we cannot see
                hdr = {"content-type": "application/json", "accept": "application/json"}
            mid = ""
            for c in sorted(calls, key=lambda c: 0 if "fileList" in c["url"] else
                            (1 if "/model/" in c["url"] else 2)):
                m = re.search(r'"(?:modelId|modelGroupId)"\s*:\s*"([0-9a-f]{24})"',
                              c.get("req_body") or "")
                if m:
                    mid = m.group(1)
                    break
            if not mid:
                m = re.search(r'"(?:modelId|id)"\s*:\s*"([0-9a-f]{24})"',
                              "".join(c.get("body") or "" for c in calls))
                mid = m.group(1) if m else ""
            src = "captured api call"
            if not mid:
                mid = str(((out["nuxt_at_dom"] or {}).get("fields") or {})
                          .get("modelNodeId") or "")
                src = "the document payload"
            out["model_id"] = mid
            out["model_id_from"] = src
            print("   model id %s (from %s), replay headers %s"
                  % (mid or "??", src, sorted(hdr)))

            out["shapes"] = _api_shapes(page)
            print("   %d model-related endpoints found in %d bundles:"
                  % (out["shapes"]["count"], out["shapes"]["scripts"]))
            for p, a in list((out["shapes"]["shapes"] or {}).items())[:12]:
                print("      %-46s %s" % (p, a[:80]))
            for s in (out["shapes"].get("cxbin") or [])[:4]:
                print("      cxbin: ...%s..." % s[-110:])

            files = _file_rows(page, mid, hdr) if mid else {}
            out["files"] = files
            print("\n3) fileListPage %s ms status %s -> ids %s formats %s names %s"
                  % (files.get("ms"), files.get("status"), files.get("ids"),
                     files.get("formats"), files.get("names")))

            if want_dl and files.get("ids"):
                enum = _three_df_ids(page, mid, hdr)
                out["id_source"] = enum
                print("\n4) ids the 3MF/file enumeration endpoints give for this model:")
                for k, d in enum.items():
                    print("   %-16s %s ms status %s code %s -> ids %s formats %s"
                          % (k, d.get("ms"), d.get("status"), d.get("code"),
                             d.get("ids"), d.get("formats")))
                    if not d.get("ids"):
                        print("      keys %s\n      head %s"
                              % (d.get("keys"), (d.get("head") or "")[:180]))
                three = []
                for d in enum.values():
                    three += [i for i in (d.get("ids") or []) if i not in three]
                # the 3MF ids first: they are the ones that can produce a file URL
                cand = three + [i for i in files["ids"] if i not in three]
                out["download"] = _file_urls(page, mid, cand, hdr)
                print("\n5) the file endpoints:")
                for k, d in (out["download"] or {}).items():
                    print("   %-22s %s -> %s ms code %s msg %r token %s@%s"
                          % (k, d.get("req"), d.get("ms"), d.get("code"),
                             d.get("msg"), d.get("token_len"),
                             d.get("token_src") or "none"))
                    print("      urls %s  rel %s  filekey %r  amp=%s url_len=%s"
                          % (d.get("url_shapes"), d.get("rel"), d.get("filekey"),
                             d.get("amp"), d.get("url_len")))
                    if d.get("bytes"):
                        b = d["bytes"]
                        print("      in-tab bytes: %s %s magic %s in %s ms %s"
                              % (b.get("bytes"), b.get("ct"), b.get("head"),
                                 b.get("ms"), b.get("error") or "fetched"))
                    if d.get("remote"):
                        b = d["remote"]
                        print("      python bytes: %s %s magic %s in %s ms (%s) %s"
                              % (b.get("bytes"), b.get("ct"), b.get("head"),
                                 b.get("ms"), b.get("variant"),
                                 b.get("error") or "fetched"))
                        if b.get("why"):
                            print("      OSS said: %s" % b["why"])
                        if b.get("alt"):
                            print("      the other URL form: %s %s %s"
                                  % (b["alt"].get("status"), b["alt"].get("bytes"),
                                     (b["alt"].get("why") or "")[:120]))
                    print("      head %s" % (d.get("head") or "")[:220])
                got = any(d.get("url_shapes") for d in (out["download"] or {}).values())
                ok = [d["remote"] for d in (out["download"] or {}).values()
                      if (d.get("remote") or {}).get("ok")
                      and not str((d.get("remote") or {}).get("head", "")).startswith("3c3f")]
                print("   A USABLE FILE URL WITHOUT A CLICK: %s"
                      % (("YES — %s bytes, magic %s, in %s ms (%s)"
                          % (ok[0]["bytes"], ok[0]["head"], ok[0]["ms"],
                             ok[0].get("variant")))
                         if ok else
                         ("URL SEEN BUT BYTES DID NOT FETCH" if got else
                          "NO — the file step stays click-based")))
            else:
                print("\n4) file endpoints NOT called (pass --download to try them)")

            # timing of the whole read chain, in a tab whose focus we now report
            filler = ctx.new_page()
            filler.goto("about:blank")
            time.sleep(0.4)
            st = _state(page)
            times = []
            for _ in range(5):
                r = _ask(page, "https://%s/api/cxy/v3/model/fileListPage" % ORIGIN,
                         "POST", json.dumps({"modelId": mid, "page": 1, "pageSize": 1}),
                         hdr, False)
                times.append(r.get("ms"))
                time.sleep(0.2)
            out["chain"] = {"state_during": st, "file_list_ms": times,
                            "status": r.get("status")}
            print("\n6) tab state during repeats: %s" % st)
            print("   fileListPage x5 = %s ms (status %s)" % (times, r.get("status")))
            print("   the read chain is %s: page load %.1f s + %d POSTs of ~%d ms"
                  % ("a hidden tab" if st.get("vis") == "hidden" else
                     "a FOREGROUND tab (Chrome never hid it)",
                     t_dom, 2, int(sum(t for t in times if t) / max(len(times), 1))))
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
            print("FAILED: %s" % out["error"])
        finally:
            for p in (page, filler):
                try:
                    p and p.close()
                except Exception:
                    pass

    out_path = os.path.join(BASE, "debug",
                            "api_deep_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=2)
    print("\nsaved -> %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
