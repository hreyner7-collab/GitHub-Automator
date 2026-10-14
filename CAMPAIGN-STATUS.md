# One-hour verification campaign — 2026-10-08, ~17:15-18:20 (dashboard on 127.0.0.1:8000, PID 17024)

Every control was exercised against the **live** app and the **real logged-in Chrome**
(CDP 9222), not against mocks. Evidence: `debug/inspector_step2.json`,
`debug/inspector_form.png`, `logs/dashboard.log`, `artifacts/published_models.json`,
83 tests, two offline gates. Real publishes were never triggered.

## A. Dashboard controls (as the user clicks them)

| # | Control | What it did | Verdict |
|---|---------|-------------|---------|
| 1 | `GET /` (page load) | 200, text/html, cards = Analytics · Auto · Research · 1 Model URL · 2 Preview · 3 Publish; no ledger panel; inline script parses (`check_page_syntax.py`) | WORKS |
| 2 | Analytics → **Refresh now** | `POST /analytics/refresh` — drives the logged-in tab, reads the account's own model list; the startup seed grew the config to 24 models from it | WORKS |
| 3 | Analytics → **Sign in to Creality Cloud** | `POST /analytics/signin` opens Creality's own sign-in page in the debug Chrome; not exercised (already signed in, nothing to prove) | NOT RUN |
| 4 | Analytics → 7D / 30D / Year | `GET /analytics/series?days=…` returns the step-interpolated series | WORKS |
| 5 | Analytics cards/points | `GET /analytics/summary` → totals, per-period deltas, points | WORKS |
| 6 | Analytics → per-model downloads | `POST /analytics/fetch-downloads` exists but was not run (each of ~22 model pages takes 5-10 s) | NOT RUN (time) |
| 7 | Analytics toggle button | client-side show/hide only | WORKS |
| 8 | Auto → **Find & publish** | `POST /auto/start`; count is validated: 0/51/-3 → HTTP 400 with the message; a valid count would publish for real so no full run was started (the user tests that) | VALIDATED, RUN NOT STARTED |
| 9 | Auto → **Stop** | `POST /auto/stop` sets the stop flag; nothing running to stop | NOT RUN (nothing to stop) |
| 10 | Auto → run list + licence line | `GET /auto/status` → `state/items/counts/licence_allow/licence_deny/skipped_already/already_done` all present | WORKS |
| 11 | Research → **Find** | `POST /discover` (headless MakerWorld/Printables/Thingiverse) not run — those sources are no longer used by the pipeline; unchanged code | NOT RUN |
| 12 | **1 · Model URL → Fetch from Creality Cloud** | `POST /fetch` on `…/model-detail/scale-model-pallet-jack` → **200 in 18 s**: name «Домкрат для подъема поддонов 1/10-го масштаба», author, licence **CC BY-NC-SA** with 3 pieces of evidence, form answers `['ShareAlike','No','No']`, badge CC BY-NC-SA, 3MF downloaded, gallery strip 1 = 1 photo (`matches_strip: true`) | WORKS (this is the very model that used to be refused as "not licensed" and whose Cyrillic name used to lose its photos) |
| 13 | **2 · Preview** tiles | `image_urls` served via `/file/…`; the fetched photo renders (1 tile) | WORKS |
| 14 | Preview → **Regenerate photos (AI re-edit)** | not run this pass (local editor, no site interaction; covered by the local editor gates) | NOT RUN |
| 15 | Preview → Select all / none / per-tile ticks | client-side selection only | NOT RUN (unchanged) |
| 16 | **3 · Publish → Upload to Creality Cloud** | `POST /upload` — see the ledger finding below: for this model it correctly returned **HTTP 409 "Refusing to publish twice"** | WORKS (as designed) |
| 17 | Duplicate protection | `GET /published` → 24 models locked; `POST /upload` for a locked model → 409 with the date and the matched fingerprint | WORKS |
| 18 | `GET /health`, `GET /file/{name}` | ok / serves only from `downloads/` | WORKS |

## B. Creality tabs the job drives (live inspector, CDP)

