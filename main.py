import asyncio
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from generator.generate import generate as generate_fn
from downloader import fetch as dl_fetch
from editor import edit_all
from uploader import upload as uploader_upload, _debug_port_up
from discover import discover as discover_models
import analytics

load_dotenv()

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"

app = FastAPI(title="Creality Pipeline")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)


@app.on_event("startup")
async def _start_analytics_auto():
    analytics.start_auto()


# ── Request / Response models ─────────────────────────────────────────────────

class FetchRequest(BaseModel):
    url: str

class UploadRequest(BaseModel):
    name: str
    description: str
    stl_path: str
    image_paths: list[str]
    source_url: str = ""
    dry_run: bool = False

class DiscoverRequest(BaseModel):
    site: str
    count: int = 20


class GenerateRequest(BaseModel):
    prompt: str
    image_path: str = ""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/generate")
async def generate_endpoint(req: GenerateRequest):
    """Nemotron 3 Ultra designs it from the user's words (or image); the
    local forge compiles a real binary STL; the verifier must pass before
    any preview is shown. Runs in a worker thread — a design call takes
    ~1.5-2 min (measured 76-90 s) plus forge time."""
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Describe what to make.")
    try:
        result = await asyncio.to_thread(
            generate_fn, prompt,
            req.image_path or None, None)
    except Exception as e:
        name = type(e).__name__
        detail = str(getattr(e, 'report', {}).get('errors', None) or e)
        msg = detail if isinstance(detail, str) else "; ".join(map(str, detail))
        raise HTTPException(status_code=502, detail=f"Generation failed ({name}): {msg[:400]}")
    # Serve paths as /file/ URLs the dashboard can show and download
    result["stl_url"] = "/file/" + Path(result["stl_path"]).name
    result["preview_urls"] = ["/file/" + Path(p).name
                              for p in result.get("preview_paths", [])]
    return result
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

    # Run the local editor (no API — see editor.py) on all preview images.
    edited_paths = []
    if result.get("image_paths"):
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


    return {
        "name": result.get("name", ""),
        "description": result.get("description", ""),
        "source_url": req.url,
        "stl_path": result.get("stl_path"),
        "stl_url": to_url(result.get("stl_path")),
        "original_images": [to_url(p) for p in result.get("image_paths", [])],
        "edited_images": [to_url(p) for p in edited_paths],
        "edit_warning": result.get("edit_warning"),
        "_raw_stl": result.get("stl_path"),
        "_raw_images": edited_paths if edited_paths else result.get("image_paths", []),
    }


@app.post("/discover")
async def discover_endpoint(req: DiscoverRequest):
    """Research only: list the top/most-popular models on a site. Does not
    download, edit, or upload anything."""
    try:
        models = await discover_models(req.site, max(1, min(req.count, 40)))
        return {"site": req.site, "models": models}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Discovery failed: {e}")


@app.post("/upload")
async def upload_to_creality(req: UploadRequest):
    # Always credit the original designer: append the source link to the
    # description if it isn't already mentioned.
    description = req.description or ""
    if req.source_url and req.source_url not in description:
        description = (description.rstrip() + "\n\nOriginal design: " + req.source_url).strip()
    try:
        result = await uploader_upload(req.name, description, req.stl_path,
                                       req.image_paths, req.source_url,
                                       dry_run=req.dry_run)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    """Quick self-check: is the app configured and its dependencies reachable?"""
    checks = {
        "image_editor": "local (no API key needed)",
        "browser_debug_port_up": _debug_port_up(),
        "downloads_dir": str(DOWNLOADS_DIR),
        # Launching Chromium is slow and blocking — keep it off the event loop
        "playwright_chromium": await asyncio.to_thread(_chromium_available),
    }
    return {"status": "ok", "checks": checks}


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  Creality Pipeline dashboard:  http://localhost:8000")
    print("  Health check:                 http://localhost:8000/health")
    print("=" * 60)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="info")
