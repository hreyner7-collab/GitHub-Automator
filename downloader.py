import re
import os
import json
import requests
from urllib.parse import urlparse

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
                for _ in range(3):  # trigger lazy-loaded gallery images
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(500)
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


def _download_images(images: list[str], slug: str, limit: int = 12,
                     strict: bool = True) -> list[str]:
    """Download images and keep the usable ones, in the order given.

    strict=True applies the blur/size audit used for opportunistic scans.
    strict=False is for images already proven to be the CREATOR'S OWN gallery
    photos: the blur floor must not throw away a real photo (soft studio shots
    score well under the Laplacian floor while being perfectly good listings).

    Every kept photo is converted to a format the upload form accepts, so the
    set handed to the uploader is exactly the set that can be published.
    """
    paths = []
    for i, img_url in enumerate(images[:limit]):
        ext = img_url.split("?")[0].split(".")[-1].lower()
        if not ext.isalpha() or len(ext) > 4:
            ext = "jpg"
        p = os.path.join(DOWNLOADS_DIR, f"{slug}_img_{i}.{ext}")
        try:
            _save(img_url, p)
            ok = os.path.getsize(p) > 1024  # skip empty/broken
            if ok and _ai_label_reason(p):
                ok = False                  # labelled as AI-generated: never a listing photo
            if ok and ext not in UPLOAD_IMAGE_EXTS:
                # convert first, then audit the real pixels of the converted file
                p = to_uploadable(p)
                ok = bool(p)
            if ok and strict:
                ok = _image_quality_ok(p)     # tiny thumbnails & blurry blobs
            elif ok:
                ok = _image_quality_ok(p, min_side=200, min_sharpness=0.0)
            if ok and _flat_render_reason(p):
                ok = False                  # a CAD/slicer-style render, not the object
            if ok and strict and _background_reason(p):
                ok = False                  # opportunistic scans: not on a desk/table or black backdrop
            if ok and p:
                paths.append(p)
            elif p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
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
                # Let lazy-loaded images settle (networkidle never fires on MakerWorld
                # due to constant analytics traffic, so wait on a fixed delay instead)
                await page.wait_for_timeout(5000)
                # Scroll to trigger any lazy loading
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(500)

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
            return {"name": name, "description": description[:500], "images": product_imgs}

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
    name = data.get("name", f"Model {model_id}")
    slug = _slug(name)
    description = data.get("description", "")
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

    return {"name": name, "description": description, "stl_path": stl_path, "image_paths": image_paths, "slug": slug}


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