| # | Target | Finding |
|---|--------|---------|
| 19 | `create-model-new` → step 1 "Upload Files" | The page opens on **step 1**; the whole step-2 form (Model Source, licence, covers) exists in the DOM but is **hidden and unlaid-out** (every element 0×0) until a file is attached and `div.step-one-btn.submit` is clicked. My first probe read "all radios invisible" — that was the probe's fault, not the app's. `debug/inspector_form.png` shows step 1. |
| 20 | Step 1 → file attach + Next | Attaching a real 8.5 MB 3MF and clicking `.step-one-btn.submit` **does** advance to "Fill in the information" (verified twice) |
| 21 | Step 2 control map | Radios visible with text (Original / **Remix Models** / Non-original, Free/Paid, licence Yes/No ×3, Public/Private); inputs: name, category, tags, description; 7 image inputs (`accept=".jpeg,.png,.jpg,.webp,.gif"`); buttons incl. `Submit`; the blocker toast text *"Please check the box to agree before submitting"* is present on the page (the string the false "rejected (agree to)" used to match — now correctly treated as a blocker) |
| 22 | Step 2 → uploader's selectors | `input[placeholder*='model name' i]` ×1, `textarea` ×1, `input[placeholder*='Paste the URL' i]` ×1, image inputs ×7 — every selector the uploader uses still resolves |
| 23 | **Model Source picker** (the user's complaint) | Live rows confirm the reader: 10 rows for a search; a No-Derivatives row carries **`class="origin-item flex-center disabled-item"`** *and* the red sentence (colour test matched: r≥130, g≤115, b≤115). Containers: `origin-choose > origin-choose-mark` (the registered source) `> origin-choose-list > origin-item`. Rows carry **no link**, so scoring must use names — and rows sit in two columns (x≈514 / x≈980), which the reader handles |
| 24 | **Picker: does the right model even exist in the list?** | Query `"Домкрат для подъема поддонов 1/10-го масштаба"` (the source's own title) → the source is **row 1, unflagged**, and `pick_source_suggestion` chooses it with **score 100**. Query `"scale model pallet jack"` (the old slug-word query) → the source is **absent**, the list holds 3 disabled rows, and the chooser correctly chooses **NOTHING** — so the wrong "Scale model forklift" can no longer be clicked |
| 25 | Registered-source reader | New `_registered_mark()` reads `.origin-choose-mark` (text + link): registered → text+href, empty → nothing. Unit-checked in the gate |
| 26 | Licence filter on the listing | Not re-run this pass (last verified 2026-10-08 in R9; unchanged code) | NOT RUN |
| 27 | Discovery skips already-done models | `find_creality(..., is_done=…)` filters during harvest; main passes `ledger.find`; 83 tests pass incl. the two new ones | WORKS (unit-level; not observed in a real run) |

## C. Defects found and FIXED in this pass

1. **A dry run / a `CREALITY_NO_SUBMIT` form fill was recorded as a published model** —
   `POST /upload?dry_run=true` returns `success: true` (files acquired, form untouched) and
   `CREALITY_NO_SUBMIT=1` returns `success: true` too; `main.py` recorded both in the
   already-published config. Consequence seen live: the pallet-jack model I only *fetched*
   became "published" (entry dated 17:42) and would have been refused forever.
   Fixed: `published_now` requires `success` **and** no `dry_run`/`no_submit` marker (the
   no-submit return now carries `no_submit: true`); the false entry was forgotten (24 models
   locked, all real). Test: `test_a_dry_run_or_a_no_submit_fill_is_never_recorded_as_published`.
2. **`dry_run` does not exercise the form** — it stops after file acquisition, so it cannot
   verify covers/gallery/source/licence. `CREALITY_NO_SUBMIT=1` is the real "fill but don't
   publish" switch. Documented here; README should say so (prompt 2 below).
3. **The Remix picker's leftover covered the footer and absorbed the first click** (closed in
   this pass, prompt 9). Measured live: after the source was registered, `elementFromPoint()` at
   the consent circle returned `div.origin-choose-mark` — the picker's registered-source card —
   so the first tick click hit it and only the retry landed (`tried: [["round1:circle", false],
   ["round2:circle", true]]`); the same leftover sat over the footer when the 17:03 run pressed
   Submit and Creality never confirmed it. Fixed: `_clear_picker_overlay()` + `_topmost_at()` in
   `uploader.py` (~lines 1489-1560) dismiss the leftover (Escape, then the one real click the
   live form itself took — always at the consent point, never on a suggestion row) and verify
   with `elementFromPoint` that BOTH footer regions — the consent circle and the Submit button —
   are reachable before the tick; step 10 runs the same guard before pressing Submit.

4. **The run's accounting hid fetch failures inside "skipped"** (closed in this pass, prompt 11).
   `published: 2, skipped: 4, failed: 0` reported four never-published models as skips. Fixed with
   the `fetch-failed` state + `fetch_failed` count and the panel pill/status lines; Stop now also
   cuts a fetch backoff instead of sitting it out.
5. **The user's own run's four failures were all failure-HANDLING, not models** (closed in
   this pass, prompt 10). Models 7 and 8 were written off with `Page.evaluate: Execution
   context was destroyed, most likely because of a navigation` and `Page.goto:
   net::ERR_INTERNET_DISCONNECTED` — both transient, both simply skipped; models 5 and 6 were
   carried all the way to the publisher and only died there with `No edited images for this
   model. Run Fetch & Edit first so the red images are generated`. Fixed: `_fetch_with_retries()`
   retries a navigation-destroyed context / temporary network drop up to `FETCH_ATTEMPTS` (3)
   times (`FETCH_RETRY_DELAY` 4 s between tries, both patchable) and records the attempt history
   on the item; a fetch that came back with no cover photo **on disk for this model** is reported
   as a fetch failure (`usable_cover_images()` / `NO_COVERS_ERROR`, shared with the publisher)
   before the publisher is ever called, so the run moves to the next candidate.

## C2. The agreement box ("I have read and agree to Copyright Statement.") — proven live

The reported failure ("Creality rejected the submission (agree to)") and the earlier "no row
matching the finder's text at all" are one and the same defect. It was reproduced, explained and
fixed on the real form, and nothing was published.

How it was proved: `uploader.upload()` itself, on a licence-clean model that is not in
`published_models.json` (`scale-model-pallet-jack`, CC BY-NC-SA), with `CREALITY_NO_SUBMIT=1` —
attach the 3MF → Next (step 1→2) → name → photos (web cover + gallery) → Auto-Generated →
description → Model Source (Remix Models; picked and registered
`Домкрат для подъема поддонов 1/10-го масштаба`) → licence answers `[2,1,1]` read back with badge
`CC BY-NC-SA` → step 9's own finder. Scripts: `scripts/consent_fill.py` (fill),
`scripts/consent_probe.py` (live DOM + real-click probe), `scripts/consent_fill_diag.py`
(same fill with `CONSENT_*` wrapped so each step-9 call logs the DOM). Evidence:
`debug/consent/upload_result.json`, `debug/consent/consent_probe.json`,
`debug/consent/consent_diag.json`, `debug/consent/unticked_dom.json`,
`debug/consent/probe_footer.png`.

What the inspector shows on the live page:

* **The row exists on this page build.** 7 elements match the phrase; the real row is
  `label.el-checkbox` → `span.el-checkbox__input` (the 16×16 circle) →
  `input.el-checkbox__original[type=checkbox]` + `span.el-checkbox__inner`, the text in
  `span.el-checkbox__label` ("I have read and agree to " + `<strong>Copyright Statement.</strong>`).
* **The defect.** While the box is UNTICKED — exactly when the finder must act — the warning
  `div.tips > span "Please check the box to agree before submitting"` is a CHILD of that label, so
every matching element measures **93 chars** ("…Copyright Statement. Please check the box to
  agree before submitting"); the old finder's `textFor(e).length < 90` cap therefore filtered out
  every match: `found: false`, no candidates, nothing clicked. The real fill's log shows
  `under90=0` on every step-9 call; once a fallback click happened to tick the box, the warning
  disappeared, the text fell to 45 chars and the same finder read `under90=5` → `checked: true`.
* **The real click target works.** A real mouse click at the circle's centre (16×16 at 286,695)
  toggles the row's own checkbox: `input_checked true → false → true`, the row class going
  `el-checkbox` ⇄ `el-checkbox is-checked` (`ticks` in `debug/consent/consent_probe.json`).
* **What Creality says.** Unticked → the footer carries "Please check the box to agree before
  submitting" (visible). Ticked → that warning is gone and the form shows the green tick and the
  green Submit (`debug/consent/probe_footer.png`).

Fix, in `uploader.py` step 9 and the consent JS block (~lines 636-760 and `_tick_consent`
~1447-1560): the finder now matches the live DOM — it finds the row by structure
(`label.el-checkbox` carrying the phrase, with a text-only fallback for other builds and no cap
the warning text can push out), reads the tick from the row's own checkbox input plus
`is-checked`, and clicks the visible circle with a REAL mouse click, re-measuring and retrying
(bounded) with the state read back from the input.

Re-checked live after the change (`debug/consent/upload_result.json`):

```
consent: {"found": true, "row": "I have read and agree to Copyright Statement. Please check
          the box to agree before submitting", "row_cls": "el-checkbox",
          "input_html": "<input class=\"el-checkbox__original\" type=\"checkbox\">",
          "points": {"circle": "el-checkbox__input", "label": "el-checkbox"},
          "tried": [["round1:circle", false], ["round2:circle", true]], "checked": true}
copyright_agreed: True
```

Suite after the change: **83 passed**. Nothing was submitted; nothing was recorded in the
config.

## C3. The authorised verification publish (2026-10-08, 19:14) — ran, did NOT publish

One publish was authorised: type 1 in the dashboard's own card and press **Find & publish** (a real
click in the dashboard, job `20261008-191417`). Result, verbatim from the run and the live form:

* **Model chosen**: «Домкрат для подъема поддонов 1/10-го масштаба»
  (`https://www.crealitycloud.com/model-detail/scale-model-pallet-jack`), keyword `household items`,
  licence **CC BY-NC-SA** with evidence `["the creativecommons.org link", "the licence named in the
  card (CC BY-NC-SA)", "the CC grant paragraph"]`; the licence filter was verified first
  (`ticked_on` = CC BY, CC BY-NC, CC BY-NC-ND, CC BY-NC-SA, CC BY-SA, CC0; `ticked_off` = CC BY-ND,
  Standard License, `applied_with: Confirm`), and 4 already-published models were skipped before
  queueing. The ledger did **not** contain this model, so it was not a duplicate refusal.
* **Fetch**: produced **1 photo** — `downloads/домкрат_для_подъема_поддонов_1_10-го_мас_img_0.webp`
  (48,240 bytes) — and the real 3MF. The form received that photo in the **Web cover 4:3** slot
  (`downloads/_shots/before_submit.png`); the **App cover 3:4** and the “3:4 cover image displays
  larger area in App” slots stayed **empty**, although `uploader.image_plan()` returns `app = web`
  when there is only one photo (uploader.py ~825-855) — see prompt 12.
* **Agreement + picker**: the pre-Submit screenshot shows the circle **ticked** (“I have read and
  agree to Copyright Statement.”, green) with Submit enabled, and the Model Source card registered
  for the source. Creality's own agreement blocker (“please check the box to agree before
  submitting”) never appeared. NOTE: the first-click proof the prompt 9/10 passes produced
  (`tried: [["round1:circle", true]]`) is **not** quotable for this run — the watchdog written for
  it never attached (Playwright's `connect_over_cdp` did not expose the tab the pipeline created
  afterwards; replaced by the raw-CDP `scripts/read_live_form.py`, which read the form fine).
