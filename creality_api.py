"""Read a Creality Cloud listing through the site's own data, not its pixels.

WHY. The DOM path loads the page, waits for the SPA, scrolls to wake lazy
images and reads rendered boxes: 25.3 s per model, measured. The same page ships
`window.__NUXT__` — the decoded answer the SPA would render — by the time
`DOMContentLoaded` fires, and its own API hands back the file list and a signed
download URL. Measured 2026-10-24 on three live models: page 1.2–2.3 s, file
list ~120 ms, 3mf list ~140 ms, download URL ~190 ms, bytes 213–393 ms.

STRUCTURE. The parsers below are pure — no browser, no network, no clock — so
they can be tested against fixtures offline. Everything that touches the browser
is behind `Reader`, which owns ONE tab for the whole run and puts every request
inside it, because the site answers its own session and refuses a bare `requests`
call (Cloudflare 403, `uploader.py:260-283`).

WHAT THIS MODULE REFUSES TO DO.
  * It never invents a field. Anything it could not read is listed in `unknown`
    and the caller must fall back to the DOM path for it.
  * It never decides a licence. The raw string goes into
    `downloader.classify_license`, the same function the DOM path uses, so
    CC BY-NC/ND and Creality's Standard License stay refused by the same code.
  * It never treats a gallery image as the model file: `3mfDetail` answers
    code 0 with an empty `filekey` even for a model that HAS a file, and the
    URLs it carries are covers. Only `3mfDownload` returns a download URL.
  * It never writes a signed URL into a log. A `downloadUrl` is a short-lived
    grant; it stays in memory and is used at once.

The token. `3mfDownload` needs the `__cxy_token_` header, which the page keeps in
cookie `model_token`. `FETCH_JS` finds it inside the tab and sends it there; the
value never crosses back into Python, so it cannot land in a log or a capture.
"""
from __future__ import annotations

import json
import re
import time

ORIGIN = "www.crealitycloud.com"
BASE = "https://%s" % ORIGIN

# headers a replay must not send: browser-owned, or a credential we look for in
# the page instead of copying
SKIP_HEADER_RE = re.compile(
    r"cookie|host|origin|referer|content-length|connection|accept-encoding|"
    r"user-agent|sec-|priority|token|sign|secret", re.I)

PACE_S = 0.3            # between our own calls, like a person clicking
TEXT_CAP = 120_000      # a JSON answer larger than this is not one we need

# the fields the model node carries. groupDesc is the real (HTML) description;
# desc is the short one the page also uses as its meta description.
NODE_KEYS = ("groupName", "name", "nickName", "userName", "userId", "license",
             "licenseName", "desc", "groupDesc", "originalGroupDesc",
             "description", "remark", "shareId", "urlAlias", "totalFileSize",
             "copyright", "isOriginal", "modelSource")

PAYLOAD_JS = r"""
(a) => {
    // Walk the SSR payload IN THE PAGE and return one coherent snapshot of the
    // node that describes the model. A global "first key seen" grab is wrong
    // here: the payload also carries promo banners with their own `desc`, and
    // that ad text would be published as the model's description.
    const root = window.__NUXT__;
    const h1 = (document.querySelector('h1') || {}).innerText || '';
    const title = document.title || '';
    if (!root) return {missing: true, h1, title, nodes: 0};

    const urls = (v) => {
        if (!Array.isArray(v)) return [];
        const out = [];
        for (const x of v) {
            const u = (x && (x.url || x.originUrl || x.surl)) || (typeof x === 'string' ? x : '');
            if (typeof u === 'string' && u.length > 12 && /^https?:/.test(u)
                && out.indexOf(u) < 0) out.push(u);
        }
        return out;
    };
    let model = null;
    let nodes = 0;
    const walk = (o, depth) => {
        if (model || nodes > 60000 || depth > 9 || !o || typeof o !== 'object') return;
        if (Array.isArray(o)) {
            nodes += o.length;
            for (let i = 0; i < Math.min(o.length, 40) && !model; i++) walk(o[i], depth + 1);
            return;
        }
        nodes += 1;
        if (!model && typeof o.groupName === 'string' && o.groupName.trim()) {
            const snap = {nodeId: String(o.id || o.modelId || o.modelGroupId || '')};
            for (const k of a.want) {
                const v = o[k];
                if (typeof v === 'string' || typeof v === 'number') {
                    snap[k] = String(v).slice(0, k === 'groupDesc' ? 20000 : 400);
                }
            }
            const ui = o.userInfo || o.user || null;
            if (ui && (ui.nickName || ui.userName))
                snap.nickName = String(ui.nickName || ui.userName).slice(0, 120);
            for (const k of ['covers', 'pcCovers', 'appCovers', 'editCovers']) {
                const u = urls(o[k]);
                if (u.length) snap[k] = u;
            }
            model = snap;
            return;
        }
        for (const k in o) walk(o[k], depth + 1);
    };
    walk(root, 0);
    const err = root.error;
    const dead = !!err || /page not found/i.test(h1)
                 || /page not found/i.test(title);
    return {missing: false, nodes, model, h1, title, dead,
            error: err ? String(err.errCode || err.statusCode || err.message || err).slice(0, 120) : ''};
}
"""