def _fetch_creality(url: str) -> dict:
    """Fetch a Creality Cloud model: metadata + original photos + the real
    3MF file. The file download is login/Cloudflare-gated, so this drives
    the user's logged-in debug Chrome over CDP (same bootstrap as the
    uploader). Raises RuntimeError when that browser is unavailable."""
    from browser_opener import sync_playwright_auto
    from uploader import ensure_browser, CDP_URL, UPLOAD_VERIFY

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
                page.wait_for_timeout(1500)
            page.wait_for_timeout(8000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(600)

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
            gallery_js = """
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
                        /resize[,\\/]+(?:h|w)_(?:[1-9]|[1-9][0-9]),/i.test(s);
                    const out = [];
                    // Every caller filters first; `add` stores the URL WITHOUT
                    // its query, which is the full-size original the CDN serves
                    // (the rendered thumbnail is a resize,w_40 variant).
                    const add = (s) => {
                        const key = (s || '').split('?')[0];
                        if (key && out.indexOf(key) < 0) out.push(key);
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
                            const s = im.currentSrc || im.src ||
                                      im.getAttribute('data-src') || '';
                            if (!isGalleryPhoto(s) || isNonPhoto(s) ||
                                isTinyVariant(s) || bad(im)) continue;
                            add(s);
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
                            const s = im.currentSrc || im.src || '';
                            if (!isGalleryPhoto(s) || isNonPhoto(s) ||
                                isTinyVariant(s)) continue;
                            if ((im.naturalWidth || 0) < 200) continue;
                            if (bad(im)) continue;
                            // The .cover/.thumb wrapper requirement is GONE: Creality
                            // rolled layouts where the gallery renders photos without
                            // those wrappers (and lazy swipers that show one slide
                            // until the arrow is clicked). The gallery BLOCK itself
                            // (div.model-gallery / .model-content) is the scope — the
                            // plate cards live in .print-config-comp / .list-3mf,
                            // measured OUTSIDE div.model-gallery, and bad() rejects
                            // those containers anyway.
                            add(s);
                        }
                    }
                    // 2b) last resort on unusual layouts: any creator photo on
                    // the page that is not a promo/avatar/comment/ad-rail one.
                    // Guarantees the listing is never left with zero photos.
                    if (!out.length) {
                        for (const im of document.querySelectorAll('img')) {
                            const s = im.currentSrc || im.src || '';
                            if (!isCreator(s) || isNonPhoto(s) ||
                                isTinyVariant(s)) continue;
                            if ((im.naturalWidth || 0) < 200 || bad(im) ||
                                badPromo(im)) continue;
                            add(s);
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
                    return {urls: out, strip: stripCount,
                            strip_cls: strip ? (strip.className||'').toString().slice(0,44) : '',
                            next: nb};
                }
            """

            def _grab(_where):
                """Read the gallery rule and merge any new creator photos."""
                g = page.evaluate(gallery_js)
                added = 0
                for u in g.get("urls", []):
                    if u not in gallery_imgs:
                        gallery_imgs.append(u)
                        added += 1
                _grab.strip = max(getattr(_grab, "strip", 0), g.get("strip", 0) or 0)
                return g, added

            gallery_imgs, stale = [], 0
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

            for _step in range(12):          # click the next-arrow through the set
                gallery_scope = g.get("cls") or gallery_scope
                if not g.get("next"):
                    break
                page.mouse.click(g["next"]["x"], g["next"]["y"])
                page.wait_for_timeout(950)
                g, added = _grab("arrow")
                gallery_steps += 1
                stale = stale + 1 if not added else 0
                if stale >= 3:
                    break
            # a full scroll pass catches anything that only loads in view
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1600)
            g, _ = _grab("scrolled")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(600)
            g, _ = _grab("back-to-top")
            gallery_scope = g.get("cls") or gallery_scope
            gallery_info = {
                "scope": gallery_scope, "images": len(gallery_imgs),
                "strip_thumbs": getattr(_grab, "strip", 0),
                "matches_strip": len(gallery_imgs) >= (getattr(_grab, "strip", 0) or 0),
                "arrow_clicks": gallery_steps,
                "all_creator": True,   # every one is the model's own photo
                                       # (/crealityCloud/upload/ or /common/)
                "paths": [u.replace("https://pic2-cdn.creality.com", "")[:80]
                          for u in gallery_imgs[:12]],
            }
            UPLOAD_VERIFY["gallery"] = gallery_info

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

            # ---- name: the page's own h1 when it is the model name, else the
            # SEO title with its localised tail stripped (see _model_name_from)
            name = _model_name_from(data.get("title", ""), data.get("h1", ""))
            slug = _slug(name)
            description = (data["desc"] or "")[:500]

            # ---- original photos
            image_paths = _download_images(gallery_imgs, slug, strict=False)
            gallery_info["downloaded"] = len(image_paths)
            UPLOAD_VERIFY["gallery"] = gallery_info

            # ---- the real model file: split-button 'Download 3MF' (this
            # layout) or 'View STL/CAD Files' -> menu item 'Download 3MF'
            def _find(text_re, min_w=40):
                return page.evaluate(FIND_JS, text_re)

            stl_path = None
            # The control's LABEL is in the page's own language, and on the
            # French layout (2026-10-09: 6 photos came back but no model file,
            # so the mandatory proof could not be built) the flow is TWO steps:
            #   1. "Voir les fichiers STL/CAO" (View STL/CAD files) opens a dialog
            #      listing the printable files;
            #   2. that dialog's own control — "Tout télécharger" (Download all)
            #      or a "Télécharger 3MF" item — starts the download.
            # An English-layout page offers "Download 3MF"/"View STL/CAD Files"
            # directly, so both are tried, in the site's own wording.
            main = _find(DOWNLOAD_MAIN_RE)
            if main:
                page.mouse.click(main["x"], main["y"])
                page.wait_for_timeout(1200)
                if not downloads:      # step 2, once the dialog/menu has appeared
                    for _ in range(8):
                        item = (_find(DOWNLOAD_3MF_RE)
                                or _find(DOWNLOAD_VERB_RE)
                                or _find(r"\b3MF\b"))
                        if item and item.get("t") != main.get("t"):
                            page.mouse.click(item["x"], item["y"])
                            break
                        page.wait_for_timeout(1000)
                for _ in range(45):
                    if downloads:
                        break
                    page.wait_for_timeout(1000)
                if downloads:
                    d = downloads[0]
                    fname = (d.suggested_filename or (slug + ".3mf")).replace("/", "_")
                    stl_path = os.path.join(DOWNLOADS_DIR, fname)
                    d.save_as(stl_path)
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
            "gallery": gallery_info}   # proof of WHICH images were taken


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