* **What Creality said after Submit**: **nothing** — no toast, no `.el-message`, no text matching
  please/required/agree/error anywhere in the DOM (`debug/live_form_after_failure.json`:
  `creality_says: []`, `creality_hints: []`), and no message in the after-Submit screenshot. The
  only signal is the form itself: the wizard **went back to step 1**, the “Model files” field in
  Creality's **error state** (`class="content-box model-file-content err-box"`, border
  `rgb(255, 51, 51)`), step badge 1 active (green), and the 3MF row showing a broken preview with
  `input.files` empty. Nothing was created.
* **Ledger**: **not recorded** — `artifacts/published_models.json` unchanged (sha256
  `64017a21eee601a3`, 24 entries, no pallet-jack). The run logged `counts: {published: 0, failed: 1}`
  and the reason `Submit was pressed but Creality never confirmed the submission … Nothing was
  published`. No second model was run, per the authorisation.

## C4. The second authorised publish (2026-10-08, 19:26) — **PUBLISHED**

Prompt 12 implemented, then one more authorised attempt, driven through the dashboard's own cards
(the fetch card, then the publish card) so exactly one model could be published — no run, no loop
onto others:

* pasted `…/model-detail/scale-model-pallet-jack`, **Fetch from Creality Cloud** → «Домкрат для
  подъема поддонов 1/10-го масштаба» by Крилл Карилов, **1 of 1** creator photo, gallery
  `1 of 1 — no adverts`;
* **Upload to Creality Cloud** → the form navigated to
  `https://www.crealitycloud.com/model-detail/6ac7e100f5ed98d3279fc843` (Creality's own success
  signal) and the dashboard read back: *“✓ Published … web cover
  домкрат_для_подъема_поддонов_1_10-го_мас_img_0.webp, **app cover
  домкрат_для_подъема_поддонов_1_10-го_мас_img_0.webp**, 1 in the gallery”*, licence read back
  `badge CCBY-NC-SA / needs CC BY-NC-SA · answers [2,1,1] / wanted [2,1,1] ✔`, photos
  `gallery 1 sent, form counter 0 → 1 ✔`;
* **live form watch** (`scripts/read_live_form.py --watch`, read-only raw CDP, timeline in
  `debug/live_form_watch.json`): `19:29:13` the picker's card sat over the consent circle
  (`topmost@circle = div.origin-choose-mark`, unchecked) → `19:29:17` `checked=True` with
  `topmost@circle = span.el-checkbox__inner` — the overlay-clearing fix made ROUND 1 the click that
  ticked → `19:29:19` `div.el-loading-mask` (Submit pressed) → the tab on model-detail;
* **ledger**: `artifacts/published_models.json` grew from 24 to **25** entries at `19:29:20`:
  `slug scale-model-pallet-jack`, `published_url …/model-detail/6ac7e100f5ed98d3279fc843`,
  `outcome published`, `note "published by hand from the dashboard"`, file fingerprint
  `sha256:9d1e5a862fb8deac…:236158`; `GET /published` reports **25 models**;
* **the listing is public**: `…/model-detail/6ac7e100f5ed98d3279fc843` redirects to
  `…/model-detail/scale-model-pallet-jack-1-10`, titled *“1/10 Scale Pallet Jack STL & 3MF File for
  3D Printing | Board Games & Card Games | Creality Cloud”*, listed `2026-10-08` (read unsigned-out
  in the browser).

What changed between the 19:14 attempt (rejected, empty app cover) and this one: the app-cover 3:4
slot is now filled with the same photo when only one exists (prompt 12c), and the Submit path
captures the create request + the form's error state (prompt 12a/b). The 19:14 bounce flagged
“Model files” on step 1 — the only *fill* difference in this attempt was the app cover, so the
empty required cover is the likeliest cause; that is correlation, not proof, and 12a/b are what will
name it if it happens again.

## C5. The third authorised publish (2026-10-08, 19:58) — **PUBLISHED, and the photo path is now proven with six photos**

The user's oldest complaint is *photos going missing on the upload*, and every publish so far had carried
only ONE photo, so the gallery, the de-duplication and the form's own counter had never been exercised.
This pass drove the dashboard's own two cards (1 · Model URL → **Fetch from Creality Cloud**, then
3 · Publish → **Upload to Creality Cloud**) on exactly one model, chosen because its card carries several
photos. Nothing was looped over, nothing was published twice.

**Before the run:** the dashboard process had been started at 17:46, i.e. BEFORE the last edits to
`uploader.py` (19:35) — so it was serving stale code. It was restarted (now PID 5536, `GET /` 200) so
this run tested the current code. The restart's own startup seed added one entry the account already
had (`scale-model-pallet-jack-1-10`, the published slug of the 19:29 pallet jack), so the ledger went
**25 → 26 by seeding, not by publishing**; baseline for this publish: 26 entries, sha256
`a6afcd328f65a0b2…`, neither `dumpling-squishy-clicker` nor `gecko-divertido-flexy` present.

**How the model was chosen** (read-only, logged-in Chrome; new `scripts/find_multiphoto_model.py`):
the listing gave 40 candidate links and 24 were inspected — each model's own licence card read with the
pipeline's `LICENSE_JS` and its photos counted with the same rules `downloader._fetch_creality` uses. The
selection scan (run before the publish) found only **two** licence-clean models with 4+ photos:
`dumpling-squishy-clicker` (6 photos, strip 6, **CC0**) and `gecko-divertido-flexy` (6 photos, strip 9,
CC BY); the CC0 one was taken. The same command was re-run after the publish so a full artifact is on disk
(`debug/multiphoto_candidates.json`, `ran_at 2026-10-08 20:26:07`: 40 candidates, 24 checked, 1 good =
`gecko-divertido-flexy`) — it records `dumpling-squishy-clicker` under `skipped_already_published`, an
independent second confirmation that the ledger holds the published model. The earlier selection run's own
JSON was overwritten by a one-page smoke test of the same tool, so the selection numbers above are quoted
from that run's console transcript and the artifact on disk is the re-run.

**Source card vs fetch** (`scripts/inspect_candidate.py` → `debug/candidate_inspection.json`; the fetch
itself through the dashboard):

