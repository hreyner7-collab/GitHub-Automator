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
   **A remix's pictures are the source's own model with only the backdrop rebuilt**
   (`photo_mode="same_model"`, what the page's default option and the auto run both use since
   2026-10-26): `editor.py` keeps the model's exact pixels from the source listing's photograph,
   pastes them onto a fresh sweep, and `photo_critic.py` then measures every cut against that
   photograph on eight numbers — model present, shape overlap, backdrop actually moved, backdrop
   left clean, colour kept, coverage, no words, no leftovers. A failing number names its own
   remedy (re-mask, move to a different sweep, or drop the picture), and a retry always moves the
   backdrop, because the finisher is deterministic so the same sweep twice is a no-op. Measured
   over 14 real listing photographs, **6 come out right**; five of the missing eight are refused by
   the finisher itself before the panel sees a candidate. A listing that cannot reach three
   surviving pictures stops with `photos-failed` rather than padding itself with somebody else's
   photo. `photo_gen.py` (the keyless machine-drawn set, no API key, no account, no money) is still
   there as the `generate` mode, and `generate` stays that field's *fail-closed default*: a caller
   that names no mode must land on the one mode that cannot attach a picture nobody here made. The
   own-photo modes (`as_shot`, `restyle`) require the "photographs of my print" tick, and neither a
   moved copy of a scraped gallery file nor an `ai_` studio drawing can pass for one.
   **Auto · Find and publish** is the headline control: type a number, tick that the run uses the
   source's own model, and it pulls that many designs off MakerWorld's licence-filtered listings,
   downloads each model, cuts each source photograph onto a clean backdrop and has the panel judge
   every cut, fills the wizard in your Chrome and
   publishes it, with no per-item approval (`POST /makerworld/auto/start`, `/stop`, `/status`).
   The listing filter is measured, not assumed: only Public Domain, BY and BY-SA are selectable,
   and `discover()` reads those three chips back off the rendered page and refuses to return a
   list if any other licence is green. An item still stops itself on a fact — a NoDerivatives or
   NonCommercial source, a page that names no licence (a public-domain source is refused: the
   remix form has no answer for it), no downloadable file, no category, or fewer than three
   pictures coming back from the drawer. Step 0 is the same listings as a browser, for when you
   want to pick one yourself. The Creality page at `/` publishes to Creality only; the two are
   joined by nothing but a link in the top bar.

## Picking this up (state as of 2026-10-26)

Everything lives in this one folder, branch `main`. Python runs from the repo-local venv —
`./.venv/Scripts/python.exe -X utf8 …` — because there is no system python on this machine.

Read in this order:
0. `HANDOFF-MAKERWORLD.md` — if you are picking up the **MakerWorld** pipeline only: its own file
   list, routes, the eight critics with their thresholds, the checks, the measured yield and what
   is still open. The Creality half is explicitly out of its scope. Since 2026-10-27 there is also a
   MakerWorld-only copy of that pipeline at `C:\Users\henry\Documents\free\app\MakerWorld` (12
   routes, all MakerWorld; its own `README.md` says what could not travel with it). This repo stays
   the one that runs at `/makerworld` and the one pushed here.
1. `MAKERWORLD-FLOW-CONFIG.md` — what MakerWorld's form actually does, the eight critics with
   their measured thresholds and the control numbers behind each, and the pass-by-pass picture
   yield.
2. `GATES-MAKERWORLD.md` — the MakerWorld acceptance gates (G1…G15). Every gate is the exact
   command, an `EXPECT:` line, and the measured evidence from the run that satisfied it.
3. `AGENTS.md` — the standing rules: do not open a browser, publish or restart the server unless
   the user asks right then; stage files by name (never `git add -A`); bump the dashboard version
   marker on every page change; commit and push before calling work done.

The code that decides the pictures: `editor.py` (the cut itself), `photo_critic.py` (the eight
measured checks plus the retry that moves the backdrop), `makerworld_flow.py` (discovery, the
photo rules, the ledger), `makerworld_uploader.py` (the wizard), `main.py` (the routes),
`makerworld/index.html` (the page, currently `V12`).

The checks:
```bash
./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_makerworld.py -q   # 92 passed
./.venv/Scripts/python.exe -X utf8 -m pytest -q                            # 620 passed
```

Open, and blocked on the user rather than on code:
- `python main.py` has no auto-reload, so the running dashboard still serves the page from
  before `V12` until it is restarted — which needs an explicit ask.
- Nothing has been published through `same_model` yet. That is live browser work, so it waits for
  the user to say so.
- The honest yield is 6 of 14 source photographs surviving the panel. It will not reach 100%, and
  no change should be written as if it had.
- Root `GATES.md`, `bravia/`, `image_api/`, `game.html`, `nul`, `test*.png` and the
  `scripts/check_mw_*.py` probes on disk are leftovers from other work; they are untracked and
  not part of the MakerWorld pipeline. The tracked gate files are `GATES-MAKERWORLD.md`,
  `GATES-GENERATE.md` and `GATES-REUPLOAD.md`.

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
git clone https://github.com/hreyner7-collab/GitHub-Automator.git
cd GitHub-Automator

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
