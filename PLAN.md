# Creality Pipeline — Runbook & Verification Plan

> Status as of 2026-09-12: **all 16 smoke tests pass, server verified live on :8000.**

## 1. What this is

A Python/FastAPI pipeline that turns 3D-print model pages (Thingiverse, Printables,
MakerWorld) into published Creality Cloud listings:

1. **Discover** (`discover.py`) — research-only scrape of trending models (headless Chromium).
2. **Fetch & Edit** (`downloader.py` + `editor.py`) — downloads model images + STL/CAD,
   then **locally** erases text, recolors the product red, and swaps in a clean studio
   background (no API keys, no cost, works offline). You then pick which images to upload.
3. **Publish** (`uploader.py`) — drives your real Chrome (CDP on port 9222) to fill the
   Creality Cloud upload form and submit.

## 2. Prerequisites checklist

| Requirement | How to verify |
|---|---|
| Python 3.10+ in `.venv` | `.venv\Scripts\python.exe --version` |
| Dependencies installed | `.venv\Scripts\pip.exe install -r requirements-dev.txt` |
| Playwright Chromium | `.venv\Scripts\python.exe -m playwright install chromium` |
| Google Chrome installed | default path `Program Files\\Google\\Chrome\\Application\\chrome.exe` (Edge fallback) |
| Logins (one-time, in the Chrome window the app opens) | MakerWorld + Creality Cloud |
| API keys | **None — the pipeline is 100% local and free forever** |

## 3. Start / stop / verify

```bash
# start
cd C:\Users\henry\dev\creality-pipeline
.venv\Scripts\python.exe main.py          # serves http://localhost:8000

# verify (all should pass)
curl http://127.0.0.1:8000/                       # dashboard HTML, 200
curl http://127.0.0.1:8000/health                 # status ok + checks
curl -X POST http://127.0.0.1:8000/discover \
     -H "Content-Type: application/json" \
     -d '{"site":"thingiverse","count":3}'        # returns live trending models

# tests (offline, no keys needed)
.venv\Scripts\python.exe -m pytest tests/ -q      # expect: 16 passed

# stop: close the console window, or
taskkill //F //IM python.exe   # (kills ALL python — use carefully)
```

### Reading `/health`

```json
{"status":"ok","checks":{
  "image_editor": "local (no API key needed)",  // always available
  "browser_debug_port_up": false,  // false is fine until you publish; auto-launches then
  "downloads_dir": "...",          // where fetched models/images land
  "playwright_chromium": true      // must be true for discover/fetch
}}
```

## 4. End-to-end smoke test (manual, ~5 min)

1. Open http://localhost:8000 → **Find Top Models** → pick MakerWorld → *Find*.
   Expect a grid of trending models with thumbnails.
2. Copy a model URL from the grid → paste into **1 · Model URL** → **Fetch & Edit**.
   Expect: original vs edited (red, clean background) pairs, name + description filled
   in, and a checkbox under each edited image — **untick anything you don't want
   uploaded** (Select all / Select none buttons above the grid). No key required.
3. **Upload to Creality Cloud** → a Chrome window opens, fills the form, and stops
   before Submit (or submits; see §6). Screenshots land in `downloads/_shots/`.

## 5. Where things land

- `downloads/` — fetched images + model zips (one `slug` per model)
- `downloads/model_files/` — extracted model files ready to upload
- `downloads/_shots/before_submit.png` / `after_submit.png` — upload audit trail
- `edge_debug_profile/` — Edge profile with your logins (**never commit or delete**)

## 6. Safe-publishing mode (recommended for first runs)

Set env var `CREALITY_NO_SUBMIT=1` before starting the server to fill the whole
Creality form but stop before the final Submit — you click Submit yourself after
inspecting. Unset it when you trust the flow.

## 7. Known limits / gotchas

- **Printables file download** is login-gated; the uploader fetches model files via
  the logged-in Chrome tab, so log into Printables in that Chrome window too if you use it.
- **Editor fallback chain**: U2Net segmentation (free model, downloaded once to
  `~/.u2net`) → OpenCV GrabCut if rembg ever fails, so editing never hard-fails.
- **Duplicate policy**: Creality's originality check blocks re-publishing the same
  model; the uploader reports `duplicate: true` instead of retrying. Don't bypass it.
- Sites change their HTML often — if discover/fetch returns junk, the selectors in
  `discover.py` / `downloader.py` are the first place to look.
- Long-running fetches can take 30–90 s (headless render + Gemini edits). The UI
  shows a spinner; the server stays responsive during that time.

## 8. What was verified/fixed on 2026-09-12 (session 2)

- 🔓 **Removed the API-key requirement entirely.** `editor.py` is now 100% local:
  U2Net segmentation → morphological text erasure (inpaint) → luminance-mapped red
  recolor (keeps all shading) → studio-gradient background (removes all original
  background text/watermarks). Verified on real downloaded models: green product
  came out red, background corners ~235 light grey, ~2s/image, works offline.
- ✅ New dashboard image selection: per-image "Upload this image" checkboxes plus
  Select all / Select none; only ticked images are sent to `/upload`.
- ✅ `/health` no longer reports `google_api_key_set`; reports `image_editor: local`.
- ✅ New tests: editor runs with no key and stays red/light-bg; guard test bans
  cloud SDK imports from `editor.py` forever.
- ✅ 16/16 tests pass; live end-to-end fetch of a Thingiverse model edited all
  12 images locally with zero keys.

## 8a. Earlier session (same day)

- ✅ 16/16 offline smoke tests pass (`tests/test_smoke.py`)
- ✅ Dashboard serves on :8000; `/health` returns ok; live discover verified
- 🐞 Fixed: `/fetch` returned HTTP 200 with an empty payload on failed downloads —
  now returns a clear 400
- 🐞 Fixed: `/fetch` and `/health` blocked the event loop (froze the dashboard during
  long scrapes) — now run in worker threads via `asyncio.to_thread`
- 🔒 Hardened: dashboard escapes scraped names/URLs before injecting into HTML (XSS)
- 🔧 Housekeeping: `.gitignore` now excludes `.venv/`, `.pytest_cache/`,
  `chrome_debug_profile/`; `.env` template created
- 📝 Cosmetic: fixed broken `.status.warn` CSS colour in the dashboard

## 9. Suggested next steps

1. Add a `GET /jobs/{id}` pattern or WebSocket progress for fetch (better UX for
   60–90 s waits) — or at minimum a timeout guard per site.
2. Persist an audit log (CSV/SQLite) of every publish: source URL, name, timestamp,
   result — useful if Creality ever flags the account.
3. Rate-limit discovery/fetch per site and cache discover results for a few minutes
   to be polite to the source sites.
4. Add a CI workflow (GitHub Actions) running `pytest tests/ -q` on every push.