* the source card claimed **6** creator photos (`{"photos": 6, "strip_thumbs": 6}`) and the fetch produced
  **6** — `dumpling_squishy_clicker_img_0…5.webp` — plus the real 3MF (`downloads/mantı+alt.3mf`, 2,071,404 B);
* the six files are **six distinct photos** (six distinct sha256), so `dedupe_images()` had nothing to drop:
  `image_plan()` returned `all=6 web=img_0 app=img_1 gallery=6 converted=[] dropped=0`;
* the dashboard read back, verbatim: *“Downloaded **dumpling squishy clicker** by mustafayalçınid · using
  original photos · **6 of 6 image(s) selected for upload** — they go up in THIS order: first photo → web
  cover, second → app cover, all of them → the Model Images gallery. **Gallery: 6 of 6 creator photo(s) —
  no adverts.**”*, licence *“CC0 / public domain — no attribution required (still credited). Creality form →
  adaptations: Yes · commercial: Yes · sharing: Yes — the badge on the form must read CC0”*.

**What the form received** — read-only raw-CDP watch of the live form every 1.5 s
(`scripts/read_live_form.py --watch`, now also logging the form's own counter; timeline
`debug/live_form_watch_multiphoto.json`, 23 samples / 12 changes):

| time | form's own gallery counter | previews | consent circle | topmost at the circle |
|------|---------------------------|----------|----------------|------------------------|
| 19:58:25 | **0**/9 (just rendered) | 0 img + 1 bg | unchecked | `div.el-overlay-dialog` (cover crop modal) |
| 19:58:27 | 0/9 | 2 img + 2 bg | unchecked | `span.el-checkbox__inner` |
| 19:58:28 | 0/9 | 4 img + 4 bg | unchecked | `span.el-checkbox__inner` |
| **19:58:30** | **6**/9 | 4 img + 10 bg | unchecked | `span.el-checkbox__inner` |
| 19:58:39 | 6/9 | 4 img + 7 bg | unchecked | `div.origin-choose-mark` (the Remix card) |
| 19:58:43 | 6/9 | 4 img + 7 bg | **CHECKED (round 1)** | `span.el-checkbox__inner` |
| 19:58:45 | 6/9 | 4 img + 7 bg | checked | `div.el-loading-mask` (Submit) |

The counter climbed **0 → 6** in one attach pass (so the retry branch for a short gallery never fired), and
the six photos landed while the covers' crop modals were being confirmed. NOTE: the watcher's per-input
`FileList` read **0 files in every sample** — this form clears each `input` once its upload completes, so
that field cannot show which file went to which slot; the slot evidence below is the listing's own stored
fields, not the input elements.

**What each slot actually received** — from the PUBLISHED listing, not from the pipeline's own report.
Its Nuxt record (`scripts/dump_listing_record.py` → `debug/published_listing_record.json`) stores the slots
by name:

* `pcCovers[0]` = `/crealityCloud/upload/839b7dc1….webp` — **1221×916, ratio 1.33 = exactly 4:3**, and the
  same photo as source `img_0` (958,710 B → 952,022 B, i.e. re-encoded by Creality, not replaced). This is
  also the listing's `og:image`. → **the web cover 4:3 slot received img_0**, the photo the plan named;
* `appCovers[0]` = `/crealityCloud/upload/0c0caf2b….webp` — **687×916, ratio 0.75 = exactly 3:4**, and the
  same shot as source `img_1` (visually identical, cropped to the required 3:4 by the form's own crop
  modal). → **the app cover 3:4 slot received img_1**, the photo the plan named — and this is the slot that
  was EMPTY on the 19:14 pallet-jack attempt;
* `covers[0..5]` = the **six** gallery photos, `editCovers[0..5]` the same six (`covers` has a 7th entry —
  a `/model/….png` platform asset the page never renders; `/model/`, not `/comp/model/`, is the
  promo/placeholder path the fetcher also rejects);
* `modelOrigin[0]` = the remix credit (`url` = the source listing, `pic` = the source's own cover).

**Nothing went missing — proven byte-for-byte.** All seven published images were downloaded
(`debug/published_photos/`, `debug/photo_path_sheet.jpg` is the visual sheet) and hashed:

| published image | bytes | byte-identical source photo |
|-----------------|-------|------------------------------|
| `covers[0]` db856ad7 | 55,566 | `img_1` |
| `covers[1]` 786f5f2b | 54,556 | `img_2` |
| `covers[2]` 5b8cb8b5 | 9,794 | `img_3` |
| `covers[3]` c446a9bd | 26,424 | `img_4` |
| `covers[4]` e0ffbc47 | 958,710 | `img_0` |
| `covers[5]` 90ec4732 | 63,858 | `img_5` |
| `pcCovers[0]` 839b7dc1 | 952,022 | `img_0`, 4:3 re-encode (the cover) |
| `appCovers[0]` 0c0caf2b | 384,256 | `img_1`, 3:4 crop (the app cover) |

So **6 of 6** source photos are on the published listing, byte-identical, plus the two cover renditions.
One honest caveat: the listing's rendered strip shows 7 images (the cover leads it, and the gallery holds
all six), and the strip's DOM order is a Swiper carousel (`div.swiper-slide`), so it is **not** read as an
insertion order — the stored `covers[]` order is what was compared above.

**Licence + Creality's answer:** the dashboard read back *“Licence read back: badge **CC0** / needs **CC0** ·
answers **[0, 0, 0]** / wanted **[0, 0, 0]** ✔”* and *“Photos: web cover dumpling_squishy_clicker_img_0.webp
· app cover dumpling_squishy_clicker_img_1.webp · **gallery 6 sent, form counter 0 → 6** ✔”*, then
*“✓ Published to your Creality Cloud account with 6 photo(s) in the creator's order … Check 'My Designs'.”*
with `url = https://www.crealitycloud.com/model-detail/6ac7e7e6f5ed98d327a0425f`. Creality said nothing in the DOM
because it did not reject anything: the form left `/create-model?source=12` and landed on the model-detail
page — its own success signal. The listing is public and redirects to
`…/model-detail/dumpling-squishy-game` (“dumpling squishy clicker STL & 3MF File for 3D Printing”).
LIMITATION: on a success the pipeline keeps no create-request capture (`net_hits` is only attached to
`UPLOAD_VERIFY["submit_rejection"]` on failure), so “what Creality said” is proven by the redirect plus the
listing's own stored fields, not by the create response's status.

**The ledger stayed clean.** 26 → **27** entries, exactly one added, at `19:58:46` (a later pass folded one unrelated duplicate row and the ledger now holds 26 models — see C6):
`slug dumpling-squishy-clicker`, `published_url …/6ac7e7e6f5ed98d327a0425f`, `outcome published`,
`note "published by hand from the dashboard"`; `GET /published` → **27**. The other candidate
(`gecko-divertido-flexy`) is still absent from the ledger — it was never fetched, queued or published.

**No fix prompt follows from this pass**: photos did not go missing on the upload, so there is nothing to
repair here. The offline readers written for it are `scripts/find_multiphoto_model.py`,
`scripts/inspect_candidate.py`, `scripts/read_published_listing.py`, `scripts/read_listing_structure.py`,
`scripts/dump_listing_record.py` (all read-only; each writes its evidence under `debug/`). Suite after this
pass: **90 passed** (`pytest tests/ -q`, exit 0) — and the de-duplication's own drop branch stays covered by
`test_photo_plan_keeps_order_and_uses_each_photo_once`, which feeds `image_plan()` a byte-identical copy and
requires it to be dropped (live, the source had no duplicate photo, so nothing had to be dropped — the fact
that all six distinct photos survived is what proves the filter did not over-drop).

