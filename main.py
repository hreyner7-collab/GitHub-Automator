import asyncio
import base64
import faulthandler
import json
import os
import re
from pathlib import Path
import subprocess
import threading
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from downloader import fetch as dl_fetch, classify_license
from editor import edit_all, check_options as photo_style_check
from uploader import (upload as uploader_upload, _debug_port_up,
                      license_answer_indexes, expected_badge_token,
                      usable_cover_images)
from discover import discover as discover_models, find_creality, \
    CREALITY_LICENCE_ALLOW, CREALITY_LICENCE_DENY, listing_was_refused, \
    listing_was_used, remember_tried, was_tried_recently, \
    MAKERWORLD_LICENSES, \
    DISCOVER_DEADLINE_S as AUTO_DISCOVER_DEADLINE_S
import analytics
import creality_rules
import makerworld_flow
import makerworld_ledger
import ledger
import proof
import photo_gen
import selector
import timing
import uploader        # the module, for the evidence dict a raised publish leaves
from makerworld_uploader import ensure_browser as makerworld_ensure_browser
from makerworld_uploader import upload as makerworld_upload
from origin import library as originals_lib
from origin import publish as originals_publish

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


def _code_fingerprint() -> str:
    """A fingerprint of the SOURCE this process is running: the pipeline modules
    and the dashboard page, by mtime and size.

    Freshness used to be judged on the git revision alone, so a dashboard started
    from an uncommitted tree — the normal state while the pipeline is being
    repaired — reported "running the code on disk" while serving code hours old,
    and the supervisor's auto-reset never fired for the edits sitting on disk.
    The revision says WHICH COMMIT; this says whether those files have changed
    since the process read them.
    """
    import hashlib
    root = Path(__file__).resolve().parent
    parts = []
    for f in (sorted(root.glob("*.py"))
              + sorted((root / "origin").glob("*.py"))
              + [root / "creality" / "index.html", root / "makerworld" / "index.html"]):
        try:
            st = f.stat()
            parts.append("%s:%d:%d" % (f.name, st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append("%s:missing" % f.name)
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


CODE_FINGERPRINT = _code_fingerprint()

SERVER_PORT = 8000

# ── Why did it stop answering? ───────────────────────────────────────────────
# A run drives a real browser from a worker thread, and twice now the dashboard
# has gone silent mid-run: the supervisor's probe timed out, it restarted the
# server, and the models that were queued died with it (2026-10-24, a 20-model
# publish stopped 90 seconds in with 1 model reached). Silence like that used to
# leave NO evidence — the process was alive, so nothing crashed, and the log
# simply stopped. This is the instrument that answers what the next one is doing:
# the loop keeps a beat of its own, and a plain thread dumps every stack to the
# server log when that beat stops.
LOOP_TICK: dict = {"at": 0.0, "dumped": 0.0}
STALL_DUMP_S = 25.0


async def _loop_beat() -> None:
    while True:
        LOOP_TICK["at"] = time.time()
        await asyncio.sleep(1.0)


def _stall_watch(stop: "threading.Event") -> None:
    while not stop.wait(5.0):
        now = time.time()
        gap = now - LOOP_TICK["at"]
        if LOOP_TICK["at"] and gap > STALL_DUMP_S and now - LOOP_TICK["dumped"] > 120.0:
            LOOP_TICK["dumped"] = now
            try:
                print("[stall] the event loop has not beaten for %.0fs — dumping "
                      "every thread" % gap, flush=True)
                faulthandler.dump_traceback(all_threads=True)
            except Exception:
                pass


_watchdog_started = False


def _start_stall_watchdog() -> None:
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    faulthandler.enable()          # a native death at least leaves a trace
    threading.Thread(target=_stall_watch, args=(threading.Event(),),
                     daemon=True, name="stall-watchdog").start()


@app.on_event("startup")
async def _start_analytics_auto():
    pass  # no hourly browser reads: the account is read only when the user clicks Refresh
    # Learn what is already published before the first upload of the session:
    # the account's own model list + every run log on disk. From here on the
    # pipeline refuses to publish any of those again. The account read is a
    # background task (it drives the browser), so booting never waits on it.
    _start_stall_watchdog()
    asyncio.create_task(_loop_beat())
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


class MakerWorldUploadRequest(BaseModel):
    """The same listing, aimed at MakerWorld instead of Creality. print_photos
    is its own field because MakerWorld asks for something the edited set is
    not: photographs of the object you actually printed. photo_background and
    photo_recolour are the only edits those photographs may get, and both are
    opt-in — with neither set the files go to the form exactly as shot.

    photo_mode decides where the pictures come from at all: "same_model" (what
    both buttons do — the source listing's own photograph of the model, with only
    its background rebuilt here), "generate" draws a whole new set from the
    listing's own words and sends no original image, "as_shot" uses the ticked
    photographs untouched and "restyle" runs them through the local finisher.
    This field's own default stays "generate" — a caller that names no mode must
    land on the one mode that cannot attach somebody else's picture — while the
    page's default option and the auto run both name same_model explicitly.
    The page sends its own answer for the mode in use: same_model is answered by
    `assert_source`, generate by `assert_ai` and the own-photo modes by
    `assert_prints`, and each is checked here before anything is made or sent."""
    name: str
    description: str = ""
    stl_path: str
    image_paths: list[str] = []
    print_photos: list[str] = []
    source_photos: list[str] = []
    photo_mode: str = "generate"
    photo_count: int = 3
    # The acknowledgements are on the page as ticks, and the page is not the only
    # caller of this route: each is checked here too, so "without this tick
    # nothing is sent" is a fact about the pipeline rather than about a browser.
    assert_ai: bool = False
    assert_prints: bool = False
    assert_source: bool = False
    photo_background: str = ""
    photo_recolour: str = ""
    source_url: str = ""
    license_raw: str = ""
    remix_source_url: str = ""
    tags: list[str] = []
    category: str = ""
    kind: str = "remix"
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
    # Only the MakerWorld run reads this: its pictures are the source listing's own
    # photographs of the model with the background rebuilt, so starting it
    # unattended needs the same acknowledgement the single publish needs. The
    # Creality run ignores it.
    assert_source: bool = False


class OriginalsRequest(BaseModel):
    """How many original models this run should have ready for the owner."""
    count: int = 5


class OriginalPublishRequest(BaseModel):
    """The owner's yes for ONE finished original, by its folder key.

    The click that sends this is the explicit per-model approval: the endpoint
    records it against the exact STL, contact sheet and five photos on disk,
    then publishes that one model through the gated originals publisher.
    """
    key: str
    note: str = ""


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


def cover_failure_reason(g: dict) -> str:
    """Why a model had no cover photo, in the page's own terms.

    A bare "the listing showed 0 photo(s), 0 usable" could not be acted on: it
    did not say whether the model has no pictures at all or whether the reader
    failed to see the ones it has. The gallery block now reports both, so the
    run log names which of the two happened."""
    g = g or {}
    got = g.get("images") or 0
    kept = g.get("downloaded") or 0
    page_imgs = g.get("page_images")
    creator = g.get("creator_images")
    out = ("could not fetch this model's cover photos: %d photo(s) found, "
           "%d usable" % (got, kept))
    if got == 0 and creator == 0:
        out += (" — the listing has no picture of the model at all (it shows %s "
                "image(s) on the page, none of them a creator photo), so "
                "nothing can be published for it"
                % (page_imgs if page_imgs is not None else "an unknown number of"))
    elif got == 0:
        out += (" — the page carries %s creator photo(s) that the gallery rules "
                "did not accept; found_by=%s"
                % (creator, ",".join(g.get("found_by") or []) or "nothing"))
    else:
        first = ((g.get("dropped") or [{}])[0]).get("reason", "")
        out += "; first one dropped: %s" % (first or "no reason recorded")
    return out + " — the run skipped it and moved on"


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


# ── MakerWorld: the same listing, the other platform ────────────────────────
# Deliberately its own route, its own driver (makerworld_uploader) and its own
# memory (makerworld_ledger). No Creality rule is applied here and none of this
# touches /upload.


PHOTO_COVERS = 2          # the form's separate 4:3 and 3:4 cover slots
OWN_PHOTO_MODES = ("as_shot", "restyle")
# "same_model" is what both buttons do now: the model as the source listing
# photographed it, with only the backdrop rebuilt locally. The auto run and the
# page's default option both name it, so it is one word here rather than two.
MW_PHOTO_MODE = "same_model"
ALL_PHOTO_MODES = (MW_PHOTO_MODE, "generate") + OWN_PHOTO_MODES


def _makerworld_photo_mode(mode: str = "") -> str:
    """One of the page's four modes, with anything unrecognised sent to generate.

    Failing closed matters: the other three attach files, so a typo must never
    be a way to put a picture nobody here made onto the listing. "generate" is
    the only mode that sends nothing it did not make itself.
    """
    m = (mode or "").strip()
    return m if m in ALL_PHOTO_MODES else "generate"


_GALLERY_NAME = re.compile(r"_img_\d+$")

# A gallery capture plus every picture the finisher wrote from one
# ({slug}_img_N, _edited_gray, _mask_gray, _repair_gray, and the recolour
# variants the finisher tags a second deep — {slug}_img_N_edited_gray_red). All
# of them are the source maker's photograph, so none may be passed off as the
# user's own photograph of their print — the picker refuses them by name and so
# do the own-photo routes. `_GALLERY_NAME` alone stays the test for what may go
# INTO the finisher, which must never see its own output again.
_SOURCE_FILE_NAME = re.compile(
    r"_img_\d+(?:_(?:edited|mask|repair)(?:_[a-z0-9]+)*)?$")

# What the AI studio and the drawer write into the same folder (photo_gen names
# its files `ai_{slug}_{time}_{seed}`): a machine drawing, never a photograph of
# a print. The own-photo routes refuse these by name too, because a listing
# posted with them in the print slot is the one rejection this pipeline was
# built to stop.
_GENERATED_FILE_NAME = re.compile(r"^ai_")


def _makerworld_gallery_files(paths) -> list[str]:
    """The scraped gallery's files and everything cut from them, by name.

    These are another maker's photographs. No mode may put one of them on the
    form as it stands: `same_model` cuts the model out of them and posts the
    cut, `generate` never looks at them, and the two own-photo modes are the
    photographs the user ticked — a gallery file named there is the leak this
    closes, because the route cannot tell "my print photo" from "the picture I
    scraped" by anything except the name this machine gave it.

    The name is the whole test, deliberately: an earlier version also required
    the file to sit in downloads/, and that turned out to be a way *round* the
    check rather than a tighter one — copy the scrape anywhere else on the disk
    and it sailed through as the user's own photograph.
    """
    out = []
    for p in makerworld_flow.usable_images(paths, must_exist=True):
        real = os.path.realpath(p)
        if _SOURCE_FILE_NAME.search(os.path.splitext(os.path.basename(real))[0]):
            out.append(real)
    return out


def _makerworld_own_files(paths) -> list[str]:
    """The files the own-photo modes may use, in the order given, without
    repeats: really on disk, really an image, and neither a gallery capture
    from another listing, a cut taken from one, nor a machine drawing.

    Deduplicated here because three ticks on the same photograph is one view,
    and MakerWorld's "covering 3 views" is the requirement the count is being
    read against.
    """
    gallery = set(_makerworld_gallery_files(paths))
    out, seen = [], set()
    for p in makerworld_flow.usable_images(paths, must_exist=True):
        real = os.path.realpath(p)
        base = os.path.basename(real)
        if real in gallery or real in seen:
            continue
        if _GENERATED_FILE_NAME.search(base):
            continue
        seen.add(real)
        out.append(real)
    return out


def _makerworld_source_files(paths) -> list[str]:
    """The source listing's own gallery files, and only those.

    The caller hands over what the listing was loaded with; this keeps the files
    that are really on disk, really images, and really named the way this
    machine's downloader names a listing gallery ({slug}_img_N). So a path from
    elsewhere on the disk cannot be passed off as "the source's picture", and an
    already-cut `_edited` file cannot be run through the finisher twice. Which
    listing a set belongs to is still the caller's answer, backed by the tick —
    but one listing's gallery is all it can be: files from two different
    listings' galleries are refused, because a spliced set would post a model
    that is not the one the source URL names.
    """
    root = str(DOWNLOADS_DIR.resolve()) + os.sep
    kept: dict[str, list[str]] = {}
    for p in makerworld_flow.usable_images(paths, must_exist=True):
        real = os.path.realpath(p)
        if not real.startswith(root):
            continue
        stem = os.path.splitext(os.path.basename(real))[0]
        if not _GALLERY_NAME.search(stem):
            continue
        kept.setdefault(stem.rsplit("_img_", 1)[0], []).append(real)
    if len(kept) > 1:
        raise ValueError(
            "the pictures named for this listing come from %d different source "
            "galleries (%s) — a remix's pictures must all come from the one "
            "listing it declares. Reload the source in step 1, or publish with "
            "your own photographs. Nothing was published."
            % (len(kept), ", ".join(sorted(k[:40] for k in kept))))
    return next(iter(kept.values()), [])


def _makerworld_cutout_photos(source_paths, background: str = "") -> dict:
    """Cut the model out of the source's own pictures and rebuild the backdrop.
    Blocking (cv2 + the segmenter), so the routes call it in a thread.

    The set is split the same way a drawn set is, because the form's cover slots
    and its photo slot are separate controls and the same file in both is what
    reads back as "Please set the model cover".
    """
    cut = makerworld_flow.cut_out_photos(source_paths, background)
    covers, photos = makerworld_flow.split_photo_sets(cut["photos"])
    cut.update({"covers": covers, "print_photos": photos,
                "ok": len(covers) >= 1
                      and len(photos) >= makerworld_flow.MIN_REAL_PHOTOS})
    return cut


def _makerworld_photo_want(count: int = 0) -> int:
    """How many pictures the photo slot needs, leaving room for the covers
    inside MakerWorld's own maximum."""
    want = int(count or 0) or makerworld_flow.MIN_REAL_PHOTOS
    return max(makerworld_flow.MIN_REAL_PHOTOS,
               min(want, makerworld_flow.MAX_PHOTOS - PHOTO_COVERS))


def _makerworld_generated_photos(name: str, description: str = "", count: int = 0) -> dict:
    """Draw a whole photo set for one listing from its own words. Blocking (it
    is the network and the retries), so the routes call it in a thread.

    The form's cover slots and its "real photos of your print" slot are separate
    places, and feeding the same file to both is what once came back as "Please
    set the model cover", so two extra pictures are drawn for the covers and the
    sets are kept apart.

    It returns whatever came back and says how many that was. It cannot be asked
    to fill a short set with the source listing's photographs — that is the one
    thing this pipeline was refused for, so the caller decides what to do with
    `ok` being False."""
    want = _makerworld_photo_want(count)
    drawn = photo_gen.generate_photos(name=name, description=description,
                                      count=want + PHOTO_COVERS,
                                      out_dir=str(DOWNLOADS_DIR),
                                      min_photos=want + PHOTO_COVERS,
                                      budget_each=240.0)
    covers, photos = makerworld_flow.split_photo_sets(drawn["photos"])
    drawn.update({"covers": covers, "print_photos": photos, "asked": want,
                  "ok": (bool(drawn["ok"]) and len(covers) >= 1
                         and len(photos) >= makerworld_flow.MIN_REAL_PHOTOS)})
    return drawn


@app.post("/upload/makerworld/plan")
async def makerworld_plan(req: MakerWorldUploadRequest):
    """What MakerWorld's own form would say about this listing, before anything
    opens: measured limits, required slots, the licence plan. Browser-free, and
    it never calls the image endpoint — a plan is a read, not a cost."""
    src = req.remix_source_url or req.source_url
    mode = _makerworld_photo_mode(req.photo_mode)
    want = _makerworld_photo_want(req.photo_count)
    if mode == "restyle" and (req.photo_background or req.photo_recolour):
        try:
            photo_style_check(req.photo_background or "keep", req.photo_recolour)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if mode == MW_PHOTO_MODE and req.photo_background:
        try:
            style, _ = photo_style_check(req.photo_background, "")
            if style == "keep":
                raise ValueError(
                    "this mode cannot keep the background — that would post the source "
                    "listing's photograph unchanged, which is the one thing it exists to "
                    "avoid. Name a backdrop, or use \"the photographs I ticked\".")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    # The pictures this mode will post are decided here exactly the way the
    # publish route decides them — which files survive the name tests, how the
    # set splits between the cover slots and the photo slot — because a plan
    # that answered a different question from the publish would be worse than no
    # plan. None of it opens a browser and none of it calls the image endpoint.
    cuttable = []
    plan_covers = list(req.image_paths)
    plan_photos = list(req.print_photos)
    if mode == MW_PHOTO_MODE:
        try:
            cuttable = _makerworld_source_files(req.source_photos)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif mode in OWN_PHOTO_MODES:
        shots = _makerworld_own_files(req.print_photos)
        covers = _makerworld_own_files(req.image_paths)[:PHOTO_COVERS]
        shots = [p for p in shots if p not in set(covers)]
        shots = shots[:makerworld_flow.MAX_PHOTOS - len(covers)]
        if covers:
            plan_covers, plan_photos = covers, shots
        elif shots:
            # Same rule as the publish: one of the ticked prints becomes the cover.
            plan_covers, plan_photos = makerworld_flow.split_photo_sets(shots, 1)
        else:
            plan_covers, plan_photos = [], []
    out = makerworld_flow.validate_plan(
        name=req.name, description=req.description, source_url=req.source_url,
        model_file=req.stl_path, cover_images=plan_covers,
        print_photos=plan_photos, remix_source_url=src,
        license_raw=req.license_raw, kind=req.kind, dry_run=req.dry_run,
        photos_drawn=((want + PHOTO_COVERS) if mode == "generate" else len(cuttable)))
    if mode == MW_PHOTO_MODE and len(cuttable) < makerworld_flow.MIN_REAL_PHOTOS + 1:
        out["blockers"].append(
            f"the source listing has only {len(cuttable)} of its own pictures in "
            f"downloads/, and the form needs {makerworld_flow.MIN_REAL_PHOTOS} for the "
            "photo slot plus one for the cover")
        out["ok"] = False
    out["photos"] = {
        "mode": mode,
        "will_generate": (want + PHOTO_COVERS) if mode == "generate" else 0,
        "will_cut": len(cuttable) if mode == MW_PHOTO_MODE else 0,
        "own_photos": len(plan_photos) if mode in OWN_PHOTO_MODES else 0,
        "minimum": makerworld_flow.MIN_REAL_PHOTOS,
        "prompt": (photo_gen.prompt(req.name, req.description)
                   if mode == "generate" else ""),
    }
    return out


@app.post("/upload/makerworld")
async def upload_to_makerworld(req: MakerWorldUploadRequest):
    src = req.remix_source_url or req.source_url
    # One browser, one form at a time — the same lock the Creality publish uses.
    # A MakerWorld run counts too: it would otherwise be drawing pictures at the
    # same time as this, and the free tier queues behind the first one.
    for other, label in ((AUTO, "A Creality"), (MW_AUTO, "A MakerWorld")):
        if other.get("state") in ("discovering", "running"):
            raise HTTPException(status_code=409, detail=
                f"{label} auto run is publishing right now ({other['state']}) — "
                "stop it first, or wait for it, then publish to MakerWorld by hand.")
    if not req.dry_run:
        hit = makerworld_ledger.find(source_url=req.source_url, name=req.name,
                                     published_url=src, file_path=req.stl_path)
        if hit["found"]:
            raise HTTPException(status_code=409, detail=
                "Refusing to publish twice on MakerWorld. " + makerworld_ledger.describe(hit))
    # The pictures are settled before the browser opens, because a publish that
    # discovers it has no images halfway through is the failure being removed.
    mode = _makerworld_photo_mode(req.photo_mode)
    # The ticks on the page, honoured here as well. The page is not the only
    # caller of this route, and "without this tick nothing is sent" has to be a
    # fact about the pipeline rather than about a browser.
    if mode == "generate" and not req.assert_ai:
        raise HTTPException(status_code=400, detail=
            "The pictures for this listing would be drawn by a machine, not photographed off "
            "the printer. MakerWorld can reject a listing for that, so nothing is sent until "
            "the tick that says you know that is on.")
    if mode == MW_PHOTO_MODE and not req.assert_source:
        raise HTTPException(status_code=400, detail=
            "The pictures for this listing are the source listing's own photographs of the "
            "model, with only their background rebuilt on this machine. They are not "
            "photographs of a print you made, and MakerWorld can reject a listing for that, so "
            "nothing is sent until the tick that says you know that is on.")
    if mode in OWN_PHOTO_MODES and not req.assert_prints:
        raise HTTPException(status_code=400, detail=
            "MakerWorld asks for photographs of the object you actually printed. Nothing is "
            "sent until the tick that says these are your own print photos is on.")
    drawn = None
    prep = None
    cut = None
    if mode == "generate":
        try:
            drawn = await asyncio.to_thread(_makerworld_generated_photos,
                                            req.name, req.description, req.photo_count)
        except Exception as exc:                         # noqa: BLE001
            raise HTTPException(status_code=502, detail=
                "The listing pictures could not be drawn (%s: %s), and publishing without "
                "them is not an option, so nothing was published."
                % (type(exc).__name__, str(exc)[:200]))
        if not drawn["ok"]:
            raise HTTPException(status_code=502, detail=
                f"Only {drawn['got']} of {drawn['asked'] + PHOTO_COVERS} pictures came back, and the "
                f"listing needs {makerworld_flow.MIN_REAL_PHOTOS} for the photo slot plus one "
                f"for the cover, so nothing was published. "
                + " | ".join(drawn["notes"][-3:]))
        cover_photos = drawn["covers"]
        print_photos = drawn["print_photos"]
    elif mode == MW_PHOTO_MODE:
        # The model exactly as the source listing photographed it, with the
        # backdrop rebuilt here and every result looked at before it is used.
        try:
            src_imgs = _makerworld_source_files(req.source_photos)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        need = makerworld_flow.MIN_REAL_PHOTOS + 1
        if len(src_imgs) < need:
            raise HTTPException(status_code=409, detail=
                f"The source listing has {len(src_imgs)} of its own picture(s) on this machine "
                f"and this mode needs {need} — {makerworld_flow.MIN_REAL_PHOTOS} for the photo "
                "slot plus one for the cover. Load the source in step 1 first, or switch the "
                "picture mode. Nothing was published.")
        # Every picture here costs a cut and a look, and the form's photo slot has
        # a measured ceiling, so a gallery bigger than that is not all cut.
        src_imgs = src_imgs[:makerworld_flow.MAX_PHOTOS]
        try:
            cut = await asyncio.to_thread(_makerworld_cutout_photos, src_imgs,
                                          req.photo_background)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not cut["ok"]:
            raise HTTPException(status_code=502, detail=
                f"Only {cut['got']} of {cut['asked']} source pictures came out looking right "
                f"with a new background, and the form needs {need}, so nothing was published — "
                f"a refused picture is never sent as the source's own file. "
                + " | ".join(cut["notes"][-3:]))
        cover_photos, print_photos = cut["covers"], cut["print_photos"]
    else:
        # The two own-photo modes post photographs the user ticked, and a file off
        # the scraped gallery is not one of them however the caller names it — so
        # both lists are filtered by name here, not only greyed out in the picker,
        # because curl and a stale tab are callers too. Restyling happens before
        # the browser opens and never in place: the finisher writes new files
        # beside the photographs, so the originals stay untouched, and `as_shot`
        # means exactly that — only `restyle` is allowed to change them.
        own_shots = _makerworld_own_files(req.print_photos)
        own_covers = _makerworld_own_files(req.image_paths)[:PHOTO_COVERS]
        # The two form slots are separate controls and one file in both is what
        # reads back as "Please set the model cover", so a photograph ticked on
        # both sides stays only on the cover side.
        own_shots = [p for p in own_shots if p not in set(own_covers)]
        # Measured ceiling on the whole submission, so a ticked gallery of 40
        # prints is cut to what the form can actually take rather than sent and
        # refused.
        own_shots = own_shots[:makerworld_flow.MAX_PHOTOS - len(own_covers)]
        if not own_shots and not own_covers:
            raise HTTPException(status_code=409, detail=
                f"None of the {len(req.print_photos)} photo file(s) named is one of your own "
                "photographs: this machine names the pictures it scraped off a listing "
                "slug_img_N and the pictures it drew ai_, and refuses both in the own-photo "
                "modes whatever slot they are named for. Tick your own prints, or use "
                "\"same model, new background\". Nothing was published.")
        background = req.photo_background if mode == "restyle" else ""
        recolour = req.photo_recolour if mode == "restyle" else ""
        try:
            prep = makerworld_flow.prepare_print_photos(
                own_shots, background, recolour)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        cover_photos = own_covers
        print_photos = prep["photos"]
        if not cover_photos and print_photos:
            # No cover was ticked. It comes from the user's own photographs, in a
            # slot of its own — never from the source listing's gallery, and never
            # the same file twice, which is what reads back as "Please set the
            # model cover".
            cover_photos, print_photos = makerworld_flow.split_photo_sets(print_photos, 1)
        if len(print_photos) < makerworld_flow.MIN_REAL_PHOTOS:
            raise HTTPException(status_code=409, detail=
                f"Only {len(print_photos)} of your own photographs would reach the photo slot "
                f"and MakerWorld wants at least {makerworld_flow.MIN_REAL_PHOTOS} covering 3 "
                "views — tick more of your own prints (one of them becomes the cover when no "
                "cover is ticked). Nothing was published.")
    try:
        async with UPLOAD_LOCK:
            result = await makerworld_upload(
                name=req.name, description=req.description, stl_path=req.stl_path,
                image_paths=cover_photos, source_url=req.source_url,
                dry_run=req.dry_run, license_raw=req.license_raw,
                print_photos=print_photos, remix_source_url=src,
                tags=req.tags, category=req.category, kind=req.kind)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if prep and prep["styled"] and isinstance(result, dict):
        result["photo_edits"] = {"background": prep["background"],
                                 "recolour": prep["recolour"],
                                 "notes": prep["notes"]}
    if drawn and isinstance(result, dict):
        result["photo_draw"] = {"got": drawn["got"], "asked": drawn["asked"] + PHOTO_COVERS,
                                "seconds": drawn["seconds"],
                                "covers": [os.path.basename(p) for p in drawn["covers"]],
                                "files": [os.path.basename(p)
                                          for p in drawn["print_photos"]],
                                "notes": drawn["notes"]}
    if cut and isinstance(result, dict):
        result["photo_cut"] = {
            "got": cut["got"], "asked": cut["asked"], "background": cut["background"],
            "covers": [os.path.basename(p) for p in cut["covers"]],
            "files": [os.path.basename(p) for p in cut["print_photos"]],
            "looked_at": [{"source": r["source"],
                           "looked_right": r["looked_right"],
                           "tries": [{"background": a["background"],
                                      "ok": a["ok"]} for a in r["attempts"]]}
                          for r in cut.get("reviews", [])],
            "notes": cut["notes"]}
    # Only what really landed. A dry run, a MAKERWORLD_NO_SUBMIT fill and a form
    # left waiting for a manual Publish press all put nothing on MakerWorld, so
    # none of them may lock the model.
    published_now = (result.get("success") and not result.get("error")
                     and not result.get("dry_run") and not result.get("no_submit"))
    # "This model already exists" only counts when it points at a design that is
    # not the draft the wizard is sitting on: MakerWorld's page text also carries
    # the description just typed, and a match there used to lock a model that was
    # never published, on a draft link, for good.
    dupe_of = ""
    if result.get("duplicate"):
        dupe_of = makerworld_flow.design_id_from_url(str(result.get("url") or ""))
        if dupe_of and dupe_of == str(result.get("draft_id") or ""):
            dupe_of = ""
    if published_now or dupe_of:
        makerworld_ledger.record(
            name=req.name, source_url=req.source_url, published_url=result.get("url") or "",
            design_id=str(result.get("design_id") or dupe_of or ""), file_path=req.stl_path,
            outcome="published",
            note="published by hand from the dashboard" if published_now
            else "MakerWorld said this model already exists")
    if isinstance(result, dict):
        result.setdefault("platform", "makerworld")
    return result


@app.get("/makerworld", response_class=HTMLResponse)
async def makerworld_dashboard():
    """The MakerWorld dashboard — its own page at its own address, its own
    wording, its own memory. The Creality dashboard at / does not mention this
    one and neither publishes through the other's rules."""
    return HTMLResponse((BASE_DIR / "makerworld" / "index.html").read_text(encoding="utf-8"))


@app.get("/makerworld/studio", response_class=HTMLResponse)
async def makerworld_studio():
    """The AI studio — a deliberately separate page, because it is the one place
    in this app that draws pixels that were never photographed. Everything here
    is Puter's service, called from the browser; nothing is published by it."""
    return HTMLResponse((BASE_DIR / "makerworld" / "ai_studio.html").read_text(encoding="utf-8"))


class AiSaveRequest(BaseModel):
    name: str = "photo.png"
    data_url: str = ""


AI_MAX_BYTES = 20 * 1024 * 1024


@app.post("/makerworld/ai/save")
async def makerworld_ai_save(req: AiSaveRequest):
    """Land a generated picture in downloads/ with an unmistakable ai_ prefix, so
    it can never be mistaken in the picker for a photograph of a real print."""
    m = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", (req.data_url or "").strip(),
                 re.S)
    if not m:
        raise HTTPException(status_code=400, detail=
            "expected a base64 image data url (data:image/png;base64,…)")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except ValueError as exc:                    # binascii.Error is a ValueError
        raise HTTPException(status_code=400, detail=
            f"that data url is not decodable base64 ({exc}) — nothing was written")
    if not raw:
        raise HTTPException(status_code=400, detail="the image came through empty")
    if len(raw) > AI_MAX_BYTES:
        raise HTTPException(status_code=413, detail=
            "that image is %d MB — over the %d MB this route will write"
            % (len(raw) // (1024 * 1024), AI_MAX_BYTES // (1024 * 1024)))
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(req.name or "photo").name).split(".")[0]
    stem = (stem or "photo")[:60]
    ext = "." + ("jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1))
    name = "ai_%s_%s%s" % (stem, time.strftime("%Y%m%d-%H%M%S"), ext)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (DOWNLOADS_DIR / name).write_bytes(raw)
    return {"name": name, "path": str(DOWNLOADS_DIR / name), "bytes": len(raw),
            "generated": True}


@app.get("/makerworld/files")
async def makerworld_files():
    """Images already sitting in downloads/, newest first, for the print-photo
    picker. MakerWorld wants photographs of a print you made, and those come
    from your own camera roll, not from the scrape — so the page can only offer
    files that are really on disk and let you say which ones are prints."""
    out = []
    try:
        cands = sorted((p for p in DOWNLOADS_DIR.iterdir()
                        if p.suffix.lower() in makerworld_flow.IMAGE_ACCEPT),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        cands = []
    for p in cands[:300]:
        try:
            st = p.stat()
        except OSError:
            continue
        # Two kinds of file live in this one folder and neither is a photograph
        # of the user's print: the studio's drawings (ai_*) and the source
        # listing's own gallery, which the downloader writes as {slug}_img_N.
        # The picker needs to be able to refuse both by name, not by trust.
        out.append({"name": p.name, "path": str(p), "url": "/file/" + p.name,
                    "bytes": st.st_size,
                    "generated": p.name.startswith("ai_"),
                    # Either the listing's own gallery or a cut-out made from it —
                    # neither is a photograph of the user's print, and the picker
                    # must be able to refuse both by name rather than by trust.
                    "from_source": bool(_SOURCE_FILE_NAME.search(p.stem)),
                    "modified": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(st.st_mtime))})
    return {"downloads_dir": str(DOWNLOADS_DIR), "files": out}


@app.get("/makerworld/published")
async def makerworld_published():
    """What the MakerWorld memory holds. Separate file, separate page — the
    Creality /published list is not this and never locks a MakerWorld publish."""
    return makerworld_ledger.summary()


@app.post("/makerworld/forget")
async def makerworld_forget(req: ForgetRequest):
    """You deleted the listing (or it was a dry run gone wrong): allow exactly
    that one model to be published again. Everything else stays blocked."""
    if not (req.match or "").strip():
        raise HTTPException(status_code=400, detail=
            "Name the listing to forget — its MakerWorld url, design id or model name.")
    dropped = makerworld_ledger.forget(match=req.match.strip())
    dropped.update({"count": makerworld_ledger.count()})
    return dropped


# ── Auto: find models, then publish each one ────────────────────────────────
# One run at a time, strictly sequential (fetch and publish both drive the same
# logged-in Chrome, so parallel uploads would fight over one form). ND-licensed
# finds are skipped and another candidate is used; every failure is recorded with
# its reason and the run continues. Progress is polled from /auto/status.
AUTO: dict = {"state": "idle", "job_id": None, "requested": 0, "discovered": 0,
              "items": [], "licence": {}, "keywords": [], "note": "",
              "started": None, "finished": None, "stop": False, "log_path": None,
              "skipped_already": 0, "already_done": [], "rejected_total": 0,
              "signin_required": False, "shortfall": None, "shortfall_reason": "",
              "published": 0}
AUTO_TASK: asyncio.Task | None = None
UPLOAD_LOCK = asyncio.Lock()          # one publish in the browser at a time
                                      # (Creality form or MakerWorld wizard)

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
    """Write every listing a run opened into the tried record, with the reason it
    ended for: a licence the page really carries, a policy refusal, a duplicate —
    or the pipeline's own bad luck (a fetch that died, a model file that did not
    come back, a Stop mid-page).

    Both kinds are recorded, but they are cooled down for different lengths:
    discover.remember_tried() gives a refusal a week and bad luck a couple of
    hours. Keeping the bad-luck listings in the record stops a run that is stopped
    and restarted from reading the same page twice; giving them the short window
    is what stops one unlucky run from emptying the pool of fresh listings until a
    20-model request cannot fill 20.
    Called with each log write, so a run stopped midway keeps what it decided."""
    for it in AUTO.get("items") or []:
        if listing_was_used(it):
            remember_tried(it.get("url", ""), it.get("name", ""),
                           it.get("reason", ""),
                           transient=not listing_was_refused(it))


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


def _file_attempt_note(fa: dict) -> str:
    """What the model-file step of a fetch saw, in the run's own words. A bare
    "no usable model file came back from the page" is what hid 20 identical skips
    on 2026-10-23 with no way to tell a model with no files from a browser that
    could not download."""
    if not fa:
        return "the fetch reported nothing"
    if fa.get("signin"):
        return "the page answered with Creality's sign-in form"
    if fa.get("navigated"):
        return ("the download click left the model page (it landed on %r)"
                % (fa.get("step2") or fa.get("control") or "somewhere else"))
    if not fa.get("control"):
        return ("the page showed no download control at all; its url was %s"
                % str(fa.get("page") or "?")[:80])
    if fa.get("click_error"):
        return "clicking %r failed: %s" % (fa.get("control"), fa["click_error"])
    if fa.get("rejected"):
        return ("its file dialog downloaded %s and none of it is real model "
                "geometry" % ", ".join(str(x)[:40] for x in fa["rejected"][:3]))
    if fa.get("files"):
        return ("its file dialog listed %s, and none of them started a download"
                % ", ".join("%r (%s)" % (str(f.get("name", ""))[:40],
                                         f.get("size") or "no size")
                            for f in fa["files"][:3]))
    return ("clicked %r%s and waited for a download that never started"
            % (fa.get("control"),
               (" then %r" % fa["step2"]) if fa.get("step2") else ""))


def topup_budget(count: int) -> int:
    """How many extra listing scans a run may spend chasing its target.

    A scan costs ~40s, so the old fixed 6 was fine for a small ask — but a
    20-model run does not need 6 scans, it needs 6 scans PER handful of models
    it still owes. Job 20261024-131810 measured why: of the 65 candidates the
    first scan harvested, the licence and household gates refused 35 and only
    11 were ever attempted, so a fixed ceiling was what would make a big ask
    come up short even with nothing broken. The budget now grows with the ask
    and never drops below the 6 a small run already had.
    """
    return max(6, (count or 0) // 2)


async def _auto_worker(count: int, keywords: list[str], job_id: str) -> None:
    import time as _time

    def _heartbeat() -> None:
        """Leave this run's state on disk for the supervisor to read.

        When the dashboard is too busy to answer a probe it cannot be asked
        whether a publish is in flight — asking is what just timed out — and a
        restart then destroys the run with it. A file the worker rewrites as it
        works is the one answer that survives. A run that is no longer live
        writes its finished state, so a wedged run cannot hide behind a stale
        note forever.
        """
        try:
            hb = BASE_DIR / "artifacts" / "auto_heartbeat.json"
            hb.parent.mkdir(exist_ok=True)
            hb.write_text(json.dumps(
                {"job_id": AUTO.get("job_id"), "state": AUTO.get("state"),
                 "at": _time.time(),
                 "published": sum(1 for i in (AUTO.get("items") or [])
                                  if i.get("state") == "published"),
                 "requested": AUTO.get("requested")}), encoding="utf-8")
        except Exception:
            pass

    def _log() -> str:
        _remember_outcomes()
        path = BASE_DIR / "artifacts" / ("auto_run_%s.json" % job_id)
        path.parent.mkdir(exist_ok=True)
        body = {
            "job_id": job_id, "requested": AUTO["requested"],
            "keywords": AUTO["keywords"], "licence": AUTO["licence"],
            "counts": _auto_counts(), "items": AUTO["items"],
            "timings": timing.snapshot(),
            # a run that published fewer than it was asked for says so, with the
            # reason — "done" alone used to hide 0-of-20
            "published": sum(1 for i in AUTO.get("items") or []
                             if i.get("state") == "published"),
            "shortfall": AUTO.get("shortfall"),
            "shortfall_reason": AUTO.get("shortfall_reason"),
            "signin_required": bool(AUTO.get("signin_required")),
            "finished": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _write_run_log(path, body)
        _heartbeat()
        return str(path)

    timing.reset()
    _run_t0 = _time.perf_counter()
    try:
        AUTO["state"] = "discovering"
        # a run starts with an empty queue and empty counters, even when the
        # same worker state was used by an earlier run in this process
        AUTO.update({"items": [], "licence": {}, "note": "", "discovered": 0,
                     # the run's own identity, written here and not only by the
                     # start endpoint: every report the worker makes about
                     # itself — the run log, the heartbeat the supervisor reads
                     # before it dares a restart — quotes these two, and a run
                     # that left them at the last job's values would tell the
                     # supervisor to protect a publish that is not happening.
                     "job_id": job_id, "requested": count,
                     "rejected_total": 0, "skipped_already": 0, "already_done": [],
                     # per-run flags: a stale one from the last run would stop this
                     # one before it began
                     "signin_required": False, "shortfall": None,
                     "shortfall_reason": "", "published": 0,
                     # Stop is a per-run flag like the rest: a run that was
                     # stopped left it set, and the next one would stop itself at
                     # its first checkpoint without you ever pressing anything.
                     "stop": False})
        _heartbeat()
        # Models already in the "already published" config are filtered out
        # DURING discovery (not queued and skipped later), so the run never
        # picks a model it has finished before.
        def _already_done(url: str, name: str) -> bool:
            return bool(ledger.find(source_url=url, name=name)["found"]
                        or was_tried_recently(url))

        def _on_progress(p: dict) -> None:
            AUTO["progress"] = p
            _heartbeat()      # discovery can run for minutes; say so while it does

        def _stop_requested() -> bool:
            return bool(AUTO.get("stop"))

        def _discover(need: int, exclude: set) -> dict:
            # One discovery pass. The first pass fills the queue; a top-up
            # replaces what did not publish. `exclude` holds every listing URL
            # this run already attempted, so none is picked twice.
            #
            # It harvests a SURPLUS (need + half again) rather than exactly
            # `need`, because a run asked for 20 does not get 20 publications out
            # of 20 candidates: licences re-read on the page, missing model files
            # and Creality's own duplicate check each cost rows. In one scan those
            # replacements are free; in the old design each one cost a second full
            # listing scan (the 2026-10-23 run spent 58 of its 82 minutes
            # discovering and still published nothing).
            disc = find_creality(keywords, need, "Downloads", _already_done,
                                 _on_progress, should_stop=_stop_requested,
                                 exclude=exclude, extra=max(4, need // 2),
                                 deadline_s=AUTO_DISCOVER_DEADLINE_S)
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
            # Still a shortfall: a stopped run that published none of the 20 it
            # was asked for must read as short by 20, not as a quiet stop.
            _short = count - _published_count()
            if _short > 0 and not AUTO.get("shortfall"):
                AUTO["shortfall"] = _short
                AUTO["shortfall_reason"] = "stopped by you before any model published"
            AUTO["finished"] = _time.strftime("%Y-%m-%d %H:%M:%S")
            AUTO["log_path"] = _log()
            return
        AUTO["state"] = "running"
        AUTO["log_path"] = _log()      # the queue is on disk from the start

        idx = 0
        topups = 0
        # A top-up costs a whole listing scan, so it is a last resort now that the
        # first scan harvests a surplus — but it must scale with the ask, or a
        # 20-model run stops chasing its target after 6 scans. See topup_budget().
        max_topups = topup_budget(count)
        while True:
            if idx >= len(AUTO["items"]):
                # The queue is used up. Anything that did not publish (refused,
                # duplicate, failed, fetch failed) is replaced by a NEW candidate,
                # never a repeat, until `count` models are on the account or the
                # listings run dry.
                need = count - _published_count()
                if need <= 0 or AUTO.get("stop") or AUTO.get("signin_required") \
                        or topups >= max_topups:
                    if need > 0:
                        # Honest about coming up short: "done" with 3 of 20 is not
                        # a finished run, and the caller must be able to see why.
                        AUTO["shortfall"] = need
                        AUTO["shortfall_reason"] = (
                            "signed out of Creality Cloud"
                            if AUTO.get("signin_required") else
                            "stopped by you" if AUTO.get("stop") else
                            "the listings gave no more candidates that the licence "
                            "and household gates allowed")
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
                    # the listings are dry for this run: say so with the number
                    AUTO["shortfall"] = count - _published_count()
                    AUTO["shortfall_reason"] = (
                        "the listings ran dry — a top-up scan found no candidate "
                        "the gates allowed"
                        + (" (%s)" % (more["disc"].get("note") or "")
                           if more["disc"].get("note") else ""))
                    break
                AUTO["state"] = "running"
                continue
            it = AUTO["items"][idx]
            idx += 1
            # The queue was deliberately harvested a half again bigger than the
            # run was asked for, so it outlasts the target. Without this the run
            # drains it to the end: live 2026-10-24, a run asked for 20 published
            # 22, because the surplus it kept in reserve for the models that
            # failed was published too. Every one of those is a real listing on
            # the user's account that they did not ask for, so the target is a
            # ceiling as well as a floor.
            if _published_count() >= count:
                break
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
            if res.get("signin_required"):
                # The browser is signed out of Creality Cloud. That is not this
                # model's bad luck: EVERY remaining model in the queue will read
                # its licence fine (the page is public), find no download and be
                # skipped — which is how the 2026-10-23 run spent 82 minutes to
                # publish 0 of 20. Say what is wrong once, and stop.
                it["state"] = FETCH_FAILED
                it["reason"] = ("the debug Chrome is signed out of Creality Cloud: "
                                "its download click landed on the sign-in page")
                AUTO["note"] = ("SIGN IN REQUIRED — open the debug Chrome window, log in to "
                                "crealitycloud.com once, and start the run again. The run "
                                "stopped here instead of skipping every remaining model.")
                AUTO["signin_required"] = True
                break
            # What the model-file step saw, kept on the row even when it worked:
            # a run that skipped 20 models had nothing to show for any of them.
            if res.get("file_attempt"):
                it["file_attempt"] = res["file_attempt"]
            # A page that offered no download control at all — or a click that
            # took the tab off the model page — is the FETCH failing, not the
            # model being undownloadable. It gets one clean retry on a fresh tab
            # before the candidate is written off (measured: the very same model
            # hands over its file in seconds when asked again), and it is retried
            # HERE, before the licence gate, so nothing downstream reads from the
            # throwaway first attempt.
            _fa = res.get("file_attempt") or {}
            if (not res.get("stl_path") and (not _fa.get("control") or _fa.get("navigated"))
                    and not AUTO.get("stop")):
                res_b, _e_b, _t_b, stop_b = await _fetch_with_retries(it)
                if stop_b:
                    AUTO["state"] = "stopped"
                    it["state"] = "queued"
                    it["reason"] = "run stopped while retrying this model's model file"
                    break
                if res_b is not None and (res_b.get("stl_path")
                                          or res_b.get("signin_required")):
                    res = res_b
                    it["file_attempt"] = res.get("file_attempt") or {}
                    it["refetch_note"] = ("re-fetched once for its model file: the first read "
                                          + ("left the model page" if _fa.get("navigated")
                                             else "found no download control"))
            if res.get("signin_required"):
                it["state"] = FETCH_FAILED
                it["reason"] = ("the debug Chrome is signed out of Creality Cloud: "
                                "its download click landed on the sign-in page")
                AUTO["note"] = ("SIGN IN REQUIRED — open the debug Chrome window, log in to "
                                "crealitycloud.com once, and start the run again.")
                AUTO["signin_required"] = True
                break
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
                    if res2.get("signin_required"):
                        # the retry itself discovered the sign-out: same fast-fail
                        it["state"] = FETCH_FAILED
                        AUTO["note"] = ("SIGN IN REQUIRED — open the debug Chrome "
                                        "window, log in to crealitycloud.com once, "
                                        "and start the run again.")
                        AUTO["signin_required"] = True
                        break
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
                it["reason"] = ("no usable model file came back from the page (%s)"
                                % _file_attempt_note(res.get("file_attempt") or {}))
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
                g = res.get("gallery") or {}
                it["gallery"] = g        # the evidence, on the row, not only on disk
                it["reason"] = cover_failure_reason(g)
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
                # The whole sentence, not its first 200 characters: a Model-Source
                # refusal lands here, and the half that says WHAT the form showed
                # was exactly the part the cap cut off (job 20261024-131810 ended
                # at "the registered source read"). The uploader's own evidence
                # goes on the row too, so the next run needs no browser to see why.
                it["reason"] = str(e)[:400]
                _v = getattr(uploader, "UPLOAD_VERIFY", {}) or {}
                it["upload_verify"] = {k: _v[k] for k in (
                    "remix_source_declared", "remix_source_why",
                    "remix_declared_by", "remix_registered_href",
                    "remix_suggestion_clicked",
                    "remix_suggestions_seen", "remix_suggestions_refused",
                    "attach", "attach_registered", "attach_note",
                    "read_via") if k in _v}
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
                    "remix_declared_by",
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
        # Say what the ceiling left behind: the run stopped with candidates still
        # in hand, and a row that still reads "queued" is otherwise a mystery.
        _left = sum(1 for it in AUTO["items"]
                    if it.get("state") in ("queued", "fetching"))
        if _published_count() >= count and _left:
            _tail = "%d candidate%s the run did not touch, because you already " \
                    "have the %d you asked for" % (_left, "" if _left == 1 else "s", count)
            AUTO["note"] = ((AUTO.get("note") + " | ") if AUTO.get("note")
                            else "") + _tail
        # How far the run came up short, in every ending: "done" with 3 of 20 is
        # not a finished run, and a run that stopped at a sign-out is short by
        # everything it never tried.
        _short = count - _published_count()
        if _short > 0 and not AUTO.get("shortfall"):
            AUTO["shortfall"] = _short
            AUTO["shortfall_reason"] = (
                "signed out of Creality Cloud"
                if AUTO.get("signin_required") else
                "stopped by you with %d still to publish" % _short
                if AUTO.get("state") == "stopped" else
                "the queue of candidates ran out with %d still to publish" % _short)
        if AUTO.get("signin_required"):
            # Not a finished run and not a crash: the account needs a login before
            # anything else can publish. "error" is the state the dashboard shows
            # the note for, and the note is the one thing the user has to do.
            AUTO["state"] = "error"
        elif AUTO.get("state") != "stopped":
            AUTO["state"] = "done"
        AUTO["published"] = _published_count()
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
                 "progress": None, "signin_required": False, "shortfall": None,
                 "shortfall_reason": "", "published": 0})
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


# ── MakerWorld auto run: find N, publish N ───────────────────────────────────
# Type a number and this finds that many designs on MakerWorld's own
# licence-filtered listing — only CC0 / BY / BY-SA, the three chips whose terms
# allow a remix that gets re-published — then fetches each one and drives the
# publish wizard end to end with no per-item approval. What still stops an item
# is not a human changing their mind, it's a fact: the licence read back off the
# source page, the already-published ledger, and MakerWorld's own "≥3 photos of
# the printed model" rule, which the source listing's own photographs satisfy.
MW_AUTO: dict = {"state": "idle", "job_id": None, "requested": 0, "discovered": 0,
                 "items": [], "note": "", "started": None, "finished": None,
                 "stop": False, "log_path": None, "published": 0, "error": ""}
MW_AUTO_TASK: asyncio.Task | None = None


async def _makerworld_auto_worker(count: int, job_id: str) -> None:
    log_path = BASE_DIR / "artifacts" / f"makerworld_auto_{job_id}.json"
    MW_AUTO["log_path"] = str(log_path)

    def _log():
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                json.dump(MW_AUTO, fh, indent=2, ensure_ascii=False, default=str)
        except OSError:
            pass

    def _stopped():
        return bool(MW_AUTO.get("stop"))

    try:
        # Over-harvest: a find gets refused for reasons only the page can answer
        # (no downloadable file, a licence the listing mislabels), and the user
        # still asked for N published.
        found = await discover_models("makerworld", min(40, max(count * 2, count)))
        MW_AUTO["discovered"] = len(found)
        MW_AUTO["state"] = "running"
        _log()
        for it in found:
            if _stopped() or MW_AUTO["published"] >= count:
                break
            url = it.get("url") or ""
            row = {"name": it.get("name", ""), "url": url, "state": "fetching",
                   "why": "", "at": time.strftime("%H:%M:%S")}
            MW_AUTO["items"].append(row)
            _log()
            if makerworld_ledger.find(source_url=url)["found"]:
                row["state"] = "duplicate"
                row["why"] = "already published on MakerWorld"
                _log()
                continue
            try:
                res = await asyncio.to_thread(dl_fetch, url)
            except Exception as exc:  # noqa: BLE001
                row["state"] = "fetch-failed"
                row["why"] = str(exc)[:300]
                _log()
                continue
            name = res.get("name") or row["name"]
            # The listing's licence chips got it into the queue; this is the
            # source page's own answer, read on the page that is already open.
            lic = makerworld_flow.license_plan(res.get("license_raw", ""))
            row["licence"] = lic["code"] or ""
            if lic["refused"]:
                row["state"] = "rejected"
                row["why"] = lic["why"]
                _log()
                continue
            if not lic["code"]:
                row["state"] = "skipped"
                row["why"] = ("the source page never named a licence — an unattended run "
                              "will not publish a remix on a guess")
                _log()
                continue
            if not res.get("stl_path"):
                row["state"] = "skipped"
                row["why"] = ("the model file could not be fetched — "
                              + (res.get("file_error")
                                 or "the listing offered no downloadable file"))
                _log()
                continue
            # The public design API named the source's own category and tags;
            # the form's pickers only accept its own vocabulary, so they are
            # passed through exactly as the source declares them.
            cat = next((c for c in (res.get("categories") or []) if c), "")
            if not cat:
                row["state"] = "skipped"
                row["why"] = ("the source page named no category, and MakerWorld's form "
                              "requires one")
                _log()
                continue
            tag_list = res.get("tags") or []
            need = makerworld_flow.MIN_REAL_PHOTOS + 1
            # How many pictures this listing can be cut from is settled before the
            # finisher runs, not after: a gallery thinner than the form's own
            # minimum cannot be made into enough pictures by any amount of work,
            # and the minutes it would take are the run's scarcest resource.
            try:
                src_imgs = _makerworld_source_files(res.get("image_paths") or [])
            except Exception as exc:                       # noqa: BLE001
                row["state"] = "photos-failed"
                row["why"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                _log()
                continue
            if len(src_imgs) < need:
                row["state"] = "photos-failed"
                row["why"] = (f"the source listing shows {len(src_imgs)} of its own "
                              f"photographs and this route needs {need} — "
                              f"{makerworld_flow.MIN_REAL_PHOTOS} for the photo slot "
                              "plus one for the cover — so it was skipped before "
                              "anything was cut")
                _log()
                continue
            # Only now that the listing is going out are its pictures made. They
            # are the source's own photographs of the model with the backdrop
            # rebuilt here, and each one is looked at before it is used — the
            # listing shows the real model, not a machine's idea of it.
            row["pictures"] = "same model, new background"
            row["state"] = "cutting the model out of the source's photos"
            _log()
            try:
                pics = await asyncio.to_thread(_makerworld_cutout_photos, src_imgs, "")
                enough = pics["ok"]
            except Exception as exc:                     # noqa: BLE001
                # One bad picture must not end the run: this find is skipped and
                # the next one still gets its turn.
                pics = {"got": 0, "asked": need,
                        "notes": [f"{type(exc).__name__}: {str(exc)[:200]}"]}
                enough = False
            if not enough:
                row["state"] = "photos-failed"
                row["why"] = (
                    f"only {pics['got']} of {pics['asked']} of the source's own pictures came "
                    f"out looking like the model on a new background, and the form needs "
                    f"{makerworld_flow.MIN_REAL_PHOTOS} plus a cover — "
                ) + " | ".join(pics["notes"][-2:])
                _log()
                continue
            covers, photos = pics["covers"], pics["print_photos"]
            row["looked_at"] = [{"source": r["source"], "looked_right": r["looked_right"],
                                 "tries": [{"background": a["background"], "ok": a["ok"]}
                                           for a in r["attempts"]]}
                                for r in pics.get("reviews", [])]
            row["photos"] = len(photos)
            row["picture_files"] = [os.path.basename(p) for p in covers + photos]
            row["state"] = "uploading"
            _log()
            try:
                async with UPLOAD_LOCK:
                    up = await makerworld_upload(
                        name=name, description=res.get("description", ""),
                        stl_path=res["stl_path"], image_paths=covers, source_url=url,
                        dry_run=False, print_photos=photos, remix_source_url=url,
                        remix_source_name=name, license_raw=res.get("license_raw", ""),
                        tags=tag_list, category=cat, should_stop=_stopped)
            except Exception as exc:  # noqa: BLE001
                up = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
            if up.get("success") and not up.get("error") and not up.get("no_submit"):
                row["state"] = "published"
                row["published_url"] = up.get("url", "")
                row["pending_review"] = bool(up.get("pending_review"))
                MW_AUTO["published"] += 1
                makerworld_ledger.record(name=name, source_url=url,
                                         published_url=up.get("url", ""),
                                         design_id=str(up.get("design_id") or ""),
                                         file_path=res["stl_path"], outcome="published",
                                         note=("MakerWorld is reviewing the submission"
                                               if up.get("pending_review") else ""))
            else:
                row["state"] = "stopped" if up.get("stopped") else "failed"
                row["why"] = str(up.get("error") or up.get("message") or "")[:300]
                makerworld_ledger.record(name=name, source_url=url, outcome=row["state"],
                                         file_path=res.get("stl_path", ""),
                                         note=row["why"])
            _log()
        MW_AUTO["state"] = "stopped" if _stopped() else "done"
    except Exception as exc:  # noqa: BLE001
        MW_AUTO["state"] = "failed"
        MW_AUTO["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    finally:
        # A cancellation (the task stopped, the loop's own stop, an interpreter
        # shutdown) is a BaseException and sails straight past the handler above,
        # which used to leave state at "running" forever — and every later
        # publish by hand was then refused with 409 by its own overlap guard.
        if MW_AUTO.get("state") in ("discovering", "running"):
            MW_AUTO["state"] = "stopped" if _stopped() else "interrupted"
            MW_AUTO["error"] = MW_AUTO.get("error") or (
                "the run ended mid-loop (it was stopped, or the task was cancelled) — "
                "nothing is publishing now")
        MW_AUTO["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        MW_AUTO["note"] = (f"{MW_AUTO['published']} of {count} published"
                           + (f" — {MW_AUTO['error']}" if MW_AUTO.get("error") else ""))
        _log()


@app.post("/makerworld/auto/start")
async def makerworld_auto_start(req: AutoRequest):
    """Find N licence-remixable MakerWorld designs and publish each one."""
    global MW_AUTO_TASK
    # The run posts the source listing's own model with a rebuilt background, and
    # refuses to start without the acknowledgement for that — the page's tick is
    # an answer, not plumbing.
    if not req.assert_source:
        raise HTTPException(status_code=400, detail=
            "Every picture this run posts is the source listing's own photograph of the "
            "model with only its background rebuilt here — not a photograph of a print you "
            "made — and MakerWorld can reject a listing for that. Nothing is started until "
            "the tick that says you know that is on.")
    if MW_AUTO.get("state") in ("discovering", "running"):
        raise HTTPException(status_code=409, detail=
            "A MakerWorld auto run is already going (%s). Stop it first or wait for it."
            % MW_AUTO["state"])
    if AUTO.get("state") in ("discovering", "running"):
        raise HTTPException(status_code=409, detail=
            "The Creality auto run is publishing right now (%s) — one browser, one form "
            "at a time. Stop it, then start the MakerWorld run." % AUTO["state"])
    count = int(req.count or 0)
    if count < 1:
        raise HTTPException(status_code=400, detail=
            "Type how many models you want, 1 or more (you sent %s)." % req.count)
    # Open the logged-in Chrome before the run starts. The capture stage needs
    # it for the file download, and it used to fail there with a bare
    # "connection refused" that read like MakerWorld refusing the file.
    if not await asyncio.to_thread(makerworld_ensure_browser):
        raise HTTPException(status_code=503, detail=
            "Chrome's debug port 9222 would not open, so the run was not started. "
            "Check that Chrome is installed; if a Chrome window using the pipeline's "
            "debug profile is already open, close it and try Find and publish again.")
    job_id = time.strftime("%Y%m%d-%H%M%S")
    MW_AUTO.update({"state": "discovering", "job_id": job_id, "requested": count,
                    "discovered": 0, "items": [], "note": "", "finished": None,
                    "started": time.strftime("%Y-%m-%d %H:%M:%S"), "stop": False,
                    "log_path": None, "published": 0, "error": ""})
    MW_AUTO_TASK = asyncio.create_task(_makerworld_auto_worker(count, job_id))
    return {"ok": True, "job_id": job_id, "requested": count,
            "licences": MAKERWORLD_LICENSES}


@app.post("/makerworld/auto/stop")
async def makerworld_auto_stop():
    MW_AUTO["stop"] = True
    return {"ok": True, "state": MW_AUTO.get("state")}


@app.get("/makerworld/auto/status")
async def makerworld_auto_status():
    out = dict(MW_AUTO)
    out["licences"] = MAKERWORLD_LICENSES
    out["already_published"] = makerworld_ledger.count()
    return out


# ── Originals: our own models, photographed from their own STL ──────────────
# The dashboard's "Find & Publish" no longer re-uploads another designer's
# model or photos. It builds original household designs from seeded specs,
# renders the exact STL it will upload, and puts each finished preview on the
# shelf. Publishing one is a separate, explicit click: that click is the owner's
# yes for that one model, recorded against the hashes on screen, and only then
# does the gated originals publisher open the Creality form.
ORIGINALS: dict = {
    "state": "idle", "job_id": None, "requested": 0, "items": [],
    "note": "", "started": None, "finished": None, "stop": False,
    "progress": {}, "log_path": None, "publishing": "", "stale": [],
}
ORIGINALS_TASK: asyncio.Task | None = None
ORIGINALS_PUBLISH_LOCK = asyncio.Lock()


def _originals_root() -> Path:
    override = (os.environ.get("CREALITY_ORIGINALS_DIR") or "").strip()
    return Path(override) if override else (BASE_DIR / "artifacts" / "originals")


def _originals_shelf() -> list[dict]:
    """Every finished preview on disk, with the state the dashboard shows.

    A `PUBLISHED.json` marker is the first answer; the ledger is the second, so
    a listing made by the CLI is never offered for a second publish.
    """
    root = _originals_root()
    publishing = ORIGINALS.get("publishing") or ""
    hidden = set(ORIGINALS.get("stale") or [])
    shelf = []
    for p in originals_lib.complete_previews(root):
        if p["key"] in hidden:
            continue
        marker = originals_lib.published_marker(root / p["key"])
        state, url = "preview", ""
        if marker:
            state, url = "published", marker.get("url") or ""
        else:
            hit = ledger.find(name=p["title"], file_path=p["stl"])
            if hit["found"]:
                state = "published"
                url = (hit.get("entry") or {}).get("published_url") or ""
        if state != "published" and p["approved"]:
            state = "approved"
        if p["key"] == publishing:
            state = "publishing"
        shelf.append({
            "key": p["key"], "title": p["title"], "family": p["family"],
            "state": state, "published_url": url,
            "contact_url": "/originals/contact/" + p["key"],
            "photo_urls": ["/originals/photo/%s/%s"
                           % (p["key"], Path(x).name) for x in p["photos"]],
            "dims_mm": p["dims_mm"], "licence": p["licence"] or originals_publish.LICENCE,
            "photos": len(p["photos"]), "seconds": p["seconds"],
        })
    return shelf


def _originals_counts(shelf: list[dict]) -> dict:
    run_items = ORIGINALS.get("items") or []
    counts = {"ready": 0, "approved": 0, "published": 0, "publishing": 0,
              "prepared": 0, "working": 0, "queued": 0, "failed": 0}
    for p in shelf:
        st = p["state"]
        counts[st] = counts.get(st, 0) + 1
        if st in ("preview", "approved"):
            counts["ready"] += 1
    for it in run_items:
        st = it.get("state") or "queued"
        if st in ("working", "queued"):
            counts[st] += 1
        elif st == "failed":
            counts["failed"] += 1
        if it.get("key") and st in ("preview", "approved", "published",
                                   "publishing"):
            counts["prepared"] += 1
    return counts


def _set_original_item(key: str, state: str, *, reason: str = "",
                       result: str = "") -> None:
    for it in ORIGINALS.get("items") or []:
        if it.get("key") == key:
            it["state"] = state
            it["reason"] = reason
            if result:
                it["result"] = result
            return


def _discard_original(key: str) -> None:
    """Remove an earlier unpublished preview; published work is kept."""
    if not originals_lib.KEY_RE.fullmatch(key or ""):
        return
    model_dir = _originals_root() / key
    rec = originals_lib.read_record(model_dir) or {}
    title = (rec.get("spec") or {}).get("title") or key
    stl = (rec.get("files") or {}).get("stl") or ""
    if originals_lib.published_marker(model_dir):
        return
    if ledger.find(name=title, file_path=stl)["found"]:
        return
    originals_lib.discard(model_dir)


async def _originals_worker(count: int, job_id: str) -> None:
    """Design, build and photograph originals until `count` are ready.

    Nothing is uploaded here. Each finished model is written to disk with its
    five 4:3 photos, contact sheet and exact STL; the owner's per-model click
    publishes it afterwards.
    """
    import time as _time

    def _log() -> None:
        path = BASE_DIR / "artifacts" / ("originals_run_%s.json" % job_id)
        body = {
            "job_id": job_id, "requested": count,
            "state": ORIGINALS.get("state"), "note": ORIGINALS.get("note"),
            "items": ORIGINALS.get("items") or [],
            "counts": _originals_counts(_originals_shelf()),
            "finished": ORIGINALS.get("finished"),
        }
        _write_run_log(path, body)
        ORIGINALS["log_path"] = str(path)

    try:
        ORIGINALS.update({
            "state": "preparing", "job_id": job_id, "requested": count,
            "items": [], "note": "", "stop": False, "progress": {},
            "started": _time.strftime("%Y-%m-%d %H:%M:%S"), "finished": None,
            "log_path": None, "publishing": "", "stale": [],
        })
        root = _originals_root()
        root.mkdir(parents=True, exist_ok=True)

        # A fresh set every time: earlier previews are hidden now and deleted
        # when this run ends, unless they were published. They stay on disk
        # during the run so prepare() sees their titles and builds new variants.
        ORIGINALS["stale"] = [
            p["key"] for p in _originals_shelf()
            if p["state"] in ("preview", "approved")
        ]
        items: list[dict] = []
        ORIGINALS["items"] = items
        prepared = 0

        # Spare family slots: a build that fails must not make the run short.
        families = originals_lib.families_for(count + 4, root)
        fi = 0
        while prepared < count and fi < len(families):
            if ORIGINALS.get("stop"):
                break
            family = families[fi]
            fi += 1
            item = {"key": "", "family": family,
                    "title": originals_lib.spec.FAMILIES.get(family, family),
                    "state": "working", "reason": "", "result": "",
                    "started_at": _time.time()}
            items.append(item)
            ORIGINALS["progress"] = {"index": prepared + 1, "total": count,
                                     "family": family,
                                     "title": item["title"]}
            ORIGINALS["state"] = "preparing"
            _log()
            try:
                made = await asyncio.to_thread(
                    originals_lib.prepare, root, family,
                    res=originals_lib.RES)
            except Exception as e:
                item["state"] = "failed"
                item["reason"] = str(e)[:400]
                _log()
                continue
            item.update({
                "key": made["key"], "title": made["title"],
                "state": "preview", "contact_url":
                    "/originals/contact/" + made["key"],
                "seconds": made.get("seconds"),
            })
            prepared += 1
            ORIGINALS["progress"] = {"index": prepared, "total": count,
                                     "family": family, "title": made["title"]}
            _log()

        ORIGINALS["state"] = "stopped" if ORIGINALS.get("stop") else "done"
        if prepared < count:
            ORIGINALS["note"] = (
                "%d of the %d asked for could not be prepared — the rows say why."
                % (count - prepared, count))
    except Exception as e:
        ORIGINALS["state"] = "error"
        ORIGINALS["note"] = "run failed: " + str(e)[:200]
        for it in ORIGINALS.get("items") or []:
            if it.get("state") in ("working", "queued"):
                it["state"] = "failed"
                it["reason"] = "run error: " + str(e)[:160]
    finally:
        ORIGINALS["finished"] = _time.strftime("%Y-%m-%d %H:%M:%S")
        ORIGINALS["progress"] = {}
        for key in ORIGINALS.get("stale") or []:
            try:
                _discard_original(key)
            except Exception:
                pass
        ORIGINALS["stale"] = []
        try:
            _log()
        except Exception:
            pass


@app.post("/originals/start")
async def originals_start(req: OriginalsRequest):
    """Design and photograph original models; nothing is uploaded."""
    global ORIGINALS_TASK
    if ORIGINALS.get("state") == "preparing":
        raise HTTPException(status_code=409, detail=
            "An originals run is already working — stop it or wait for it to finish.")
    if ORIGINALS.get("publishing"):
        raise HTTPException(status_code=409, detail=
            "A model is publishing right now — wait for it to finish.")
    count = int(req.count or 0)
    if count < 1:
        raise HTTPException(status_code=400, detail=
            "Type how many originals you want, 1 or more (you sent %s)." % req.count)
    if count > 50:
        raise HTTPException(status_code=400, detail=
            "50 originals in one run is the ceiling (each takes about 35 seconds "
            "to photograph). You asked for %d." % count)
    import time as _time
    job_id = _time.strftime("%Y%m%d-%H%M%S")
    ORIGINALS_TASK = asyncio.create_task(_originals_worker(count, job_id))
    return {"ok": True, "job_id": job_id, "requested": count}


@app.post("/originals/stop")
async def originals_stop():
    ORIGINALS["stop"] = True
    return {"ok": True, "state": ORIGINALS.get("state")}


@app.get("/originals/status")
async def originals_status():
    shelf = _originals_shelf()
    return {
        "state": ORIGINALS.get("state"), "job_id": ORIGINALS.get("job_id"),
        "requested": ORIGINALS.get("requested"),
        "items": ORIGINALS.get("items") or [], "shelf": shelf,
        "counts": _originals_counts(shelf), "note": ORIGINALS.get("note") or "",
        "stop": bool(ORIGINALS.get("stop")),
        "progress": ORIGINALS.get("progress") or {},
        "publishing": ORIGINALS.get("publishing") or "",
        "started": ORIGINALS.get("started"),
        "finished": ORIGINALS.get("finished"),
        "licence": originals_publish.LICENCE,
    }


@app.post("/originals/publish")
async def originals_publish_endpoint(req: OriginalPublishRequest):
    """Publish ONE finished original, after the owner's explicit yes.

    The click is the approval: the hashes of the STL, contact sheet and five
    photos on disk are recorded first, and the same gated publisher used by the
    CLI does the name check and the upload. Nothing here can publish a model
    that has no finished preview, and a second click cannot publish it twice.
    """
    if AUTO.get("state") in ("discovering", "running"):
        raise HTTPException(status_code=409, detail=
            "An auto run is using the Creality form — stop it first, or wait "
            "until it finishes, then publish this original.")
    if ORIGINALS.get("publishing") or ORIGINALS_PUBLISH_LOCK.locked():
        raise HTTPException(status_code=409, detail=
            "Another original is publishing right now — wait for it to finish.")
    key = (req.key or "").strip()
    if not originals_lib.KEY_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="Not a valid model key.")
    model_dir = _originals_root() / key
    if not model_dir.is_dir() or not originals_lib.is_complete(model_dir):
        raise HTTPException(status_code=404, detail=
            "No finished preview for that model — design and photograph it first.")
    rec = originals_lib.read_record(model_dir) or {}
    title = (rec.get("spec") or {}).get("title") or key
    stl = (rec.get("files") or {}).get("stl") or ""
    marker = originals_lib.published_marker(model_dir)
    if marker:
        raise HTTPException(status_code=409, detail=
            "This original is already published: %s" % (marker.get("url") or key))
    hit = ledger.find(name=title, file_path=stl)
    if hit["found"]:
        originals_lib.write_published(
            model_dir, (hit.get("entry") or {}).get("published_url") or "",
            title, time.strftime("%Y-%m-%d %H:%M:%S"))
        raise HTTPException(status_code=409, detail=ledger.describe(hit))

    async with ORIGINALS_PUBLISH_LOCK:
        ORIGINALS["publishing"] = key
        _set_original_item(key, "publishing")
        try:
            approval = await asyncio.to_thread(
                originals_publish.write_approval, model_dir,
                req.note.strip() or "approved in the dashboard")
            async with UPLOAD_LOCK:
                plan = await asyncio.to_thread(
                    originals_publish.publish, model_dir, live=True)
            url = plan.get("url") or ""
            originals_lib.write_published(
                model_dir, url, title, time.strftime("%Y-%m-%d %H:%M:%S"))
            _set_original_item(key, "published", result=url)
            return {
                "ok": True, "published": True, "key": key, "title": title,
                "url": url, "approval": approval,
                "novelty": plan.get("novelty") or {},
                "ledger_recorded": plan.get("ledger_recorded"),
            }
        except originals_publish.PublishRefused as e:
            _set_original_item(key, "failed", reason=str(e))
            raise HTTPException(status_code=400, detail=str(e))
        except originals_publish.PublishError as e:
            _set_original_item(key, "failed", reason=str(e))
            raise HTTPException(status_code=502, detail=str(e))
        except Exception as e:
            # An unexpected publisher fault must leave the row retryable, not
            # stuck on "publishing", and must say what happened.
            _set_original_item(key, "failed", reason=str(e)[:400])
            raise HTTPException(status_code=502, detail=
                "The publish failed before it was confirmed: %s" % str(e)[:300])
        finally:
            ORIGINALS["publishing"] = ""


@app.get("/originals/contact/{key}")
async def originals_contact(key: str):
    key = Path(key).name
    if not originals_lib.KEY_RE.fullmatch(key):
        raise HTTPException(status_code=404, detail="File not found")
    path = _originals_root() / key / ("%s_contact.jpg" % key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path))


@app.get("/originals/photo/{key}/{name}")
async def originals_photo(key: str, name: str):
    key, name = Path(key).name, Path(name).name
    if (not originals_lib.KEY_RE.fullmatch(key) or not name.endswith(".jpg")
            or name != Path(name).name):
        raise HTTPException(status_code=404, detail="File not found")
    path = _originals_root() / key / "photos" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path))


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
    disk = _code_fingerprint()
    add("running the code on disk",
        ((not head) or head == BUILD.split("+")[0]) and disk == CODE_FINGERPRINT,
        "running %s (%s), disk is %s, up %ss" % (BUILD, CODE_FINGERPRINT, disk,
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
    disk_fp = _code_fingerprint()
    return {"status": "ok", "build": BUILD, "pid": os.getpid(),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(STARTED_AT)),
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "port": SERVER_PORT,
            "head_rev": head,
            "code_fingerprint": CODE_FINGERPRINT,
            # Is the code ON DISK what this process is running? A server keeps
            # serving the code it started with, which is why a "restart" could
            # look like it did nothing. This makes that answerable — and checks
            # the files themselves, not only the commit they came from.
            "disk_fingerprint": disk_fp,
            "build_is_current": bool(head) and head == BUILD.split("+")[0]
                                and CODE_FINGERPRINT == disk_fp,
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
