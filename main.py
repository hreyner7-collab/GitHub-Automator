import asyncio
import json
import os
import re
from pathlib import Path
import subprocess
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from downloader import fetch as dl_fetch, classify_license
from editor import edit_all
from uploader import (upload as uploader_upload, _debug_port_up,
                      license_answer_indexes, expected_badge_token,
                      NO_COVERS_ERROR, usable_cover_images)
from discover import discover as discover_models, find_creality, \
    CREALITY_LICENCE_ALLOW, CREALITY_LICENCE_DENY, listing_was_used, remember_tried, was_tried_recently
import analytics
import creality_rules
import ledger
import proof
import selector
import timing

load_dotenv()

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"

app = FastAPI(title="Creality Pipeline")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# ── Which code is this process actually serving? ────────────────────────────
# The dashboard had no version anywhere, so "I restarted it but nothing changed"
# could not be checked by anyone -- not you, and not the supervisor. The stamp is
# read once, at import, from the checkout the process was started in.
STARTED_AT = time.time()


def _write_run_log(path: Path, body: dict) -> None:
    """Write a run log atomically: temp file first, then swap it in.

    A plain write_text can be truncated by a crash mid-write, and this file is
    what seeds "already published" on the next start -- a half-written one
    silently loses that memory. Serializing BEFORE the swap also means a body
    that cannot be written leaves the previous good log untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, indent=1, ensure_ascii=False)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _git(*args: str) -> str:
    """Best-effort git output: "" when git is missing or this is not a checkout."""
    try:
        out = subprocess.run(["git", *args], cwd=str(BASE_DIR), capture_output=True,
                             text=True, timeout=10)
        return (out.stdout or "").strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _build_stamp() -> str:
    """The commit this process was started from, marked when the tree has edits.

    `+edits` matters: the dashboard is usually restarted while the working tree
    still holds uncommitted work, and a bare hash would claim the running code is
    the committed code when it is not.
    """
    rev = _git("rev-parse", "--short", "HEAD")
    if not rev:
        return "unknown"
    return rev + ("+edits" if _git("status", "--porcelain", "--untracked-files=no") else "")


BUILD = _build_stamp()

SERVER_PORT = 8000


@app.on_event("startup")
async def _start_analytics_auto():
    pass  # no hourly browser reads: the account is read only when the user clicks Refresh
    # Learn what is already published before the first upload of the session:
    # the account's own model list + every run log on disk. From here on the
    # pipeline refuses to publish any of those again. The account read is a
    # background task (it drives the browser), so booting never waits on it.
    asyncio.create_task(_seed_ledger())


async def _seed_ledger():
    """Read the account's own model list once at startup, then seed the ledger
    from it and from the run logs already on disk. Best effort: a browser that
    is not up yet just means the last known list is used instead."""
    try:
        pass  # no automatic account read at startup; the disk-only seed below still runs
    except Exception:
        pass
    try:
        await asyncio.to_thread(ledger.startup_seed)
    except Exception:
        pass


# ── Request / Response models ─────────────────────────────────────────────────

class FetchRequest(BaseModel):
    url: str
    edit_images: bool = False   # default: keep the creator's ORIGINAL photos

class UploadRequest(BaseModel):
    name: str
    description: str
    stl_path: str
    image_paths: list[str]
    source_url: str = ""
    license_raw: str = ""
    dry_run: bool = False


class ForgetRequest(BaseModel):
    """Forget one entry so it MAY be published again (you deleted the listing)."""
    match: str

class PhotoSourceRequest(BaseModel):
    value: str = ""


class AuthorizeRequest(BaseModel):
    url: str = ""
    note: str = ""


class PhotoPlanRequest(BaseModel):
    """Re-plan the photos for the model already loaded in the dashboard, after
    the photo switch changed — no second fetch of the live page."""
    stl_path: str = ""
    images: list[str] = []
    name: str = ""
    slug: str = ""


class VerdictRequest(BaseModel):
    """Re-judge the link already loaded, after the authorisation changed."""
    name: str = ""
    source_url: str = ""
    licence_raw: str = ""
    proof_ok: bool = False
    proof_why: str = ""


class EditImagesRequest(BaseModel):
    image_paths: list[str]

class DiscoverRequest(BaseModel):
    site: str
    count: int = 20


class AutoRequest(BaseModel):
    """"Find and publish" run: how many models to find, and (optionally) the
    niches to search for. The count is typed by the user (any number, 1 or more)."""
    count: int = 5
    keywords: list[str] | None = None


# ── Routes ────────────────────────────────────────────────────────────────────

# ── Creality's published rules: one owner (creality_rules) ──────────────────
# `creality_rules.check()` is the ONLY place that decides whether an upload may
# go out: the licence, the not-eligible content classes, the per-source
# authorisation and the proof of creation all come from that module, and both
# publish routes below read it (never their own copy of the rules).

def _build_proof(stl_path: str, name: str, slug: str = "") -> dict:
    """The proof of creation for this model file — a render of the geometry
    being uploaded, with its parameters. Never raises: a failure comes back as
    {ok: False, why: …} and the gate refuses the upload with that sentence."""
    blank = {"ok": False, "png": "", "params": {}, "sha256": "",
             "why": "no model file to build the proof from"}
    if not stl_path:
        return blank
    try:
        return proof.build(stl_path, name=name, slug=slug)
    except Exception as e:
        return dict(blank, why="the proof renderer failed: %s" % str(e)[:160])


def _upload_images(source_images: list, proof_info: dict, stl_path: str,
                   name: str, slug: str = "") -> tuple[list, str, bool]:
    """The photos this listing will carry: ONLY the creator's own photos from
    the original upload, in the order fetched.

    CHANGED 2026-10-14 (user): renders of the model file are never uploaded.
    CAD/slicer-style views and the proof-of-creation PNG were being rejected
    because they do not look like the object as it is fully printed. So there
    is no render fallback and the proof is not attached here. The proof is
    still built and checked upstream (the rules require it), and the other
    arguments are kept so the callers stay the same.

    Returns (paths, note, cover_ok). `cover_ok` says whether a real photo for
    this model is on disk; without one the model is not published."""
    own = [p for p in (source_images or []) if p]
    cover_ok = bool(usable_cover_images(source_images))
    if cover_ok:
        note = ("photos: the creator's originals straight from the original "
                "upload (%d)" % len(own))
    else:
        note = ("photos: no usable creator photo on disk — nothing to publish "
                "with (renders of the model are never uploaded)")
    return own, note, cover_ok


@app.get("/policy")
async def policy_endpoint():
    """Creality's published rules, exactly as creality_rules.py holds them — the
    dashboard reads this instead of keeping its own copy of any table."""
    return creality_rules.policy()


@app.post("/policy/photo-source")
async def set_photo_source_endpoint(req: PhotoSourceRequest):
    """The dashboard's photo switch: which photos a listing carries. Saved to
    creality_rules' settings file and read per request, so it applies to the
    next fetch with no restart. The operator env override still wins."""
    try:
        creality_rules.set_photo_source(req.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return creality_rules.policy()


@app.post("/policy/authorize")
async def authorize_source_endpoint(req: AuthorizeRequest):
    """Authorise a source to be re-published — the dashboard's version of
    scripts/authorize_source.py, so the decision needs no terminal. The reason
    is required: Creality's program page lists re-uploads as not eligible, so
    the only thing that makes one legitimate is a stated right to redistribute
    (your own design, or the creator's permission)."""
    url = (req.url or "").strip()
    note = (req.note or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Paste the model link (or its slug) to authorise.")
    if not note:
        raise HTTPException(status_code=400, detail="Say why it is allowed (your own design, or the creator's permission) — an unexplained authorisation is not one.")
    creality_rules.add_authorized(url, note)
    return creality_rules.policy()


@app.post("/policy/unauthorize")
async def unauthorize_source_endpoint(req: AuthorizeRequest):
    """Take an authorisation back — the same file, one click, no terminal."""
    removed = creality_rules.remove_authorized(req.url)
    out = creality_rules.policy()
    out["removed"] = removed
    return out


@app.post("/policy/verdict")
async def verdict_endpoint(req: VerdictRequest):
    """Re-judge the link already loaded in the dashboard (same one owner) after
    an authorisation change — so the verdict flips without re-fetching the live
    page. The proof result comes from the proof built at fetch time."""
    lic = await asyncio.to_thread(classify_license, req.licence_raw or "")
    proof_info = {"ok": bool(req.proof_ok), "why": req.proof_why or ""}
    if not req.licence_raw:
        lic = None
    return {"policy": creality_rules.check(name=req.name, source_url=req.source_url,
                                           licence=lic, proof=proof_info)}


@app.post("/photo-plan")
async def photo_plan_endpoint(req: PhotoPlanRequest):
    """The photos the listing would carry under the CURRENT switch, for the
    model the dashboard already has — used after the switch changes so the
    tiles and the note update without a second fetch."""
    proof_info = _build_proof(req.stl_path, req.name, req.slug)
    images, note, cover_ok = _upload_images(list(req.images or []), proof_info,
                                            req.stl_path, req.name, req.slug)
    def to_url(p):
        return ("/file/" + Path(p).name) if p else None
    return {"image_urls": [to_url(p) for p in images],
            "_raw_images": images,
            "photo_note": note,
            "cover_ok": cover_ok,
            "photo_source": creality_rules.photo_source()}


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (BASE_DIR / "creality" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.post("/fetch")
async def fetch_and_edit(req: FetchRequest):
    # Run the blocking scrape/download in a worker thread so the event loop
    # (and therefore every other dashboard request) stays responsive.
    try:
        result = await asyncio.to_thread(dl_fetch, req.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")

    if not result.get("stl_path") and not result.get("image_paths"):
        raise HTTPException(status_code=400, detail="Download failed: no model file or "
                            "images could be fetched from that URL (the page may be "
                            "login-gated, removed, or blocking automated access).")

    # Photo policy (user's rule): upload the creator's ORIGINAL photos by
    # default. The AI re-edit is opt-in — the dashboard's "Regenerate photos"
    # button calls /edit-images on demand.
    edited_paths = []
    if req.edit_images and result.get("image_paths"):
        try:
            edited_paths = await asyncio.to_thread(edit_all, result["image_paths"])
        except Exception as e:
            # Don't fail the whole request if editing errors — surface it as warning
            edited_paths = []
            result["edit_warning"] = str(e)

    result["edited_paths"] = edited_paths

    # Convert paths to URL-safe /file/ references for the frontend
    def to_url(p):
        if p:
            return "/file/" + Path(p).name
        return None


    # Licence plan for THIS link: the exact answers the upload will set on
    # Creality's three licence questions and the badge those answers must
    # produce (one source of truth — the uploader). Shown in the dashboard so
    # every model link is checked the same way before anything is published.
    lic = dict(result.get("license") or {})
    try:
        lic_code, lic_idx = license_answer_indexes(lic.get("raw") or "")
        lic["answers"] = lic_idx
        lic["form_answers"] = ["Yes" if i == 0 else ("No" if i == 1 else "ShareAlike")
                              for i in lic_idx]
        lic["form_badge"] = expected_badge_token(lic_code)
    except Exception:
        pass

    # Creality's published rules, applied to THIS link before anything is
    # published: the licence, the not-eligible content classes, the source
    # authorisation and the proof of creation (built from the model file here,
    # so the dashboard can show it and /upload reuses the same judgement).
    model_name = result.get("name") or ""
    model_slug = result.get("slug") or ""
    proof_info = _build_proof(result.get("stl_path"), model_name, model_slug)
    verdict = creality_rules.check(name=model_name, source_url=req.url,
                                   licence=lic, proof=proof_info)
    base_images = edited_paths if edited_paths else result.get("image_paths", [])
    upload_images, photo_note, _cover_ok = _upload_images(
        base_images, proof_info, result.get("stl_path") or "",
        model_name, model_slug)

    return {
        "name": result.get("name", ""),
        "description": result.get("description", ""),
        "source_url": req.url,
        "author": result.get("author", ""),
        "license": lic,
        "stl_path": result.get("stl_path"),
        "stl_url": to_url(result.get("stl_path")),
        "original_images": [to_url(p) for p in result.get("image_paths", [])],
        "edited_images": [to_url(p) for p in edited_paths],
        "edit_warning": result.get("edit_warning"),
        # proof of WHICH photos were taken (gallery scope, adverts excluded)
        "gallery": result.get("gallery") or {},
        "_raw_stl": result.get("stl_path"),
        # The proof of creation (a render of the model file itself) and the
        # verdict Creality's published rules give this link.
        "proof": {"ok": bool(proof_info.get("ok")),
                  "png": to_url(proof_info.get("png")),
                  "params": proof_info.get("params") or {},
                  "why": proof_info.get("why") or ""},
        "policy": verdict,
        "photo_note": photo_note,
        "photo_source": creality_rules.photo_source(),
        # raw filesystem paths = what the uploader needs; image_urls = what the
        # browser can actually render (a raw Windows path as <img src> is broken)
        "_raw_images": upload_images,
        "image_urls": [to_url(p) for p in upload_images],
        "_raw_orig_images": list(result.get("image_paths", [])),
    }


@app.post("/edit-images")
async def edit_images_endpoint(req: EditImagesRequest):
    """Opt-in AI re-edit of the creator's original photos ("Regenerate
    photos"). Returns the edited set; nothing is uploaded here."""
    paths = []
    for p in req.image_paths:
        if not p:
            continue
        q = Path(p)
        if not q.is_absolute():          # bare name from the browser → downloads/
            q = DOWNLOADS_DIR / q.name
        if q.is_file():
            paths.append(str(q))
    if not paths:
        raise HTTPException(status_code=400, detail="No valid image paths to edit.")
    try:
        edited = await asyncio.to_thread(edit_all, paths)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image editing failed: {e}")
    return {"edited_images": ["/file/" + Path(p).name for p in edited],
            "_raw_edited": edited}


@app.post("/discover")
async def discover_endpoint(req: DiscoverRequest):
    """Research only: list the top/most-popular models on a site. Does not
    download, edit, or upload anything."""
    try:
        models = await discover_models(req.site, max(1, min(req.count, 40)))
        return {"site": req.site, "models": models}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Discovery failed: {e}")


def _record_published(*, name: str = "", source_url: str = "", author: str = "",
                      slug: str = "", stl_path: str = "", published_url: str = "",
                      urls=None, outcome: str = "published", note: str = "") -> dict:
    """Remember a publish under EVERY link it is known by.

    Creality answers with an opaque /model-detail/<id> and 301s it to the
    readable /model-detail/<slug>, and the account's own model list uses that
    slug. Storing only the opaque one is how one pallet jack became two ledger
    rows (27 rows for 26 models): the next restart's account seed saw a link the
    entry had never heard of. So the redirect is resolved once here (short
    timeout; a failure just keeps the original spelling) and every spelling is
    recorded as an alias of the one entry.
    """
    links = [u for u in (urls or []) if u]
    if published_url:
        links.insert(0, published_url)
    canonical = ledger.canonical_url(published_url) if published_url else ""
    if canonical and canonical != published_url:
        links.append(canonical)
        published_url = canonical
    return ledger.record(name=name, source_url=source_url, author=author,
                         slug=slug, stl_path=stl_path,
                         published_url=published_url, aliases=links,
                         outcome=outcome, note=note)


@app.post("/upload")
async def upload_to_creality(req: UploadRequest):
    # One writer on the Creality form at a time: an auto run holds the form for
    # its models, so a manual publish waits for it instead of fighting over it.
    if AUTO.get("state") in ("discovering", "running"):
        raise HTTPException(status_code=409, detail=
            "An auto run is publishing right now (%s) — stop it first, or wait "
            "until it finishes, then publish by hand." % AUTO["state"])
    # Already published — never twice. Checked BEFORE any browser opens, against
    # the permanent ledger (source link, listing slug, name + designer, and the
    # model file's own fingerprint). Skipped for a dry run, which publishes
    # nothing.
    if not req.dry_run:
        hit = ledger.find(source_url=req.source_url, name=req.name,
                          file_path=req.stl_path)
        if hit["found"]:
            raise HTTPException(status_code=409, detail=
                "Refusing to publish twice. " + ledger.describe(hit))
    # Creality's published rules decide whether this may be published at all —
    # read from creality_rules (the one owner) instead of any local copy: the
    # licence (Standard / No-Derivatives / NonCommercial / unreadable are all
    # refused, each with its own sentence), the not-eligible content classes,
    # whether THIS account may re-publish this source, and the proof of
    # creation, which is mandatory (a missing proof is what the help centre says
    # may get a model refused). Nothing opens a browser until this passes.
    lic = classify_license(req.license_raw)
    proof_info = _build_proof(req.stl_path, req.name)
    verdict = creality_rules.check(name=req.name, source_url=req.source_url,
                                   licence=lic, proof=proof_info)
    if not verdict["allowed"]:
        raise HTTPException(status_code=403, detail=
            "Refusing to publish: " + verdict["reason"] + "  (rule: " +
            verdict["rule"] + "; source license: " +
            (lic["raw"][:120] or "unknown") + ")")
    # Always credit the original designer: append the source link to the
    # description if it isn't already mentioned.
    description = req.description or ""
    if req.source_url and req.source_url not in description:
        description = (description.rstrip() + "\n\nOriginal design: " + req.source_url).strip()
    # The photos this listing carries: the creator's originals by default, our
    # own renders when the switch says so — plus the proof of creation, so the
    # form itself carries the evidence the review asks for.
    upload_images, photo_note, _cover_ok = _upload_images(
        req.image_paths, proof_info, req.stl_path, req.name)
    try:
        async with UPLOAD_LOCK:
            result = await uploader_upload(req.name, description, req.stl_path,
                                           upload_images, req.source_url,
                                           dry_run=req.dry_run,
                                           license_raw=req.license_raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Remember it forever — but ONLY what really went onto the account. A dry
    # run and a CREALITY_NO_SUBMIT form fill both report success while
    # publishing nothing (that mistake once locked a model the user never
    # uploaded, so it could never be published); a Creality duplicate report IS
    # remembered, because asking Creality the same question twice is a wasted
    # round trip.
    published_now = (result.get("success") and not result.get("dry_run")
                     and not result.get("no_submit")
                     and result.get("published") is not False)
    if published_now or result.get("duplicate"):
        _record_published(name=req.name, source_url=req.source_url,
                          stl_path=req.stl_path,
                          published_url=result.get("url") or "",
                          urls=result.get("urls"),
                          outcome="published" if published_now else "creality-duplicate",
                          note="published by hand from the dashboard")
    if isinstance(result, dict):
        result.setdefault("policy", {"rule": verdict["rule"], "allowed": True,
                                      "checks": verdict["checks"]})
        result.setdefault("proof", {"ok": bool(proof_info.get("ok")),
                                    "png": Path(proof_info.get("png") or "").name,
                                    "sha256": proof_info.get("sha256", "")})
        result.setdefault("photo_note", photo_note)
    return result


# ── Auto: find models, then publish each one ────────────────────────────────
# One run at a time, strictly sequential (fetch and publish both drive the same
# logged-in Chrome, so parallel uploads would fight over one form). ND-licensed
# finds are skipped and another candidate is used; every failure is recorded with
# its reason and the run continues. Progress is polled from /auto/status.
AUTO: dict = {"state": "idle", "job_id": None, "requested": 0, "discovered": 0,
              "items": [], "licence": {}, "keywords": [], "note": "",
              "started": None, "finished": None, "stop": False, "log_path": None,
              "skipped_already": 0, "already_done": [], "rejected_total": 0}
AUTO_TASK: asyncio.Task | None = None
UPLOAD_LOCK = asyncio.Lock()          # the Creality form: one writer at a time

# The default searches are the user's own object families, defined once in
# selector.py — NOT the phrase "household items", which is what surfaced a
# toilet and a bathroom accessory set.
AUTO_DEFAULT_KEYWORDS = list(selector.DEFAULT_KEYWORDS)

# ── Recoverable fetch failures ───────────────────────────────────────────────
# A fetch that dies because the page navigated out from under the scraper, or
# because the internet dropped for a moment, is not a broken model — the user's
# own run skipped models 7 and 8 with exactly "Page.evaluate: Execution context
# was destroyed, most likely because of a navigation" and "Page.goto:
# net::ERR_INTERNET_DISCONNECTED". Those are retried a small bounded number of
# times before the candidate is written off; anything else (a page that really
# is broken, junk geometry) fails the model straight away, as before.
FETCH_RETRYABLE_RE = re.compile(
    r"execution context was destroyed"                 # page navigated mid-scrape
    r"|most likely because of a navigation"
    r"|err_internet_disconnected"                       # temporary network drop
    r"|net::err_|err_connection|err_name_not_resolved|err_empty_response"
    r"|target page, context or browser has been closed"  # the debug Chrome died
    r"|browser has been closed"
    r"|playwright driver died"                            # the node driver subprocess died mid-handshake
    r"|connection closed while reading from the driver"
    r"|object has no attribute '_playwright'",
    re.I)
FETCH_ATTEMPTS = 3            # 1 real try + 2 retries (small, bounded)
FETCH_RETRY_DELAY = 4.0       # seconds between tries

# A model whose fetch never produced what publishing needs is NOT "skipped" —
# skipped is a decision (a licence that forbids re-publishing, a page with no
# model file). A fetch that died on a navigation or the network is a FAILURE, and
# the counts and the run row must say so, so "4 skipped" can no longer hide four
# models that never got published.
FETCH_FAILED = "fetch-failed"


def _fetch_failure_is_recoverable(message: str) -> bool:
    """True when a fetch failure is one of the two the user's run hit and a
    later attempt has a real chance of getting through."""
    return bool(FETCH_RETRYABLE_RE.search(message or ""))


def _remember_outcomes() -> None:
    """Write every listing a run OPENED into the tried record — refused, failed,
    duplicate, or left undecided by a Stop after its page was read. Called with
    each log write, so a run stopped midway keeps what it decided. A listing
    already recorded keeps its first date."""
    for it in AUTO.get("items") or []:
        if listing_was_used(it):
            remember_tried(it.get("url", ""), it.get("name", ""), it.get("reason", ""))


async def _wait_between_fetch_tries(seconds: float) -> bool:
    """Wait between fetch attempts, waking up at once when Stop is pressed, so
    stopping never sits through the whole backoff. Returns True when the run was
    stopped instead of waiting it out."""
    waited = 0.0
    while waited < seconds:
        if AUTO.get("stop"):
            return True
        step = min(0.25, seconds - waited)
        await asyncio.sleep(step)
        waited += step
    return bool(AUTO.get("stop"))


async def _fetch_with_retries(it: dict):
    """`dl_fetch` with the recoverable failures retried. Returns
    (result | None, error | None, attempts, stopped): the attempt history is kept
    on the item so the run log says what actually happened, not just that it
    failed, and `stopped` says the run was stopped while backing off (the model
    was never decided — it is not this model's failure)."""
    attempts, last = [], ""
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with timing.phase("fetch"):
                res = await asyncio.to_thread(dl_fetch, it["url"])
            if attempts:
                it["fetch_attempts"] = attempts
            it["reason"] = ""
            return res, None, attempts, False
        except Exception as e:
            last = str(e)[:300]
            attempts.append("attempt %d: %s" % (attempt, last[:160]))
            it["fetch_attempts"] = attempts
            if not _fetch_failure_is_recoverable(last) or attempt >= FETCH_ATTEMPTS:
                break
            it["reason"] = ("fetch attempt %d/%d died on a navigation/network "
                            "drop — retrying: %s"
                            % (attempt, FETCH_ATTEMPTS, last[:110]))
            if await _wait_between_fetch_tries(FETCH_RETRY_DELAY):
                return None, last, attempts, True
    return None, last, attempts, False


def _fetch_attempts_note(attempts: list) -> str:
    """How many tries a fetch took, so a retried / recovered model says so."""
    if len(attempts or []) <= 1:
        return ""
    return (" (tried %d times: %s)"
            % (len(attempts), "; ".join(a[:90] for a in attempts)))


def _auto_counts() -> dict:
    """Honest tallies, one bucket per outcome. `fetch_failed` is separate from
    `skipped` on purpose: a fetch that died on a navigation or the network is a
    failure, while `skipped` is a decision (licence, no model file)."""
    items = AUTO.get("items") or []
    out = {"published": 0, "skipped": 0, "failed": 0, "fetch_failed": 0,
           "duplicate": 0, "queued": 0, "working": 0, "rejected": 0}
    for it in items:
        st = it.get("state") or "queued"
        if st in ("fetching", "uploading"):
            out["working"] += 1
        elif st == FETCH_FAILED:
            out["fetch_failed"] += 1
        elif st in out:
            out[st] += 1
        else:
            out["queued"] += 1
    return out


async def _auto_worker(count: int, keywords: list[str], job_id: str) -> None:
    import time as _time

    def _log() -> str:
        _remember_outcomes()
        path = BASE_DIR / "artifacts" / ("auto_run_%s.json" % job_id)
        path.parent.mkdir(exist_ok=True)
        body = {
            "job_id": job_id, "requested": AUTO["requested"],
            "keywords": AUTO["keywords"], "licence": AUTO["licence"],
            "counts": _auto_counts(), "items": AUTO["items"],
            "timings": timing.snapshot(),
            "finished": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _write_run_log(path, body)
        return str(path)

    timing.reset()
    _run_t0 = _time.perf_counter()
    try:
        AUTO["state"] = "discovering"
        # a run starts with an empty queue and empty counters, even when the
        # same worker state was used by an earlier run in this process
        AUTO.update({"items": [], "licence": {}, "note": "", "discovered": 0,
                     "rejected_total": 0, "skipped_already": 0, "already_done": []})
        # Models already in the "already published" config are filtered out
        # DURING discovery (not queued and skipped later), so the run never
        # picks a model it has finished before.
        def _already_done(url: str, name: str) -> bool:
            return bool(ledger.find(source_url=url, name=name)["found"]
                        or was_tried_recently(url))

        def _on_progress(p: dict) -> None:
            AUTO["progress"] = p

        def _stop_requested() -> bool:
            return bool(AUTO.get("stop"))

        def _discover(need: int, exclude: set) -> dict:
            # One discovery pass. The first pass fills the queue; a top-up
            # replaces what did not publish. `exclude` holds every listing URL
            # this run already attempted, so none is picked twice.
            disc = find_creality(keywords, need, "Downloads", _already_done,
                                 _on_progress, should_stop=_stop_requested,
                                 exclude=exclude)
            # The user's rule, re-judged here by the one owner (selector.py) even
            # though discovery already applied it: a rubbish or off-family candidate
            # is written into the run as a visible `rejected` row with its reason and
            # can never reach the publisher. Discovery's own refusals are listed too,
            # so the run shows exactly what was passed over and why.
            # `exclude` is enforced here too, not only inside the harvester: a
            # listing already attempted by this run is never handed over again.
            models = [m for m in (disc.get("models") or [])
                      if m.get("url") not in exclude]
            verdicts = selector.filter_candidates(models)
            items = [{"name": m.get("name", ""), "url": m.get("url", ""),
                      "keyword": m.get("keyword", ""), "family": m.get("family", ""),
                      "state": "queued", "reason": "", "result": ""}
                     for m in verdicts["kept"]]
            for m in verdicts["rejected"]:
                items.append({"name": m.get("name", ""), "url": m.get("url", ""),
                              "state": "rejected", "reason": m["reason"],
                              "result": ""})
            for m in (disc.get("rejected") or []):
                items.append({"name": m.get("name", ""), "url": m.get("url", ""),
                              "state": "rejected", "reason": m["reason"],
                              "result": ""})
            return {"disc": disc, "models": models, "items": items,
                    "rejected_total": (len(verdicts["rejected"])
                                       + (disc.get("rejected_total") or 0))}

        def _absorb(found: dict) -> None:
            disc = found["disc"]
            AUTO["licence"] = AUTO.get("licence") or disc.get("licence") or {}
            AUTO["note"] = disc.get("note") or AUTO.get("note") or ""
            # Each discovery pass re-reads the same listings, so a model skipped
            # in one pass is skipped again in the next. Keep the largest pass's
            # count and the unique names, rather than adding passes together.
            AUTO["skipped_already"] = max(AUTO.get("skipped_already") or 0,
                                           disc.get("skipped_done") or 0)
            AUTO["already_done"] = list(dict.fromkeys(
                (AUTO.get("already_done") or []) + (disc.get("already_done") or [])))[:12]
            AUTO["discovered"] = ((AUTO.get("discovered") or 0)
                                  + len(found["models"]))
            AUTO["rejected_total"] = max(AUTO.get("rejected_total") or 0,
                                          found["rejected_total"])
            # a listing re-read by a later pass is already a row: not added twice
            seen = {it.get("url") for it in AUTO.setdefault("items", []) if it.get("url")}
            for it in found["items"]:
                if it.get("url") and it.get("url") in seen:
                    continue
                seen.add(it.get("url"))
                AUTO["items"].append(it)

        def _published_count() -> int:
            return sum(1 for it in AUTO.get("items") or []
                       if it.get("state") == "published")

        def _attempted() -> set:
            return {it.get("url") for it in AUTO.get("items") or []
                    if it.get("url")}

        with timing.phase("discovery"):
            first = await asyncio.to_thread(_discover, count, set())
        _absorb(first)
        disc = first["disc"]
        if AUTO.get("stop") or disc.get("stopped"):
            # Stop was pressed while the listings were being read: nothing has
            # been fetched or published, so the run ends here as stopped.
            AUTO["state"] = "stopped"
            AUTO["finished"] = _time.strftime("%Y-%m-%d %H:%M:%S")
            AUTO["log_path"] = _log()
            return
        AUTO["state"] = "running"
        AUTO["log_path"] = _log()      # the queue is on disk from the start

        idx = 0
        topups = 0
        max_topups = 4
        while True:
            if idx >= len(AUTO["items"]):
                # The queue is used up. Anything that did not publish (refused,
                # duplicate, failed, fetch failed) is replaced by a NEW candidate,
                # never a repeat, until `count` models are on the account or the
                # listings run dry.
                need = count - _published_count()
                if need <= 0 or AUTO.get("stop") or topups >= max_topups:
                    break
                topups += 1
                AUTO["state"] = "discovering"
                with timing.phase("discovery"):
                    more = await asyncio.to_thread(_discover, need, _attempted())
                _absorb(more)
                AUTO["log_path"] = _log()
                if more["disc"].get("stopped") or AUTO.get("stop"):
                    AUTO["state"] = "stopped"
                    break
                if not more["models"]:
                    break
                AUTO["state"] = "running"
                continue
            it = AUTO["items"][idx]
            idx += 1
            # keep the log current after every model, so a run that is stopped or
            # killed mid-way still leaves what it did on disk
            AUTO["log_path"] = _log()
            if it.get("state") == "rejected":
                continue                   # never fetched, never published
            if AUTO.get("stop"):
                AUTO["state"] = "stopped"
                break
            # Already published? Judged by the card's own name and link BEFORE
            # the model's page is opened at all.
            hit0 = ledger.find(source_url=it["url"], name=it["name"])
            if hit0["found"]:
                it["state"] = "duplicate"
                it["reason"] = ledger.describe(hit0)
                continue
            it["state"] = "fetching"
            # A fetch that died on a navigation or a temporary network drop is
            # retried a few times before this candidate is written off.
            res, fetch_err, fetch_tries, stopped = await _fetch_with_retries(it)
            if stopped:
                # Stop was pressed while the run was backing off: the model was
                # never decided, so it is not reported as its failure at all.
                AUTO["state"] = "stopped"
                it["state"] = "queued"
                it["reason"] = "run stopped while retrying this model's fetch"
                break
            if res is None:
                # a FAILURE, not a skip: the counts and the run row say so
                it["state"] = FETCH_FAILED
                it["reason"] = ("could not fetch this model: " + fetch_err[:160]
                                + _fetch_attempts_note(fetch_tries))
                continue
            # Already published, as the page names it? Refused now, before the
            # second licence read and the photo downloads.
            hit1 = ledger.find(source_url=res.get("source_url") or it["url"],
                               name=res.get("name") or it["name"],
                               author=res.get("author", ""),
                               slug=res.get("slug", ""),
                               file_path=res.get("stl_path"))
            if hit1["found"]:
                it["state"] = "duplicate"
                it["reason"] = ledger.describe(hit1)
                # Learn this listing's link and both titles on the published
                # model, so the next run refuses it before opening the page.
                ledger.learn_listing(
                    source_url=it["url"],
                    names=[res.get("name") or "", it["name"]],
                    author=res.get("author", ""), slug=res.get("slug", ""),
                    file_path=res.get("stl_path") or "")
                continue
            lic = res.get("license") or {}
            # An UNREADABLE licence gets ONE clean retry before it is believed:
            # the fetch may have landed while the page was still a login/cookie
            # wall (2026-10-09: five run fetches read licence='' off a page the
            # model never rendered). A stale first fetch must not decide a run.
            if (not lic.get("evidence") and not lic.get("restricted")
                    and not AUTO.get("stop")):
                res2, err2, _t2, stop2 = await _fetch_with_retries(it)
                if stop2:
                    AUTO["state"] = "stopped"
                    it["state"] = "queued"
                    it["reason"] = "run stopped while re-reading this model"
                    break
                if res2 is not None:
                    res = res2
                    lic = res.get("license") or {}
                    it["reason"] = "re-fetched once: the first read saw no licence"
            it["licence"] = lic.get("raw", "")[:200]
            it["licence_evidence"] = lic.get("evidence") or []
            if AUTO.get("stop"):
                AUTO["state"] = "stopped"
                it["state"] = "queued"      # decided nothing: not left as 'fetching'
                it["reason"] = "run stopped after this model was read"
                break
            # ND / Creality Standard / NonCommercial / unreadable licence, and
            # the not-eligible content classes and the source authorisation —
            # judged by the ONE owner (creality_rules) as soon as the page has
            # been read, so a refused model is skipped for its own reason and
            # its file is never even looked at. The proof of creation is added
            # to the same judgement below, once the model file is in hand.
            # A licence that could not be read at all is skipped too, and SAID
            # to be unread — never reported as "not licensed" (that false claim
            # is what a licensed model was being refused with).
            gate = creality_rules.check(name=res.get("name") or it["name"],
                                        source_url=res.get("source_url") or it["url"],
                                        licence=lic, require_proof=False)
            it["policy"] = {"rule": gate["rule"], "allowed": gate["allowed"],
                            "checks": gate["checks"]}
            if not gate["allowed"]:
                it["state"] = "skipped"
                it["reason"] = gate["reason"]
                continue
            if not res.get("stl_path"):
                it["state"] = "skipped"
                it["reason"] = "no usable model file came back from the page"
                continue
            # The proof of creation, built from the file being uploaded, and the
            # photos the listing will carry (the creator's originals, or our own
            # renders when the switch says so) — both from one place.
            with timing.phase("proof"):
                proof_info = _build_proof(res.get("stl_path"),
                                          res.get("name") or it["name"],
                                          res.get("slug") or "")
            gate = creality_rules.check(name=res.get("name") or it["name"],
                                        source_url=res.get("source_url") or it["url"],
                                        licence=lic, proof=proof_info)
            it["policy"] = {"rule": gate["rule"], "allowed": gate["allowed"],
                            "checks": gate["checks"]}
            it["proof"] = {"ok": bool(proof_info.get("ok")),
                           "sha256": proof_info.get("sha256", ""),
                           "why": proof_info.get("why") or ""}
            if not gate["allowed"]:
                it["state"] = "skipped"
                it["reason"] = gate["reason"]
                continue
            with timing.phase("images"):
                upload_images, photo_note, cover_ok = _upload_images(
                    res.get("image_paths") or [], proof_info,
                    res.get("stl_path") or "", res.get("name") or it["name"],
                    res.get("slug") or "")
            it["photo_note"] = photo_note
            # Already published — never twice. The ledger is consulted BEFORE
            # the form is touched, so a repeat costs nothing (no browser, no
            # submit, no chance of a double listing).
            hit = ledger.find(source_url=res.get("source_url") or it["url"],
                              name=res.get("name") or it["name"],
                              author=res.get("author", ""),
                              slug=res.get("slug", ""),
                              file_path=res.get("stl_path"))
            if hit["found"]:
                it["state"] = "duplicate"
                it["reason"] = ledger.describe(hit)
                continue
            # The publish step shows THIS model's cover photos; a fetch that
            # brought no usable creator photo cannot be published at all. Renders
            # of the model are never uploaded (see _upload_images), so this is a
            # real dead end, reported here instead of walking to the form.
            if not cover_ok:
                it["state"] = FETCH_FAILED
                it["reason"] = ("could not fetch this model's cover photos: "
                                + NO_COVERS_ERROR)
                continue
            if AUTO.get("stop"):
                # Stop was pressed while this model was fetched: nothing has been
                # submitted yet, so the model is left undecided, not published.
                AUTO["state"] = "stopped"
                it["state"] = "queued"
                it["reason"] = "run stopped before this model was published"
                break
            it["state"] = "uploading"
            description = res.get("description") or ""
            if it["url"] and it["url"] not in description:
                description = (description.rstrip() + "\n\nOriginal design: "
                               + it["url"]).strip()
            try:
                with timing.phase("publish"):
                    async with UPLOAD_LOCK:
                        up = await uploader_upload(res.get("name") or it["name"],
                                                   description,
                                                   res.get("stl_path"),
                                                   upload_images,
                                                   it["url"], dry_run=False,
                                                   license_raw=lic.get("raw", ""),
                                                   should_stop=_stop_requested)
            except Exception as e:
                it["state"] = "failed"
                it["reason"] = str(e)[:200]
                continue
            if up.get("stopped"):
                # Stop was pressed before Submit: nothing was submitted, so the
                # model is left undecided (not published, not recorded).
                AUTO["state"] = "stopped"
                it["state"] = "queued"
                it["reason"] = "run stopped before Submit — nothing was submitted"
                break
            # Keep the upload's own evidence — photo slots, consent, licence
            # answers and, when Submit failed, Creality's captured rejection —
            # in the run log, so a rejection is diagnosable after the fact.
            if up.get("verify"):
                _v = up["verify"]
                it["upload_verify"] = {k: _v[k] for k in (
                    "images", "consent", "copyright_agreed", "license_answers",
                    "remix_source_declared", "remix_registered_href",
                    "remix_suggestion_clicked", "remix_source_why",
                    "submit_rejection") if k in _v}
            if up.get("success"):
                it["state"] = "published"
                it["result"] = up.get("message") or up.get("url") or "published"
            elif up.get("duplicate"):
                it["state"] = "duplicate"
                it["reason"] = up.get("error") or "already on Creality Cloud"
            else:
                it["state"] = "failed"
                # 400 chars: a rejected submit must name Creality's own reason
                # (step + flagged field + response) in the run's own record
                it["reason"] = (up.get("error") or up.get("message")
                                or "upload did not complete")[:400]
            # Every model this run got onto the account is remembered for good,
            # including the ones Creality itself flagged as duplicates — the
            # next run skips them without touching the form at all.
            really_published = (up.get("success") and not up.get("dry_run")
                                and not up.get("no_submit"))
            if really_published or up.get("duplicate"):
                _record_published(name=res.get("name") or it["name"],
                                  source_url=res.get("source_url") or it["url"],
                                  author=res.get("author", ""),
                                  slug=res.get("slug", ""),
                                  stl_path=res.get("stl_path") or "",
                                  published_url=up.get("url") or "",
                                  urls=up.get("urls"),
                                  outcome="published" if really_published
                                          else "creality-duplicate",
                                  note="run " + job_id)
        if AUTO.get("state") != "stopped":
            AUTO["state"] = "done"
        AUTO["finished"] = _time.strftime("%Y-%m-%d %H:%M:%S")
        AUTO["log_path"] = _log()
    except Exception as e:
        AUTO["state"] = "error"
        for it in AUTO.get("items") or []:
            if it.get("state") in ("fetching", "uploading"):
                it["state"] = "failed"     # the run died on it: never left 'working'
                it["reason"] = "run error: " + str(e)[:160]
        AUTO["note"] = (AUTO.get("note") or "") + " | run failed: " + str(e)[:200]
        try:
            AUTO["log_path"] = _log()
        except Exception:
            pass
    finally:
        # The run's own timing table, recorded even when the run FAILED: a phase
        # that fails slowly is exactly the one worth seeing.
        timing.record("run", _time.perf_counter() - _run_t0)
        AUTO["timings"] = timing.snapshot()
        try:
            _log()          # re-write the log so the timings are in the file
        except Exception:
            pass


@app.post("/auto/start")
async def auto_start(req: AutoRequest):
    """Find N models with the licence filter, then publish each one."""
    global AUTO_TASK
    if AUTO.get("state") in ("discovering", "running"):
        raise HTTPException(status_code=409, detail=
            "An auto run is already going (%s). Stop it first or wait for it to finish."
            % AUTO["state"])
    count = int(req.count or 0)
    if count < 1:
        raise HTTPException(status_code=400, detail=
            "Type how many models you want, 1 or more (you sent %s)." % req.count)
    keywords = [k for k in (req.keywords or []) if str(k).strip()] or list(AUTO_DEFAULT_KEYWORDS)
    import time as _time
    job_id = _time.strftime("%Y%m%d-%H%M%S")
    AUTO.update({"state": "discovering", "job_id": job_id, "requested": count,
                 "discovered": 0, "items": [], "licence": {}, "keywords": keywords,
                 "note": "", "started": _time.strftime("%Y-%m-%d %H:%M:%S"),
                 "finished": None, "stop": False, "log_path": None,
                 "skipped_already": 0, "already_done": [], "rejected_total": 0,
                 "progress": None})
    AUTO_TASK = asyncio.create_task(_auto_worker(count, keywords, job_id))
    return {"ok": True, "job_id": job_id, "requested": count, "keywords": keywords,
            "licence_allow": CREALITY_LICENCE_ALLOW,
            "licence_deny": CREALITY_LICENCE_DENY}


@app.post("/auto/stop")
async def auto_stop():
    AUTO["stop"] = True
    return {"ok": True, "state": AUTO.get("state")}


@app.get("/auto/status")
async def auto_status():
    out = dict(AUTO)
    out["counts"] = _auto_counts()
    out["licence_allow"] = CREALITY_LICENCE_ALLOW
    out["licence_deny"] = CREALITY_LICENCE_DENY
    out["already_published"] = ledger.count()
    out["selector"] = selector.policy()
    out["timings"] = timing.snapshot()
    return out


# ── Diagnostics: the dashboard checking itself ──────────────────────────────
@app.get("/diagnostics")
async def diagnostics():
    """The dashboard's own self-test, using the SAME code the tests use.

    Fast and side-effect free: it starts no browser and reads no account, so it
    is safe to run whenever. Every failing check carries the exact command that
    fixes it -- the whole point after seeing ERR_CONNECTION_REFUSED with no
    explanation. The one check that DOES start a browser is behind
    /diagnostics/browser, because that one costs a real browser launch.
    """
    import dashboard_guard as guard

    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail,
                       "fix": fix})

    # NOT a self-probe. Asking this server to fetch its own URL from inside its
    # own handler cannot be answered -- the request is holding the loop that would
    # serve it -- so it always reported "nothing answered on port 8000" on a
    # perfectly healthy server. Running this handler at all IS the proof, and the
    # port owner below is the independent check.
    holder = guard.port_holder(SERVER_PORT)
    detail = guard.port_holder_detail(holder)
    add("server is answering", True,
        "this reply came from port %d, PID %s — that is the proof"
        % (SERVER_PORT, os.getpid()),
        "start it: .venv/Scripts/python.exe scripts/dashboard_supervisor.py")
    add("port owner", bool(holder) and detail.get("ours", False),
        ("PID %s (%s)" % (holder, detail.get("image") or "unknown")) if holder
        else "nobody is listening",
        "if a stale copy holds it: scripts/dashboard_supervisor.py --restart")

    head = _git("rev-parse", "--short", "HEAD")
    add("running the code on disk", (not head) or head == BUILD.split("+")[0],
        "running %s, disk is %s, up %ss" % (BUILD, head or "unknown",
                                            round(time.time() - STARTED_AT)),
        "code changed since this process started: scripts/dashboard_supervisor.py --restart")

    try:
        n = ledger.count()
        add("ledger readable", True, "%d model(s) already published" % n)
    except Exception as e:
        add("ledger readable", False, str(e)[:160],
            "the ledger is what stops a double publish; check artifacts/")

    try:
        import discover as _disc
        tried = _disc._tried_store(_disc.TRIED_PATH)
        corrupt = _disc.TRIED_PATH.with_name(_disc.TRIED_PATH.stem + ".corrupt.json")
        add("tried-listings record readable", True,
            "%d listing(s) remembered" % len(tried)
            + ("; a corrupt copy is kept as %s" % corrupt.name if corrupt.exists() else ""),
            "a corrupt record is moved aside, never overwritten -- see discover.py")
    except Exception as e:
        add("tried-listings record readable", False, str(e)[:160],
            "without it, Find Models reopens pages a run already refused")

    art = BASE_DIR / "artifacts"
    try:
        art.mkdir(parents=True, exist_ok=True)
        probe = art / ".diagnostics-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("artifacts writable", True, str(art))
    except Exception as e:
        add("artifacts writable", False, str(e)[:160],
            "run logs and the run history live here")

    try:
        pol = selector.policy()
        add("harvest policy loads", bool(pol.get("families")),
            "%d families, %d reject rules" % (len(pol.get("families") or []),
                                              len(pol.get("rejects") or [])))
    except Exception as e:
        add("harvest policy loads", False, str(e)[:160])

    try:
        rules = creality_rules.policy()
        add("Creality's rules load", bool(rules.get("licence_allow")),
            "allow: %s" % ", ".join(rules.get("licence_allow") or []))
    except Exception as e:
        add("Creality's rules load", False, str(e)[:160])

    cdp = await asyncio.to_thread(_debug_port_up)   # blocking probe: keep it off the loop
    add("Chrome debug port (publishing)", True,
        "up on 9222" if cdp else
        "not up -- it starts by itself when you publish or fetch",
        "" if cdp else "publishing will launch Chrome automatically")

    autostart = _autostart_entry()
    add("starts with Windows", autostart is not None,
        str(autostart) if autostart else "no Startup entry -- a reboot leaves it down",
        "install it: powershell -File scripts/install_dashboard_autostart.ps1")

    # The step checks themselves, proven here on a fixture: the same assertions
    # the tests cover, so "the dashboard always gets it right" is checked in the
    # dashboard and not only in pytest.
    try:
        faults = _step_check_self_test()
        add("step checks catch faults", not faults,
            "all 3 injected faults were caught" if not faults
            else "NOT caught: %s" % "; ".join(faults),
            "verify.py is the one owner; a check that cannot fail is not a check")
    except Exception as e:
        add("step checks catch faults", False, str(e)[:160])

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "build": BUILD, "pid": os.getpid(),
            "checks": checks, "summary": timing.summary_line(),
            "timings": timing.process_snapshot()}


def _autostart_entry():
    """Path of the per-user Startup entry, or None when it is not installed."""
    base = os.environ.get("APPDATA")
    if not base:
        return None
    entry = (Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
             / "Startup" / "Creality-Dashboard.vbs")
    return entry if entry.exists() else None


def _step_check_self_test() -> list:
    """Run the step checks against deliberately WRONG pages, and report any that
    failed to notice. A check that cannot fail proves nothing.

    Pure and offline: it builds the pages in memory, so it never touches the site.
    """
    import page_model as _pm
    import verify as _verify

    def page(controls=(), images=()):
        return _pm.normalize_report({"url": "fixture", "controls": list(controls),
                                     "images": list(images), "texts": []})

    def field(**kw):
        base = {"tag": "input", "type": "text", "id": "", "name": "", "cls": "",
                "label": "", "value": "", "checked": None, "disabled": False,
                "required": False, "text": "", "box": [1, 1, 5, 5], "visible": True}
        base.update(kw)
        return base

    faults = []
    stuck = page(controls=[field(label="model name", visible=False, box=None)])
    if _verify.step_two_open(stuck).ok:
        faults.append("a form stuck on step 1 was accepted")
    wrong_name = page(controls=[field(label="model name", value="something else")])
    if _verify.name_applied(wrong_name, "the name we set").ok:
        faults.append("a name that was never applied was accepted")
    unticked = page(controls=[field(type="checkbox", label="CC BY-SA",
                                    checked=False)])
    if _verify.licence_rows_ticked(unticked, ("CC BY-SA",)).ok:
        faults.append("an unticked licence row was accepted")
    return faults


@app.get("/diagnostics/browser")
async def diagnostics_browser():
    """The ONE check that starts a browser, so it runs only when asked.

    Headless and throwaway: it proves Playwright can launch Chromium at all. It
    opens no page and touches no account, but it does cost a real browser start,
    which is why it is not part of /diagnostics.
    """
    ok = await asyncio.to_thread(_chromium_available)
    return {"ok": ok, "check": "Playwright can launch Chromium",
            "detail": "launched and closed a headless browser" if ok
                      else "could not launch -- playwright install may be needed",
            "fix": "" if ok else ".venv/Scripts/python.exe -m playwright install chromium"}


@app.get("/timings")
async def timings():
    """Where the time goes: this run's table plus everything since the server
    started. Read-only and cheap — it starts nothing and touches no account."""
    return {"run": timing.snapshot(), "process": timing.process_snapshot(),
            "summary": timing.summary_line()}


@app.get("/selector")
async def selector_policy():
    """What the machine is allowed to publish on its own: the useful household
    families it harvests, the rubbish rules it refuses, and the default searches.
    Defined once in selector.py and read by the dashboard, so there is one place
    to change what "useful household item" means."""
    return selector.policy()


# ── Already published: the pipeline's own memory ────────────────────────────
# Nothing in here is ever uploaded again — not in a run, not by hand.

@app.get("/published")
async def published_list():
    """Everything the pipeline knows it has already put on your account."""
    return ledger.summary()


@app.post("/published/sync")
async def published_sync():
    """Re-learn what is already on the account: read your Creality model list
    (the browser session is the API) and every run log on disk."""
    try:
        r = await analytics.refresh()
        note = "" if r.get("ok") else str(r.get("error") or "")[:200]
    except Exception as e:                      # the disk sources still apply
        note = str(e)[:200]
    added_acc = ledger.seed_from_account()
    added_runs = ledger.seed_from_runs()
    out = ledger.summary()
    out.update({"ok": True, "added_from_account": added_acc,
                "added_from_runs": added_runs, "note": note})
    return out


@app.post("/published/forget")
async def published_forget(req: ForgetRequest):
    """Deliberately allow one model to be published again (you deleted the
    listing). Everything else stays blocked."""
    dropped = ledger.forget(req.match)
    return {"ok": True, "dropped": dropped, "count": ledger.count()}


@app.get("/analytics/summary")
async def analytics_summary():
    """Current totals + per-period deltas (today / 7d / 30d / YTD), computed
    from the snapshot store — never touches the network. Includes the
    account-level creator-points balance and its deltas."""
    snaps = analytics.load_snapshots()
    out = analytics.period_stats(snaps)
    out["points"] = analytics.points_stats()
    return out


@app.get("/analytics/series")
async def analytics_series(days: int = 30):
    """Step-interpolated cumulative downloads/likes for charts."""
    days = max(1, min(days, 365))
    return analytics.chart_series(analytics.load_snapshots(), days)


@app.post("/analytics/refresh")
async def analytics_refresh():
    """Scrape the user's Creality models via the logged-in Chrome session and
    append a snapshot (the logged-in browser is the API — no keys, no cloud).
    Per-model DOWNLOAD counts come from detail pages, so they are fetched too."""
    r = await analytics.refresh()
    if r.get("ok"):
        try:
            await analytics.fetch_download_counts()
        except Exception:
            pass          # downloads are best-effort; likes/views already landed
    return r


@app.post("/analytics/signin")
async def analytics_signin():
    """Open the OFFICIAL Creality sign-in page in the debug Chrome. The session
    persists in the profile, so uploads + analytics keep working from it."""
    return await analytics.open_signin()


@app.post("/analytics/fetch-downloads")
async def analytics_fetch_downloads():
    """Read the real per-model DOWNLOAD counts (model-detail pages only expose
    them; profile cards show views/likes). One batch per call; cached on disk."""
    return await analytics.fetch_download_counts()


@app.get("/analytics/status")
async def analytics_status():
    """Login state (from the debug Chrome's session) + auto-refresh health."""
    status = await analytics.login_status()
    status.update(analytics.auto_status())
    return status


@app.get("/file/{filename}")
async def serve_file(filename: str):
    # Security: only serve files from the downloads directory. Resolve the path
    # and verify it stays inside DOWNLOADS_DIR (blocks ../ traversal even if a
    # client sneaks encoded separators past the route pattern).
    safe_path = (DOWNLOADS_DIR / Path(filename).name).resolve()
    if DOWNLOADS_DIR.resolve() not in safe_path.parents or not safe_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(safe_path))


@app.get("/health")
async def health():
    """Fast self-check. It must never start anything.

    This used to launch a headless Chromium through asyncio.to_thread just to
    answer, so every poll of a health endpoint spawned a browser: slow for the
    supervisor and for the dashboard. The browser check now runs only when you
    explicitly ask for it, at GET /diagnostics/browser.
    """
    checks = {
        "image_editor": "local (no API key needed)",
        # A blocking probe (2s timeout), so it must not run on the event loop:
        # a health check that freezes every other request is not a health check.
        "browser_debug_port_up": await asyncio.to_thread(_debug_port_up),
        "downloads_dir": str(DOWNLOADS_DIR),
        "playwright_importable": _playwright_importable(),
        "browser_probe": "not run here — use the Diagnostics panel's browser check",
    }
    head = _git("rev-parse", "--short", "HEAD")
    return {"status": "ok", "build": BUILD, "pid": os.getpid(),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(STARTED_AT)),
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "port": SERVER_PORT,
            # Is the code ON DISK what this process is running? A server keeps
            # serving the code it started with, which is why a "restart" could
            # look like it did nothing. This makes that answerable.
            "head_rev": head,
            "build_is_current": bool(head) and head == BUILD.split("+")[0],
            "checks": checks}


def _playwright_importable() -> bool:
    """Is the library present? Cheap, and it starts nothing.

    find_spec only asks the import system -- it does not import the package, and
    it starts no driver, which is the whole requirement for a fast health check.
    """
    import importlib.util
    try:
        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


def _chromium_available() -> bool:
    try:
        from browser_opener import sync_playwright_auto
        with sync_playwright_auto() as pw:
            b = pw.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


def _port_holder_pid(port: int) -> int | None:
    """PID listening on `port`, or None. Reuses the supervisor's own parser, so
    "who holds the port" has exactly one implementation in this project."""
    try:
        import dashboard_guard
        return dashboard_guard.port_holder(port)
    except Exception:
        return None


if __name__ == "__main__":
    import uvicorn

    # Fail loudly and usefully instead of dying invisibly on "address already in
    # use". That silent death is what produced ERR_CONNECTION_REFUSED while an old
    # copy kept serving old code, so a restart could look like it did nothing.
    holder = _port_holder_pid(SERVER_PORT)
    if holder:
        detail = {}
        try:
            import dashboard_guard
            detail = dashboard_guard.port_holder_detail(holder)
        except Exception:
            pass
        print("=" * 68)
        print("  Port %d is already in use by PID %s (%s)."
              % (SERVER_PORT, holder, detail.get("image") or "unknown"))
        print("  If that is the dashboard, it is already up: http://localhost:%d"
              % SERVER_PORT)
        print("  If it is a stale copy, it is serving OLD code. Replace it with:")
        print("      .venv/Scripts/python.exe scripts/dashboard_supervisor.py --restart")
        print("  Nothing was started, so no second half-broken copy exists.")
        print("=" * 68)
        raise SystemExit(2)
    print("=" * 68)
    print("  Creality Pipeline dashboard:  http://localhost:%d" % SERVER_PORT)
    print("  Health check:                 http://localhost:%d/health" % SERVER_PORT)
    print("  This code:                    %s" % BUILD)
    print("=" * 68)
    uvicorn.run("main:app", host="127.0.0.1", port=SERVER_PORT, log_level="info")