## C6. The ledger duplicate the restart created — found and repaired (2026-10-08, 20:50)

The guarantee this pipeline is built around is "never publish the same model twice", and the last
pass found it damaged in the safest-looking place: the ledger itself. The 19:49 restart's account seed
added a SECOND row for the pallet jack — once under its source slug (`scale-model-pallet-jack`) and
once under its published slug (`scale-model-pallet-jack-1-10`) — so the ledger counted **27 rows for 26
models**.

**Root cause.** Matching read only `fingerprints{url, slug, name, author, file}`; an entry's own
`source_url` / `published_url` were never identity at all. Creality answers a publish with an opaque
`/model-detail/<id>` (the URL the tab lands on) and the account's own list uses the readable
`/model-detail/<slug>` the same link **301s** to — measured live:
`curl -D- …/model-detail/6ac7e100f5ed98d3279fc843` → `HTTP/1.1 301 Moved Permanently`,
`location: /model-detail/scale-model-pallet-jack-1-10`. The row knew the opaque id; the seed brought
the readable slug; the names differ by language («Домкрат для подъема поддонов 1/10-го масштаба» vs
"1/10 Scale Pallet Jack"), so nothing matched and a second row was written.

**Fix** ([ledger.py](ledger.py)): identity is now a SET of links and slugs per entry (source link,
published link, every alias, their slugs) and a lookup matches on any equal link, any equal slug
(≥ 8 chars, **never a prefix** — `organizer-box` and `xl-organizer-box` stay different models), the
file digest, or the name+designer (with `also_named` titles counting, so a merge widens recognition
rather than narrowing it). `record()` merges on that rule; `canonical_url()` follows Creality's 301
without a browser; `main._record_published()` stores every spelling at publish time (both the manual
card and an auto run), so this class of duplicate cannot recur; `learn_published_aliases()` and
`absorb_published_redirect()` give the seed the same link when it meets an older row;
`merge_duplicates()` folds any surviving pair into the earliest row; `startup_seed()` folds before it
reads the account, so a restart self-heals.

**Repair, in place, keeping history** ([merge_ledger_duplicates.py](scripts/merge_ledger_duplicates.py),
run with `--dry-run` first on a COPY): 27 → **26** rows. Survivor `scale-model-pallet-jack` keeps
`published_at` **19:29:20** (the earlier date), the file digest `sha256:9d1e5a862fb8deac…:236158`, both
titles (`name` = the Russian one, `also_named` = ["1/10 Scale Pallet Jack"]) and all three spellings in
`aliases` (source slug, published slug, opaque id); `published_url` is now the readable slug.
Evidence: [ledger_merge_report.json](debug/ledger_merge_report.json) — and its own proof step asserts
that every identity that existed before still marks a row and that the count fell by exactly the rows
folded.

**Verification** ([check_ledger_seed_idempotent.py](scripts/check_ledger_seed_idempotent.py), exit 0):
the account seed twice and the runs seed twice added **`[0, 0, 0, 0]`**; 26 rows and sha256
`3181dd9c5346d6ef` **unchanged**; **91 identities** checked across all 26 rows (every link, slug, alias
and distinctive name still finds its row; the three rows whose only title is the generic "car" are held
by their link, by design); `startup_seed → {aliases: [], merged: [], from_account: 0, from_runs: 0,
count: 26}`; and the repair re-run reports "already one row per model". LIVE: two dashboard restarts
(20:45:39 and 20:49:58) whose account reads really ran — `downloads/_analytics/snapshots.json` written
20:45:48 and 20:50:07 and containing the very title "1/10 Scale Pallet Jack" — left the ledger file
**byte-identical** and `/published` reporting **26**. Suite: **96 passed** (25 in tests/test_ledger.py,
**+7 new**, all offline: an autouse fixture neuters `canonical_url` so no test can resolve a real
redirect).

**Dashboards' code freshness**: the process serving :8000 had been started at 19:49 — *before* these
edits — so it was restarted; it now runs PID 14796, started 20:49:58, i.e. after `ledger.py` (20:41:56)
and `main.py` (20:37:14), `GET /` 200 and `/published` 26.

**Limitation, stated plainly**: two spellings that neither Creality's 301 nor a recorded alias connects
(an account URL with no published link recorded and no name overlap) still need one resolve at seed
time — when that resolve fails offline nothing is guessed, the model is simply recorded as new.

## C7. "Household items only" — the gate the run never had (2026-10-08, 21:20-21:45)

**The user's complaint, verbatim:** "to do household items only and one of the uploads was a toilet one
of them was a garage that's not the household household item that is useful … actually filter out
properly no rubbish item like household items that are actually useful that people will use in their
household day-to-day life".

**What the 21:02 run actually published** (their own run, 5 requested): `Ключница домой` (21:04:06),
`Clothes Peg` (21:05:02), **`Toilet` (21:06:06)**, **`Set of accessories for the bathroom` (21:07:25)**;
the fifth candidate failed. Ledger went 26 → 30 rows. The comments in the dashboard's own code had already
recorded the earlier damage with the phrase "household items" — that literal search string was the default.

**Why it happened:** nothing between discovery and the uploader ever asked whether a model was a useful
household item. `discover.find_creality` took the most-downloaded cards Creality's search returned for a
keyword list that included the words "household items" themselves — popularity is not usefulness.

**What is now different — one owner, three readers.** `selector.py` holds the rule: an allow list of the
user's own families (cable/headphone organizer · phone holder · cotton-bud holder · bottle/cup storage ·
storage box/basket/tray · clothes clip/peg · hook/key holder · desk organizer/pen stand; English and
Russian; whole-word matching, so `car` never fires inside `card`) and a reject list checked FIRST
(bathroom fixture/set · garage-automotive-workshop · 3D-printer part · toy/novelty/joke · decorative
trinket · weapon/adult), plus one-of-each-per-run. Discovery judges every card as it harvests (a refused
card never counts towards the requested number, so the listing keeps looking for an allowed one, and the
reason is returned); the run re-judges every candidate before queueing (a refused one is a visible
`rejected` row and is skipped, so a discovery bug still cannot put a toilet on the form); the dashboard
reads `GET /selector` and prints the families and the never-published list above the run, prefilling the
keyword box from it. Default searches are the families — **"household items" is gone**. A model published
**by hand** through `/upload` is deliberately not filtered: the user choosing a model themselves is the
decision, not this gate.

**Live proof** (`scripts/check_selector_live.py --count 3`, exit 0, `debug/selector_live_run.log`,
`debug/selector_live.json`): licence filter verified on the live listing first; **3 kept** — Organizer Box
(storage box / basket / tray), Charger Cables organizer 2nd edition (cable organizer), D Print Phone Mount
(phone holder) — and **15 turned down with a reason each**: 12 `duplicate-family`, 2 `no-family`
(Double laptop stand, K2 Plus Collecteur), 1 `reject:garage` (Double Heavy Duty Hook / Tool Holder —
Cabinet). The script's own invariant: no harvested model is one the gate refuses.