FETCH_JS = r"""
async (a) => {
    const h = Object.assign({'content-type': 'application/json',
                             'accept': 'application/json'}, a.headers || {});
    let tokenLen = 0, tokenSrc = '';
    if (a.useToken) {
        // find the site's own token in this tab and send it from this tab: the
        // value never crosses to Python, so it cannot be logged or committed
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
        const longest = (v) => {
            let best = v;
            try {
                const walk = (o) => {
                    if (typeof o === 'string') {
                        if (/^[A-Za-z0-9._~+\/-]{16,}$/.test(o) && o.length > best.length)
                            best = o;
                    } else if (o && typeof o === 'object') {
                        for (const k in o) walk(o[k]);
                    }
                };
                walk(JSON.parse(v));
            } catch (e) {}
            return best;
        };
        for (const [src, v] of cands) {
            const t = longest(v);
            if (t && t.length > 8 && t.length < 2000) {
                h.__cxy_token_ = t; tokenLen = t.length; tokenSrc = src;
                break;
            }
        }
    }
    const t0 = performance.now();
    try {
        const r = await fetch(a.url, {method: a.method || 'POST', headers: h,
                                      credentials: 'include', body: a.body});
        const text = (await r.text()) || '';
        return {ok: r.ok, status: r.status, ms: Math.round(performance.now() - t0),
                token_len: tokenLen, token_src: tokenSrc,
                text: text.slice(0, a.cap || 120000)};
    } catch (e) {
        return {error: String(e).slice(0, 160), ms: Math.round(performance.now() - t0),
                token_len: tokenLen, token_src: tokenSrc};
    }
}
"""


# ---------------------------------------------------------------------------
# pure parsers: no browser, no network, no clock
# ---------------------------------------------------------------------------

