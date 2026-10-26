# Creality Pipeline

A Python / FastAPI pipeline that turns 3D-print model pages (Thingiverse, Printables, MakerWorld) into published **Creality Cloud** listings.

![Creality Pipeline dashboard](screenshots/dashboard.png)

## What it does
1. **Discover** — research panel that lists the most popular models on a site (read-only; no download/upload).
2. **Fetch & Edit** — downloads a model's images and STL/CAD file, then **100% locally** (no API, no keys, free forever):
   - keeps the product's colors and detail EXACTLY as-is (blue stays blue, blue+green stay blue+green),
   - sharpens sources that were blurry (unsharp pass on product pixels only),
   - replaces the background with a **procedural wooden studio backdrop** (removes all original background text/watermarks),
   - refuses to save any composite that fails its built-in sharpness/color QA.
   You then tick exactly which edited images you want to upload.
3. **Publish** — drives your real Chrome (via CDP, `--remote-debugging-port=9222`) to fill the Creality Cloud upload form and submit.
4. **MakerWorld — its own dashboard at <http://127.0.0.1:8000/makerworld>** (added 2026-10-25)
   — the other platform, kept completely separate: its own page
   (`makerworld/index.html`, `MakerWorld Pipeline V…`), its own driver
   (`makerworld_uploader.py`), its own measured flow map
   (`MAKERWORLD-FLOW-CONFIG.md`), its own "already published" memory
   (`artifacts/makerworld_published.json`) and its own routes (`POST /upload/makerworld`,
   `GET /makerworld/files`, `GET|POST /makerworld/published|forget`, plus
   `POST /upload/makerworld/plan`, which tells you what MakerWorld would refuse without
   opening a browser). MakerWorld asks for two things Creality does not: **the photo slot wants
   at least 3 pictures**, and — for the remix path — a source design that already lives on
   MakerWorld.
   **Every picture a MakerWorld listing gets is drawn for it** (`photo_mode="generate"`, the
   default, changed 2026-10-26): `photo_gen.py` writes the pictures from that listing's own name
   and description through a free keyless endpoint — no API key, no account, no money. Neither
   the source listing's photographs nor your own are attached unless you deliberately switch the
   card to *as I shot them* / *tidy them*, and a set that comes back short is a 502 that
   publishes nothing instead of being padded with somebody else's photo. Both buttons settle
   their pictures before the browser opens, and the acknowledgement ticks ("these were drawn by
   a machine", "these are photographs of my print") are required by the routes, not just by the
   page.
   **Auto · Find and publish** is the headline control: type a number, tick that the run posts
   machine-drawn pictures, and it pulls that many designs off MakerWorld's licence-filtered
   listings, downloads each model, draws its own picture set, fills the wizard in your Chrome and
   publishes it, with no per-item approval (`POST /makerworld/auto/start`, `/stop`, `/status`).
   The listing filter is measured, not assumed: only Public Domain, BY and BY-SA are selectable,
   and `discover()` reads those three chips back off the rendered page and refuses to return a
   list if any other licence is green. An item still stops itself on a fact — a NoDerivatives or
   NonCommercial source, a page that names no licence (a public-domain source is refused: the
   remix form has no answer for it), no downloadable file, no category, or fewer than three
   pictures coming back from the drawer. Step 0 is the same listings as a browser, for when you
   want to pick one yourself. The Creality page at `/` publishes to Creality only; the two are
   joined by nothing but a link in the top bar.

## What you MUST provide before it will run
1. **Python 3.10+** installed on your machine.
2. **Google Chrome** browser installed (the upload step drives your real Chrome via CDP).
3. **One-time logins** in that Chrome window to **MakerWorld** and **Creality Cloud** (so the uploader can reuse your sessions).
4. **Playwright's Chromium** browser (downloaded below).

**No API keys. No accounts. No usage costs. Ever.** Image editing is fully local
(OpenCV + the open-source U2Net model via `rembg`, which is downloaded once and
then works offline forever).

## Step-by-step setup
```bash
# 1. Clone the repo
git clone https://github.com/hreyner7-collab/creality-pipeline.git
cd creality-pipeline

# 2. Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows  (use: source .venv/bin/activate on macOS/Linux)

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install the Playwright Chromium browser
playwright install chromium

# 5. Log in once (in Chrome) to MakerWorld and Creality Cloud
#    The uploader reuses these sessions from the Chrome debug profile.

# 6. Run the dashboard
python main.py
#    open http://localhost:8000 in your browser
```

## How to use the dashboard
1. **Find Top Models** (optional research) — pick a site, click *Find*, browse trending models.
2. **Model URL** — paste a Thingiverse / Printables / MakerWorld URL, click *Fetch & Edit*.
3. **Preview** — compare original vs edited (red, clean background) and **tick which images to upload** (Select all / Select none / per-image).
4. **Publish to Creality Cloud** — click *Upload to Creality Cloud* (drives Chrome, fills the form; log in if prompted, then confirm the final Submit).

## Never publishes the same model twice
Every model the pipeline publishes is written into a permanent ledger
(`artifacts/published_models.json`) and checked **before anything is uploaded**,
by hand or by a run — so the same model can never be published twice, in a later
run or after a restart. A model is recognised by any one of: its source link, its
Creality listing, its name + designer, or the model file's own fingerprint (the
same geometry re-listed under a new name and URL is still the same model). At
startup the ledger also reads your own Creality account and learns every model
already on it, plus every earlier run log on disk. It lives at
`artifacts/published_models.json` inside the project (not ignored by git), so it
survives restarts and updates; a run filters those models out DURING discovery,
so a finished model is not even looked at again. A model that FAILED stays
retryable; only successes are locked. `POST /published/forget {match}` is the one
way to make an entry publishable again (e.g. you deleted the listing) — it is
not on the dashboard on purpose, so nothing there can unlock a model by accident.

## Structure
- `main.py` — FastAPI app + dashboard routes
- `ledger.py` — permanent "already published" memory (never uploads twice)
  · `GET /published` lists it, `POST /published/forget {match}` unlocks one entry
- `discover.py` — trending-model research scraper
- `downloader.py` — fetches images + model files from source sites
- `editor.py` — fully local editor: text removal, red recolor, studio background
- `uploader.py` — Creality Cloud upload via Chrome CDP
- `makerworld_uploader.py` — MakerWorld upload via the same Chrome CDP (separate driver)
- `makerworld_flow.py` — MakerWorld's measured field names, limits and licence plan
- `photo_gen.py` — the keyless listing-picture drawer MakerWorld uses (retries the free tier's
  refusals, checks that what came back is a photograph, and cuts the generator's corner mark)
- `makerworld_ledger.py` — permanent "already on MakerWorld" memory (separate file)
- `makerworld/index.html` — the MakerWorld dashboard, served at `/makerworld` (its own page)
- `MAKERWORLD-FLOW-CONFIG.md` · `GATES-MAKERWORLD.md` — the measured flow map and its gates
- `creality/index.html` — dashboard UI
- `PLAN.md` — runbook + verification plan

## Notes
- The upload step reuses your logged-in MakerWorld / Creality sessions from the Chrome debug profile, so log in once in that window.
- Only repost models you have the rights to, with credit to the original designer.
- Set `CREALITY_NO_SUBMIT=1` to fill the form but stop before the final Submit.