**Found by the live run and fixed:** `Usbc Cable Holder Screw Clamp` was refused as garage gear
(`clamp` was a reject word) — a cable tidy refused for being a clamp. `clamp` is out of the reject list;
a real workshop clamp falls to `no-family` and reads honestly. Re-run after the fix: the same model sits
in `duplicate-family` for the right reason.

**Tests:** `tests/test_selector.py`, 12 new — the user's own six rubbish names, all eight families
(English and Russian), reject-beats-family ("Toilet paper holder" is still rubbish), one-of-each, the
defaults not containing "household items", and the bypass test that hands the run a Toilet on purpose and
asserts the uploader is called exactly once (for the phone holder) and that no Toilet is ever recorded.
Suite: **108 passed**. Fixtures in `tests/test_auto_run.py`, `tests/test_ledger.py` and
`tests/test_reupload.py` whose models were called "Good", "Fresh" or "Rejected" now carry household-family
names — the run judges before it fetches, so a fixture called "Good" would never have reached the path
under test. No assertion was weakened: the only other change is the new `rejected` bucket in the expected
counts dict.

**Adversarial pass (21:50-21:57) — three real defects, all fixed, plus the live probe with the failing
phrase:**
1. **Ordering.** The gate ran *before* the already-published check in the harvest loop, so an
already-published cable organizer claimed the cable-organizer family and every **fresh** cable organizer
on the page was then refused with "a cable / headphone organizer is already going out in this run" — a
reason that was simply false. Reproduced with the old order (`{'cable-organizer'}` claimed by a published
card → the fresh one refused); the decision is now one pure function, `discover.judge_card()`, which checks
already-done FIRST and lets only a harvested card claim its family. Three new tests pin it.
2. **Live probe with the exact phrase that caused the complaint:**
`scripts/check_selector_live.py --count 2 --keywords "household items"` → exit 0, 2 kept, 17 turned down,
and the junk is refused by name — `Toilet`, `Set of accessories for the bathroom`, `Toothbrush stand`
(`reject:bathroom`), `Garage 3D Printing Projects` (`reject:garage`), 8 `no-family`, 4 `duplicate-family`.
3. **False refusal found by that probe:** "Tableware Plate Bowl Cup Spoon Fork Knife" was refused as a
weapon because of the word `knife`; cutlery is household, so `knife`/`blade` left the reject set (a bare
knife matches no family and is refused anyway, with the honest reason). `Gun grip` / `Sword stand` are
still refused. Suite now **111 passed**; the retry replay still exits 0 with the real ledger unchanged.
**Not fixed, deliberately:** a card whose name is smushed into one word ("Holderkeys") is refused — a
length-gated substring fallback was tried and rejected (it either misses those names or starts refusing
unrelated ones as decor/garage), so this stays a whole-word limitation with an honest reason.

**State at the end of this pass:** dashboard restarted 21:55 (PID 19424, `GET /` 200, `/selector` 200,
`/auto/status` 200 with `selector`: 8 families / 6 reject rules, `/published` still 30) so it serves the
new code; the live checker's browser session was read-only; **nothing was published by this pass**. The
ledger's rows are unchanged (30 before and after, `sha256 f0b558534f65a0aa`) — the 21:41 restart's own
startup seed rewrote the file at 21:41:19 for the ordinary R15 reason: it learned the readable published
links as aliases for three of the four rows the user's 21:02 run added (`Ключница домой` →
`homestead-key-holder`, `Clothes Peg` → `3d-printed-clothes-peg`, the bathroom set →
`bathroom-accessories-kit`); the `Toilet` row still carries its opaque id only. No row was added, removed
or replaced, and nothing new became publishable.

## D. Still to verify — numbered fix/verify prompts

Each one names the broken or unproven behaviour, where it lives, and what the fix must achieve.

12. **✅ DONE (2026-10-08, live) — a rejected Submit is diagnosable now, and a single photo fills both covers.**
   Implemented in `uploader.py`: `REJECTION_JS` reads the active wizard step (green badge) and every
   field in Creality's error state with its label; a `page.on("response")` capture keeps the create
   request's status + body while Submit is pressed; `describe_submit_failure()` turns
   step + flagged field + response into the returned `error` (so a bounce reads “Creality rejected the
   submission: the form bounced back to step 1; field ‘Model files’ is in Creality's error state
   (err-box); POST … → HTTP 400 …”), and both land in `UPLOAD_VERIFY["submit_rejection"]`. `main.py`
   keeps that evidence on the run item (`it["upload_verify"]`) so the run log carries it, and the
   failure reason is no longer cut at 200 chars. `cover_attach_plan()` replaces the old
   `app != web` guard: the two cover slots are independent inputs, so one photo now fills BOTH
   (image_plan() always said `app = web` for that case). Evidence: the 19:29 publish above
   (`app cover … img_0.webp` filled, submitted, recorded); `debug/live_form_watch.json`.
   Tests: `test_describe_submit_failure_reports_what_creality_did`,
   `test_a_single_photo_fills_BOTH_cover_slots`, `test_the_run_log_keeps_the_upload_evidence`.
   Suite: **90 passed**. The rejection read itself never ran live (this attempt published, so there
   was no bounce to read), so it is now exercised offline against the real markup by
   `scripts/check_rejection_reader.py` — and that gate immediately caught **three real defects** in
   the new code: (1) the message selector string spanned two lines, a JS SyntaxError that would
   have thrown on every failed submit; (2) the label was read from the outer `.el-form-item`, so a
   bounce would have named the section (“Model Info”) instead of the field (“Model files”);
   (3) `describe_submit_failure()` treated a bare step number as a rejection, so it could have
   invented a rejection on a clean form. All three fixed; the gate now passes (exit 0) and the
   suite is green again. One deviation from the prompt's wording: the create request is captured
   with the browser's own `page.on("response")` events rather than a separate CDP `Network`
   session — same status + body, one less protocol client.

   Original prompt spec (all three clauses now met): the verification publish had been rejected by
   Creality's own validation with **no message**, and the pipeline's report (“Submit was pressed but
   Creality never confirmed”) could not say why. CODE: `uploader.py` step 10 — `_click_submit()` and
   the 40×1 s confirmation loop plus the final `return {... "error": "Submit was pressed but
   Creality never confirmed …"}`; the photo slots are planned in `image_plan()`. REQUIRED OUTCOME:
   (a) right after the Submit click, read back which wizard step is active and any field in its error
   state (`div.err-box` / red border / `.el-form-item__error`) with its label, and put BOTH in
   `UPLOAD_VERIFY` + the returned `error` — a bounced-to-step-1 form must be reported as “Creality
   rejected the form: <field> flagged (step N)”, never as “never confirmed”; (b) capture the
   create request's status + response body so the next failure has Creality's own words instead of
   guesses; (c) fill the **App cover 3:4** slot with the same photo when `image_plan()` says
   `app = web`. CHECK: `test_describe_submit_failure_reports_what_creality_did`,
   `test_a_single_photo_fills_BOTH_cover_slots`, `test_the_run_log_keeps_the_upload_evidence`, and
   the live publish in C4; `scripts/check_rejection_reader.py` runs `REJECTION_JS` against the real
   bounce markup in a browser (and a clean form, which must stay quiet).