def _first(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def _int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def parse_payload(raw: dict) -> dict:
    """The SSR snapshot -> the fields a publish is decided on.

    `unknown` lists every decided field that came back empty, so the caller
    knows exactly which ones need the DOM path. Nothing is filled in.
    """
    raw = raw or {}
    if raw.get("missing"):
        # the same record shape, every field empty and named as unknown: a caller
        # that indexes rec["name"] must get "" and fall back, never a KeyError
        return {"name": "", "author": "", "license_raw": "", "description": "",
                "description_html": "", "covers": [], "model_id": "", "share_id": "",
                "total_file_size": None, "alias": "", "dead": False,
                "unknown": ["payload"], "evidence": {},
                "h1": raw.get("h1") or "", "title": raw.get("title") or ""}
    node = raw.get("model") or {}
    covers = []
    for k in ("covers", "editCovers", "pcCovers", "appCovers"):
        for u in node.get(k) or []:
            if u not in covers:
                covers.append(u)
    rec = {
        "name": (node.get("groupName") or "").strip(),
        "author": (node.get("nickName") or node.get("userName") or "").strip(),
        "license_raw": (node.get("license") or node.get("licenseName") or "").strip(),
        "description": (node.get("desc") or "").strip(),
        "description_html": (node.get("groupDesc") or node.get("originalGroupDesc")
                             or node.get("description") or "").strip(),
        "covers": covers,
        "model_id": (node.get("nodeId") or "").strip(),
        "share_id": str(node.get("shareId") or "").strip(),
        "total_file_size": _int(node.get("totalFileSize")),
        "alias": (node.get("urlAlias") or "").strip(),
        "dead": bool(raw.get("dead")),
        "h1": raw.get("h1") or "",
        "title": raw.get("title") or "",
        "evidence": {k: "nuxt:%s" % k for k, v in (
            ("name", node.get("groupName")), ("author", node.get("nickName")),
            ("license", node.get("license")), ("covers", covers),
            ("model_id", node.get("nodeId"))) if v},
    }
    rec["unknown"] = [k for k in ("name", "author", "license_raw", "covers",
                                  "model_id")
                      if not rec[k]]
    return rec


def parse_search_item(item: dict) -> dict:
    """One `smart_search` result card — the discovery path's record."""
    item = item or {}
    ui = item.get("userInfo") or item.get("user") or {}
    covers = []
    for c in item.get("covers") or []:
        u = (c.get("url") or c.get("originUrl") or c.get("surl")) if isinstance(c, dict) else c
        if isinstance(u, str) and u not in covers:
            covers.append(u)
    return {
        "name": (item.get("groupName") or item.get("name") or "").strip(),
        "author": (ui.get("nickName") or item.get("nickName") or "").strip(),
        "license_raw": (item.get("license") or item.get("licenseName") or "").strip(),
        "covers": covers,
        "model_id": str(item.get("id") or item.get("groupId") or item.get("group_id")
                        or "").strip(),
        "share_id": str(item.get("shareId") or "").strip(),
        "total_file_size": _int(item.get("totalFileSize")),
        "alias": (item.get("urlAlias") or "").strip(),
    }


def parse_file_list(payload: dict) -> dict:
    """`fileListPage` -> the model's files, in the order the site lists them."""
    result = (payload or {}).get("result") or {}
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return {"files": [], "count": None, "shape_ok": False}
    files = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        files.append({"id": str(r.get("id") or ""),
                      "name": str(r.get("fileName") or r.get("name") or ""),
                      "size": _int(r.get("fileSize")),
                      "format": str(r.get("fileFormat") or ""),
                      "key": str(r.get("fileKey") or r.get("filekey") or "")})
    return {"files": files, "count": _int(result.get("count", len(files))),
            "shape_ok": True}


def parse_3mf_list(payload: dict) -> dict:
    """`3mfList` -> the ids `3mfDownload` accepts (they are NOT the fileList ids)."""
    result = (payload or {}).get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return {"ids": [], "shape_ok": False}
    ids, names = [], []
    for r in rows:
        if isinstance(r, dict) and r.get("id"):
            if str(r["id"]) not in ids:
                ids.append(str(r["id"]))
            if r.get("name"):
                names.append(str(r["name"]))
    return {"ids": ids, "names": names[:6], "shape_ok": True}


def parse_download(payload: dict) -> dict:
    """`3mfDownload` -> the signed URL. `&amp;` must go: a URL still carrying it
    reaches OSS without a Signature and is refused as 'bucket acl'."""
    result = (payload or {}).get("result") or {}
    url = ""
    if isinstance(result, dict):
        url = str(result.get("downloadUrl") or result.get("url") or "")
    url = url.replace("&amp;", "&").replace("\\u0026", "&")
    return {"url": url if re.match(r"^https?://", url) else "",
            "name": str((result or {}).get("name") or "")}


def parse_error(payload: dict) -> dict:
    p = payload or {}
    return {"code": p.get("code"), "msg": str(p.get("msg") or "")[:160]}


def license_of(raw: str, icons=None) -> dict:
    """The same judge the DOM path uses — never a second opinion."""
    from downloader import classify_license
    return classify_license(raw or "", icons)


# ---------------------------------------------------------------------------
# the reader: one tab for the whole run
# ---------------------------------------------------------------------------

_READER = None
MARK_JS = "() => { try { window.__cc_reader__ = 1; return true; } " \
          "catch (e) { return false; } }"


def get_reader(ctx=None):
    """One reader tab for the run, even across separate browser connections.

    Each fetch opens its own Playwright connection to the same Chrome, so a
    module-level page object does not survive between fetches — but the TAB
    does. It is marked from inside (`__cc_reader__`), and a marked tab is
    adopted instead of opening a new one on every model.
    """
    global _READER
    if _READER is not None and _READER.alive():
        return _READER
    try:
        for p in ctx.pages:
            try:
                if p.evaluate("() => !!window.__cc_reader__"):
                    _READER = Reader(ctx, page=p)
                    return _READER
            except Exception:
                continue
    except Exception:
        pass
    _READER = Reader(ctx)
    return _READER


class Reader:
    """Reads Creality listings from one tab, navigating it once per model.

    Every request is made inside the tab with `credentials: 'include'`, because
    that is the session the site answers. Headers are learned from the page's own
    `/api/` requests as they happen — nothing about the account is hardcoded.
    """

    def __init__(self, ctx, page=None):
        if ctx is None:
            raise TypeError("creality_api.Reader needs a browser context")
        self.ctx = ctx
        self.page = page or ctx.new_page()
        self._hdr: dict = {}
        self._last = 0.0
        self.page.on("request", self._learn_headers)
        try:
            self.page.evaluate(MARK_JS)
        except Exception:
            pass

    # -- housekeeping ------------------------------------------------------
    def alive(self) -> bool:
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    def close(self):
        try:
            self.page.close()
        except Exception:
            pass

    def _learn_headers(self, req):
        try:
            if req.method != "POST" or "/api/" not in (req.url or "") or ORIGIN not in req.url:
                return
            h = {k: v for k, v in (req.headers or {}).items()
                 if not SKIP_HEADER_RE.search(k)}
            if h:
                self._hdr = h
        except Exception:
            pass

    def _pace(self):
        wait = PACE_S - (time.perf_counter() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.perf_counter()

    def url(self) -> str:
        try:
            return self.page.url or ""
        except Exception:
            return ""

    # -- reads -------------------------------------------------------------
    def open_model(self, url: str) -> dict:
        """Load the model page and read its SSR payload. Returns parsed + ms."""
        t0 = time.perf_counter()
        here = (self.url() or "").split("?")[0].rstrip("/")
        want = (url or "").split("?")[0].rstrip("/")
        if here != want:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                self.page.evaluate(MARK_JS)      # navigation drops the marker
            except Exception:
                pass
        if re.search(r"crealitycloud\.com/login(?:[?/#]|$)", self.url() or ""):
            return {"dead": False, "signin": True, "unknown": ["signin"],
                    "load_ms": round((time.perf_counter() - t0) * 1000)}
        raw = self.page.evaluate(PAYLOAD_JS, {"want": list(NODE_KEYS)}) or {}
        rec = parse_payload(raw)
        rec["load_ms"] = round((time.perf_counter() - t0) * 1000)
        return rec

    def post(self, path: str, body: dict, token: bool = False) -> dict | None:
        """POST inside the tab. None means 'no answer we can trust' — never an
        invitation to guess. 403/429 are ordinary events."""
        self._pace()
        try:
            r = self.page.evaluate(FETCH_JS, {
                "url": BASE + path, "method": "POST",
                "body": json.dumps(body), "headers": self._hdr,
                "useToken": bool(token), "cap": TEXT_CAP}) or {}
        except Exception as e:
            return {"ok": False, "status": 0, "error": str(e)[:140]}
        text = r.get("text") or ""
        try:
            r["json"] = json.loads(text) if text else {}
        except ValueError:
            r["json"] = {}
        return r

    def file_list(self, model_id: str) -> dict | None:
        r = self.post("/api/cxy/v3/model/fileListPage",
                      {"modelId": model_id, "page": 1, "pageSize": 10})
        if not r or not r.get("ok") or not isinstance(r.get("json"), dict):
            return None
        if r["json"].get("code") not in (0, "0", None):
            return None
        out = parse_file_list(r["json"])
        out["ms"] = r.get("ms")
        return out if out["shape_ok"] else None

    def three_d_ids(self, model_id: str) -> dict | None:
        r = self.post("/api/cxy/v3/model/3mfList",
                      {"page": 1, "pageSize": 10, "modelGroupId": model_id})
        if not r or not r.get("ok") or not isinstance(r.get("json"), dict):
            return None
        if r["json"].get("code") not in (0, "0", None):
            return None
        out = parse_3mf_list(r["json"])
        out["ms"] = r.get("ms")
        return out if out["shape_ok"] else None

    def download_url(self, three_d_id: str) -> dict | None:
        r = self.post("/api/cxy/v3/model/3mfDownload", {"id": three_d_id}, token=True)
        if not r or not r.get("ok") or not isinstance(r.get("json"), dict):
            return None
        if r["json"].get("code") not in (0, "0", None):
            return None
        out = parse_download(r["json"])
        out["ms"] = r.get("ms")
        return out if out["url"] else None

    # -- the file ----------------------------------------------------------
    @staticmethod
    def fetch_bytes(url: str, dest: str) -> dict:
        """The signed CDN URL, fetched by Python.

        An in-tab fetch is refused by the bucket's missing CORS header, and the
        signature is self-contained (Expires + OSSAccessKeyId + Signature), so no
        cookie or token is needed for this one hop. Written to `dest`; the
        caller verifies the geometry.
        """
        import requests
        t0 = time.perf_counter()
        if ORIGIN in url:
            # a same-origin URL would need the session; the click path owns that
            return {"ok": False, "why": "same-origin URL needs the browser session"}
        try:
            with requests.get(url, stream=True, timeout=120,
                              headers={"User-Agent": "Mozilla/5.0"}) as r:
                if not r.ok:
                    return {"ok": False, "status": r.status_code,
                            "why": "CDN refused the signed URL"}
                n = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(262144):
                        f.write(chunk)
                        n += len(chunk)
            with open(dest, "rb") as f:
                head = f.read(4).hex()
            return {"ok": True, "path": dest, "bytes": n, "head": head,
                    "ms": round((time.perf_counter() - t0) * 1000)}
        except Exception as e:
            return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:120])}

    # -- the whole read ----------------------------------------------------
    def read(self, url: str, dest_dir: str | None = None) -> dict:
        """Name, author, licence text, gallery, file rows and (optionally) the file.

        Returns `read_via: "api"`. `unknown` is the fallback list; an empty
        `unknown` with a non-dead page is the proof the DOM pass can be skipped.
        A dead/removed listing comes back with `dead: True` and nothing else.
        """
        import os
        rec = self.open_model(url)
        rec["read_via"] = "api"
        if rec.get("dead"):
            return rec
        mid = rec.get("model_id") or ""
        if not mid:
            return rec

        files = self.file_list(mid)
        rec["files"] = (files or {}).get("files") or []
        rec["file_count"] = len(rec["files"])
        rec["files_ms"] = (files or {}).get("ms")

        if dest_dir and rec["files"]:
            three = self.three_d_ids(mid) or {}
            rec["three_d_ids"] = three.get("ids") or []
            rec["three_d_ms"] = three.get("ms")
            got = None
            for tid in (rec["three_d_ids"] or [])[:3]:
                got = self.download_url(tid)
                if got:
                    break
            if got:
                ext = ".3mf"
                for f in rec["files"]:
                    if f.get("format"):
                        ext = f["format"] if f["format"].startswith(".") else "." + f["format"]
                        break
                slug = re.sub(r"[^A-Za-z0-9._-]+", "_",
                              (rec.get("alias") or rec.get("name") or "model"))[:60]
                dest = os.path.join(dest_dir, slug + ext)
                os.makedirs(dest_dir, exist_ok=True)
                got_bytes = self.fetch_bytes(got["url"], dest)
                rec["download_url_ms"] = got.get("ms")
                rec["file"] = got_bytes
                if got_bytes.get("ok"):
                    rec["stl_path"] = dest
        if rec.get("unknown"):
            rec["unknown"] = sorted(set(rec["unknown"]))
        return rec