11. **✅ DONE (2026-10-08, live panel + replay). Fetch failures are counted and shown as
   failures, and Stop cuts the backoff.** The audit's first gap: the run's counts read
   `published: 2, skipped: 4, failed: 0`, so four models that never got published looked merely
   skipped and a fetch death was indistinguishable from a licence refusal. Fixed: a model whose
   fetch died (after its retries) or came back with no cover photo is `state: "fetch-failed"`
   (`main.FETCH_FAILED`), `_auto_counts()` carries a separate `fetch_failed` bucket next to
   `skipped` (a decision: licence, no model file), and the dashboard renders the new pill
   (`.astate.fetch-failed`, red) and counts it in both status lines ("Run finished — 2 published,
   0 skipped, 4 could not be fetched, …"). `_fetch_with_retries()` now returns whether the run
   was stopped and `_wait_between_fetch_tries()` wakes at once on Stop (0.25 s slices), so
   pressing Stop during a backoff leaves the model undecided (`queued`, reason "run stopped while
   retrying this model's fetch") instead of blaming it for a fetch it was never allowed to
   finish. Evidence: replay `debug/auto_fetch_retry_replay.json` (counts
   `published 2 · skipped 0 · fetch_failed 4`, exit 0, real ledger hash-identical); the REAL
   dashboard clicked in a browser — `scripts/replay_recorded_failures.py --serve` served the real
   app with the replayed run, the run list showed 2 `published` + 4 `fetch-failed` pills with
   distinct computed colours (published `rgb(165,214,167)`/green border vs fetch-failed
   `rgb(255,138,101)`/red border), a real click on **Stop** produced `POST /auto/stop 200 OK` and
   `stop: true` on `/auto/status`, and the running line read "Publishing 2 of 6 — 0 in progress,
   0 skipped, 4 could not be fetched, 0 failed" (`debug/panel_run_list.json`). Tests: the count
   separation (`test_auto_run_skips_nds_and_continues`, `test_a_navigation_or_network_fetch_failure_is_retried`,
   `test_a_fetch_without_images_is_never_handed_to_the_publisher`) and
   `test_stop_is_honoured_during_a_fetch_backoff` (5 s backoff, Stop pressed mid-wait, run ends
   in <2 s with no second attempt). Suite: **87 passed**. Limitations: the row link's
   `target=_blank` click did not surface a tab in this preview webview, and
   `preview_screenshot` could not composite a frame — the panel evidence is the live DOM read.

10. **✅ DONE (2026-10-08, offline replay). Recoverable failures in the auto run's per-model
   loop.** The user's own run skipped models 7 and 8 with `Execution context was destroyed,
   most likely because of a navigation` / `net::ERR_INTERNET_DISCONNECTED` and carried 5 and 6
   to the publisher to die there on `No edited images for this model`. `main._auto_worker` now
   (a) retries those two failure classes a small bounded number of times — `FETCH_ATTEMPTS` 3
   (1 try + 2 retries), `FETCH_RETRY_DELAY` 4 s — keeping every attempt on the item
   (`fetch_attempts`) and only counting the model as a fetch failure when the last attempt also
   dies (`could not fetch this model: <recorded string> (tried 3 times: attempt 1: …)`), and
   (b) refuses to hand a model to the publisher when `usable_cover_images()` finds no cover
   photo of THIS model on disk, reporting `could not fetch this model's cover photos: No edited
   images for this model. Run Fetch & Edit first so the red images are generated.` and moving on.
   Replayed through the real loop with the exact recorded strings (`scripts/replay_recorded_failures.py`,
   fakes + temp ledger, no browser, no publish): a failure that happens **once** is now a model
   that gets published on try 2 (`fetch_attempts: ["attempt 1: <recorded string>"]`), a failure
   that happens **every time** is retried 3× and then skipped as a fetch failure having never
   touched the publisher, and a fetch with no usable photos is skipped with the publisher on the
   hook for **0** calls. Evidence: `debug/auto_fetch_retry_replay.json` (`real_ledger_unchanged:
   true`, sha256 + mtime of `artifacts/published_models.json` identical before/after). Tests:
   `test_a_navigation_or_network_fetch_failure_is_retried` (both recorded strings, parametrized)
   and `test_a_fetch_without_images_is_never_handed_to_the_publisher`. Suite: **86 passed**.

1. **✅ DONE (2026-10-08, live). Prove the whole form fill without publishing.** Filled
   `scale-model-pallet-jack` (not in the config) with `CREALITY_NO_SUBMIT=1`: covers + gallery
   counter `[0,1]` (1 photo), source declared and registered, licence answers `[2,1,1]` read back
   with badge `CC BY-NC-SA`, consent found + ticked by a real mouse click on the circle. No listing,
   nothing recorded. See section C2. Original wording: `uploader.upload()` (`dry_run` returns at
   line ~857 after file acquisition, so it never touches the form; `CREALITY_NO_SUBMIT=1` at
   ~line 1531 is the real "fill but don't submit" switch). Target: run one form fill for a model
   that is NOT in `artifacts/published_models.json`, with `CREALITY_NO_SUBMIT=1`, and report what
   the covers, the Model Images gallery counter, the Model Source card (`.origin-choose-mark`
   text/link), the three licence answers and the consent box actually registered. Must not
   create a listing and must not record anything in the config.
2. **Say which switch does what.** `README.md` and `GATES-REUPLOAD.md` currently imply
   `dry_run` verifies the form. Target: document that `dry_run`/`UPLOAD_DRY_RUN=1` stops before
   the form, while `CREALITY_NO_SUBMIT=1` fills everything and stops at the last click, so
   nobody verifies with the wrong switch again.
3. **Observe the discovery skip in a real run.** `discover.find_creality(..., is_done=...)` +
   `main._auto_worker`'s `_already_done`. Only unit-tested so far. Target: run discovery with the
   real ledger as `is_done` (read-only, no publishing) and show `skipped_done > 0` with fresh
   models filling the requested count.
4. **Re-verify the licence filter read-back once.** `scripts/check_discovery.py` (last green
   2026-10-08) — it was skipped this pass because the code has not changed since; run it once
   more to confirm the panel still opens, ticks and reads back.
5. **Analytics per-model downloads.** `POST /analytics/fetch-downloads` was not run (≈22 model
   pages, 5-10 s each). Target: run it and confirm the cached download counts land for the
   account's models, or that a missing tab is reported honestly.
6. **AI re-edit path.** Preview → "Regenerate photos": `/edit-images` → `editor.edit_all`. Not
   exercised this pass (fully local). Target: run it on one fetched photo set and confirm the
   edited tiles come back and are the ones the publish plan would send.
7. **Bounded waits in the picker.** The picker uses fixed waits (3.5 s per query, 2 s after the
   click, 12×0.5 s for registration). Target: replace with polling on the DOM condition
   (rows present / mark filled) so a slow form does not report a false failure and a fast one
   is not slowed by ~10 s per model.
8. **Two-tab safety.** Everything drives `browser.contexts[0]` and the first matching tab; a
   stale create-model tab from a previous run can be reused. Target: close or reuse tabs
   deterministically and never operate on a form this run did not open.
9. **✅ DONE (2026-10-08, live). Dismiss the Remix picker overlay before the footer is
   clicked.** Same live fill as section C2 (no submit, ledger untouched). `uploader.py` now has
   `_topmost_at()` (what a real click would hit, iframe-aware) and `_clear_picker_overlay()`:
   Escape, then — while the picker's card still covers the point — one real click at the consent
   point (the leftover there is the card, never a suggestion row; the Submit check passes this
   point as `dismiss_at`), verifying with `elementFromPoint` again. Step 9 runs it for BOTH
   footer regions before the first tick click; step 10 runs the same guard before pressing
   Submit. Live record from the fixed fill (`debug/consent/consent_diag.json`):
   `reach: [["consent","topmost","div","origin-choose-mark"],
   ["consent","after-escape","div","origin-choose-mark"],
   ["consent","after-click","span","el-checkbox__inner"],
   ["submit","topmost","div","submit"]]`, `reachable: true`, `submit_reachable: true`,
   `tried: [["round1:circle", true]]`, `checked: true` — the pre-tick diagnostic shows the circle
   itself as the element at the click point (Escape alone does not clear the card; the one real
   click does). Suite: **83 passed** after the change. Original wording: it was found in the consent
   proof (section C2) — the instrumented fill logged `document.elementFromPoint()` at the consent
   circle's centre as `div.origin-choose-mark`; the same pattern was seen in the 17:03 run, whose
   `after_submit.png` shows the picker over the footer when Submit was pressed ("Submit was pressed
   but Creality never confirmed").

## 2026-10-09 — the takedown report: only CC0 / CC BY / CC BY-SA are re-published, and a remix must name WHICH model it credits

**What the user reported.** Three re-uploads were taken down; `model-detail/6ac912918a85cf05f9ae1ba1`
was the one flagged for infringement, and another was flagged for "not original image".

**What it was.** That listing is our re-upload of `smartphone-stand-with-cable-cutout`
(author Borisenko, licence read **CC BY-NC 4.0**), published 2026-10-09 17:13 by run
`20261009-170809`. The listing is gone and the page answers **"The model did not pass
platform review."**

**The measurement that mattered.** Checking every listing that run published, live:
removed = cable organiser (**CC BY 4.0**), modular desk organizer (CC BY-NC 4.0),
Smartphone Stand with Cable Cutout (CC BY-NC 4.0); still live = ORGANIZER BOX (CC BY 4.0),
Hook (CC BY-NC 4.0). All five **source** pages are still up. So the licence type alone does
not explain the takedowns — which is why the ask was split into two fixes and the second one
(the attribution check) was done as well.

**Fix 1 — policy (decided by the user).** `discover.CREALITY_LICENCE_ALLOW` is now
`[CC0, CC BY, CC BY-SA]` and `CREALITY_LICENCE_DENY` is `[CC BY-NC, CC BY-NC-SA, CC BY-ND,
CC BY-NC-ND, Standard License]`. The old list wrongly ticked **CC BY-NC-ND** (No-Derivatives —
a remix may not be made from it at all). `downloader.classify_license` refuses NC with its own
sentence, and ND/NC are decided by the licence's **identity** (CC link / named licence / icon
badge) when the page gives one, so the form's Model-Source tip ("licenses such as CC BY-ND and
CC BY-NC-ND…") can no longer refuse a genuine CC BY source.

**Fix 2 — attribution.** `uploader.declares_our_source()` replaces the old
"first word of the clicked row appears in the card" check; the run's records show an EMPTY
`remix_registered_href` for all five models, i.e. nothing ever proved which model was credited.
The picker no longer clicks a merely-plausible row (`min_score` 80 = certain), and the upload
**fails** rather than submitting a listing that credits a different creator.

**Verified.** Suite **119 passed** (was 114); `check_license_read.py` 6/6; `check_source_pick.py`
PASS; `check_license_capture.py` PASS; `check_page_syntax.py` clean. Live, read-only, on the
user's own browser: the reported source now reads `ok_for_reupload: false` (NonCommercial) while
`cable-storage-solution-3d` still passes as CC BY. The dashboard was restarted on this code and
`/auto/status` reports the new allow/deny lists; `POST /upload` with that model's own licence
string returns **403** with the NonCommercial reason. Nothing was published.

**Still open.** The "not original image" report is not addressed: the pipeline still uploads the
source creator's photographs.

## 2026-10-09 — second pass: the three audit findings repaired (R18)

**1. The "Make a file" card was restored.** It had been deleted from `creality/index.html` in this
campaign's uncommitted diff. The repo settles that it was never authorised: `GATES-GENERATE.md` G9
*requires* the markup, its OWNS list includes `creality/index.html`, and no plan/status entry asks
for the retirement. Restored verbatim from HEAD; `scripts/verify_generate.py G9` went from
`GATE-FAIL: dashboard missing #generate-btn` (exit 1) to `GATE-PASS G9` (exit 0), and the card is
visible on the live dashboard.

**2. Both publish decisions are on the dashboard.** `creality_rules` now keeps the photo choice in
`artifacts/publish_settings.json` (`photo_source()`, read per call; safe default `ours`; the
`CREALITY_PHOTO_SOURCE` env var remains an operator override) with `set_photo_source()`, and the
authorisation file gained `remove_authorized()`/`authorized_entries()`. New routes:
`POST /policy/photo-source`, `/policy/authorize` (a reason is required), `/policy/unauthorize`,
`/policy/verdict` and `/photo-plan` — the last two re-judge / re-plan the ALREADY loaded model, so
no re-fetch and no restart. The rules card has the select, the authorise row and the authorised
list with Remove; the per-link verdict line states "Photos this listing carries: …" and offers an
inline "Authorise this source" when that is the only refusal. Live-verified both directions
(renders ↔ the creator's photos in ~6 s; authorise → `allowed ✔` + Publish enabled; Remove →
refused again), leaving the authorisation file empty and the photo source on the safe default.

**3. The listing name no longer carries the SEO tail.** `scripts/probe_model_name.py` proved the
page's own `h1` (`.group-name`) holds the real name while `document.title`/`og:title` carry
"… Fichier STL et 3MF pour l'impression 3D | Autre | Creality Cloud". `downloader._model_name_from`
prefers the heading when the title was built from it and otherwise strips the localised tail; the
collector returns `h1`. Live: the reported link now fills the Model Name field with
`Cliqueur moelleux en forme de ravioli` (37 chars), not the title.

**Checks:** 203 tests pass; `check_policy`, `check_page_syntax`, `check_license_read`,
`check_source_pick`, `check_license_capture`, `check_ledger_seed_idempotent`,
`check_rejection_reader`, `verify_generate G9` all exit 0; `replay_recorded_failures` passes in two
phases (the recorded failures reproduced in their own mode, plus the control proving the
"no photos came back" dead end now publishes under the safe default). Dashboard restarted on the
final code (PID 21348) and driven live. **Nothing was published** — ledger 40 rows, sha256
`ddac346031dc`, unchanged.

## 2026-10-09 — the user's dashboard feedback (R19)

The rules card ("Creality's published rules · what may be published") is removed — it was my
addition in R17 and the user never asked for it. The two publish decisions the user did ask for
(photo source + per-source authorisation) moved out of it into the "1 · Model URL" card, next to
the link they apply to; the per-link verdict line keeps stating the photo choice and keeps the
inline authorise button. The "Make a file" card is removed again — deliberately this time — and
its unreachable residue retired with it (`POST /generate`, `generator/`, `scripts/verify_generate.py`,
`scripts/time_nemotron.py`, the two requirements they alone used, and `GATES-GENERATE.md` rewritten
as a RETIRED record). The earlier restore of that card was wrong: `scripts/verify_buttons.py` already
asserted "the generator section is gone by design". Nothing was published; the ledger is unchanged.
