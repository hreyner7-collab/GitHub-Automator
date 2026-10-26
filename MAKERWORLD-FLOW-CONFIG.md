# MakerWorld upload flow — the mapped truth (2026-10-25)

This is the map `makerworld_uploader.py` is written against. It was measured by
driving the **real MakerWorld wizard over CDP** in the logged-in Chrome on port
9222, with the raw evidence committed in `MAKERWORLD-STUDY/` (page dumps, the
network log of the walk, screenshots, and MakerWorld's own guideline pages).
`scripts/check_mw_provenance.py` re-reads that evidence and refuses any control
name the pipeline uses that is not present in it — so this file cannot quietly
drift into invention.

Nothing here touches Creality. The Creality target keeps `uploader.py`,
`UPLOAD-FLOW-CONFIG.md` and `ledger.py`; MakerWorld keeps `makerworld_uploader.py`,
`makerworld_flow.py`, `makerworld_ledger.py` and this file. They share the
browser, the fetched model files and the edited images — nothing else.

## The stack (why no Creality selector transfers)

MakerWorld is a **Next.js pages-router SPA, React + Material UI + Emotion**
(`mw-css-*` classes), i18next for language, Cloudflare in front, Sentry for
errors. Creality's form is **Element UI / Vue (Nuxt)**. Different component
library, different DOM, different event plumbing — the one thing that carries
over is that **React needs the native value setter before an `input` event**,
same as the Creality remix box did.

API namespace: `https://makerworld.com/api/v1/<service>/…`, the publish service
being `design-service`. File bytes go to a **presigned S3 PUT** on
`or-cloud-makerworld-prod.s3.us-west-2.amazonaws.com`, then the CDN prefix
`https://makerworld.bblmw.com` serves them.

## Entry points (measured from the nav Upload popover's own hrefs)

`MAKERWORLD-STUDY/upload_menu.json` records the four choices the popover
offers, with their descriptions as the site words them:

| entry | href | the site's own words |
|---|---|---|
| 3D original model | `/en/my/models/publish?type=original` | "This model is my original creation." |
| 3D remix model | `/en/my/models/publish?type=remix` | "I made significant modifications based on another model." |
| Laser & cut original | `/en/my/laser-and-cut-models/publish?type=original` | "This model is my original creation." |
| Import from a third-party site | (dialog, not a plain href) | "Supported: Thangs and Thingiverse" |

The pipeline publishes through **`?type=remix`** (the user's choice).

**Import is not a route the pipeline uses.** It verifies that you own the
Thingiverse/Thangs account the model came from — it is for creators moving
their own work, not for re-publishing someone else's.

## Step 1 — the file (`/en/my/models/publish?type=remix`)

Measured walk: `r01_open_dump.json` → `r02_stl_branch_dump.json` →
`r03_file_attached_dump.json`, network side in `remix_walk.log`.

1. The page **opens on the MakerLab / 2D branch**: the only `input[type=file]`
   accepts `.jpg, .jpeg, .png, .webp`. Attaching an STL here is impossible.
2. The 3D branch is chosen by a **text control**, not a radio:
   `I have STL/CAD files or other types of 3MF files`. Clicking it reveals the
   model-file inputs.
3. The model-file input is `input[type=file][multiple]` with a measured
   `accept` list — `3ds, amf, dwg, dxf, f3d, factory, fcstd, iges, ipt, obj,
   ply, rsdoc, scad, shape, shapr, skp, sldasm, sldprt, slvs, step, stl, stp,
   studio3, zip, 3mf, stpz`. Find it by `accept` containing `.stl`, not by
   position (the page holds 2–3 model inputs at once).
4. **Attaching a file is what creates the draft.** The client calls, in this
   order (all 200, recorded in `remix_walk.log`):
   - `POST /api/v1/design-user-service/my/upload`
     `{"useType":"makerworld/model","fileNames":["<name>.stl"]}`
     → `{"cdnPrefix":"https://makerworld.bblmw.com","urls":["<presigned S3 PUT url>", …]}`
   - `PUT <presigned url>` — the file bytes (it also generates a `.jpeg` preview
     upload on its own)
   - `POST /api/v1/design-service/my/draft` → `{"id":10186871}`
   - the wizard URL becomes **`/en/my/models/drafts/<id>/edit`**
5. `Next Step` advances to Model Information.

So the draft id is read from **the URL** (`makerworld_flow.draft_id_from_url`),
which is the only identity the page itself advertises. Nothing is verified by
`input.files.length`.

## Step 2 — Model Information (one page, everything)

`/en/my/models/drafts/<id>/edit` carries the whole listing on one scrollable
page (the stepper reads `1 Upload · 2 Model Information · Edit Model`).
Inventory below is from `r04_step2_dump.json` — 9 file inputs, 18 radios, 4
checkboxes, 9 text fields, 4 selects, 35 buttons.

**Model source radios:** `Original` / `Remix` / `Share` (Remix arrives checked
because the entry was `?type=remix`).

**Raw Model Files** — an "Open Source" checkbox (checked by default) and the
line "Check off the open source, users will not be able to download raw
models." The site also states that uploading the original STEP separately
improves cloud slicing.

**Model Covers — two required slots.** `Web/App cover 4:3` and `App cover 3:4`
("3:4 cover appears larger in app"). Both take `image/jpeg,png,webp,gif`,
`multiple=false`, and the hint says `jpg/gif/png, ≤ 30MB. Please use real print
photos`. Find them by the text beside each hidden input: `4:3 Cover` and
`3:4 Cover is prioritized in App`.

**Real life photos ( n / 16 )** — `Upload ≥3 photos of the printed model,
covering 3 views`. This is the requirement that does **not** transfer from
Creality: the pipeline's edited studio composites are renders, not prints.
`makerworld_flow.MIN_REAL_PHOTOS = 3` and the driver refuses before opening a
tab. Its own `Add Photo` input is `multiple=true`.

**Model Pictures ( n / 16 )** — "Photos of the printed model,
png/jpg/webp/gif, ≤ 30MB/piece, 4:3 ratio recommended." A second `Add Photo`
input, identical in markup to the real-life one: they are told apart **only by
document order after their headings**, which is what `SLOT_INDEX_JS` does.

**Model Origin** (the remix source). The measured warning is the licence rule:
"Please make sure the source model's license does NOT contain
\"NoDerivatives\" attribute. For example CC-BY-ND, CC-BY-NC-ND, **Standard
Digital File License** are not allowed." An `Add` control opens a search box
whose placeholder is
`Paste the URL or utilize the search function for models on MakerWorld`.
In the form state the choice lands in `relateDesignInfo`:
`{needRelate, id, designType, title, cover, status}` (read out of the site's own
bundle, `MAKERWORLD-STUDY`'s `net/` route files) — i.e. **the base must be a
MakerWorld design id**, which is why a Thingiverse source cannot be a remix.

**The same rule, upstream, on the listing** (measured 2026-10-25 by clicking the
filter chips in a logged-out headless Chromium and reading the address bar): the
param is `licenses=`, and its tokens are the chip labels verbatim — `BY`, `BY-SA`,
`BY-ND`, `BY-NC`, `BY-NC-SA`, `BY-NC-ND` — except Public Domain, which is `CC0`. The
pipeline harvests only `licenses=CC0,BY,BY-SA`. Two traps, both measured: the names
that appear in MakerWorld's own prose (`CC-BY`, `CC-BY-SA`) are **not** accepted by
the param — the site ignores an unknown value silently and hands back the full
unfiltered list, which is how the first attempt looked perfectly normal while
offering NoDerivatives sources. And a design's licence is rendered as a
`creativecommons.org/licenses/<code>/<version>/` **link**, not as text, so
`_fetch_makerworld` reads the anchors; a page that names no licence at all stops an
auto item rather than defaulting to CC BY the way the manual path does.

**Model Name** `0/50` · **Category** — a MUI autocomplete whose
`<input>` has **no placeholder**; "Quick search by entering category
keywords" is its floating label · **Tags** `Tags (0 / 50)` with a `0/100` input
and `Press "Enter" to separate tags` · **Description** and **Model Videos**
("Max 30s, mp4/mov"), **BOM**, **Community Post**, **Assembly Guide**,
**Exclusive Model Program** ("Not Eligible" for this account), **Visibility**
`Public` / `Private` (Public arrives checked). The fields are laid out in the
order Name → Category → Tags, and are filled in that order.

**Licence — four radio groups, measured verbatim:**

| question | options |
|---|---|
| Allow adaptations of your work to be shared? | Yes · Yes, as long as others share in the same way · Yes, but any derivative works must be published exclusively on MakerWorld · Yes, any derivative works must be published exclusively on MakerWorld and comply with Standard Digital File License – Community Use (SDFL‑C) · No |
| Allow commercial uses of your work? | Yes · No |
| Allow sharing or redistributing of your work or its derivatives? | Yes · No |
| Is this model supported only for printing through Bambu Handy? | Yes · No |

The form renders the resulting licence **name** (a select reading
`Standard Digital File License` on an untouched form). The answer→name mapping
is MakerWorld's own and **was not measured** (that would mean publishing). So
`LICENSE_PLAN` states the ordinary CC reading, and `LICENSE_READ_JS` reads the
name back **inside the License block only** — between the Bambu Handy question
and Visibility — because the Model Origin warning *names* CC-BY-ND and
CC-BY-NC-ND as examples, exactly the trap `UPLOAD-FLOW-CONFIG.md` documents for
Creality's Model Source tip. If the rendered name is not the source licence, the
driver refuses to submit.

**Description rule, as the pipeline runs it (decided 2026-10-25):** MakerWorld's
form *asks* "Please detail how you remixed the original model. Otherwise, our
moderators will remove your model." — but the user's instruction is that the
listing carries **the source listing's own description, word for word**, with no
generated preamble, credit line or remix note, so `plan_description` returns the
source text unchanged. The text is not scraped from rendered HTML: `_fetch_makerworld`
reads the public `GET /api/v1/design-service/design/<id>` record and takes
`summary`, which is HTML wrapping the author's prose in
`<boostme><boosttitle>…</boosttitle><boostcontent>…</boostcontent></boostme>`;
`_mw_html_to_text` keeps only the `boostcontent` (the title is site chrome),
strips tags without inserting spaces, and unescapes entities. The same API call
supplies the bare licence token (`BY-SA` → `CC BY-SA`), `title`, `categories`,
`tags` and the creator name. The driver types the text line by line into the
contenteditable editor, reads it back, and records `same_as_source` in `verify`;
an empty editor fails the publish (Description is required).

**End of the page:** `Previous` · `Save to draft` · `Publish`.

## Draft housekeeping (how the study cleaned up after itself)

From the site's own bundle: `GET /api/v1/design-service/my/drafts` →
`{"hits":[…],"total":N}` and `DELETE /api/v1/design-service/my/draft/<id>`.
The probe draft `10186871` was deleted through that endpoint; the re-list
returned `{"hits":[],"total":0}` (`MAKERWORLD-STUDY/drafts_after_delete_10186871.json`).
The draft's own edit page has **no Delete control** — the drafts list is the
only place it exists, which is why the API route is the reliable one.

The draft object MakerWorld returns is a useful schema reference: it carries
`modelSource:"remix"`, `coverLandscape`/`coverPortrait`, `designPictures`,
`realPhoto…`, `license`, `tags`, `title`, `summary`, `categoryId`,
`exclusive`, `nsfw`, `paidSetting.isPaid`, `draftSetting.createStep`, `parentId`,
`designId`, `status`.

### Evidence hygiene

`MAKERWORLD-STUDY/` is committed so the mapping can be re-checked without re-walking
the wizard, and it is scrubbed first: the account's MakerWorld `uid` (which appears in
`/design-user-service/my/preference` responses and inside every upload key path
`makerworld/model/<date>/<uid>/<hash>`) is replaced with `REDACTED_UID`, the presigned
S3 query strings (`X-Amz-Credential`, `X-Amz-Signature`, …) with
`presigned_query_scrubbed=1`, and the site's Sentry DSN key with `REDACTED`. Endpoint
names, methods, statuses and every control text are untouched, because those are what
`scripts/check_mw_provenance.py` verifies. No listing was published, so the only
account-shaped artefact ever created was the probe draft, and it is deleted.

## What is NOT measured (and how the code treats each gap)

| gap | handling |
|---|---|
| the licence answer → rendered-name mapping | never trusted: read back and refuse on mismatch |
| the source-suggestion **row markup** after typing a URL (`[class*=origin]` etc. is a guess from Creality's naming) | the driver waits for *any* suggestion rows and reports them in `verify.origin.rows`; it publishes nothing if none appear. The row is chosen by `pick_source_row` on its **text**, and `SOURCE_ROW_CLICK_JS` re-reads that text before clicking, so a list that re-rendered in between refuses instead of registering a look-alike. `ORIGIN_STATE_JS` then re-reads the Model Origin block and the publish is refused if the source is not named in it |
| whether MakerWorld flags a duplicate model | `DUPLICATE_RE` watches for the wording; our own `makerworld_ledger` is the real guard |
| the source-suggestion row **markup** | measured later (2026-10-25): typing a URL lists design cards; the pipeline clicks the card that carries the source's own title/design id with a real mouse (`ORIGIN_CARD_JS`), re-reads `ORIGIN_STATE_JS`, and refuses if the card's text or the Model Origin block does not name the source |
| category picker semantics | measured 2026-10-25 off the live draft (`r04_step2_dump.json`): the `<input>` has **no placeholder** — the searched-for text is its floating label, so placeholder-based lookup found nothing and every run stopped here. `CATEGORY_BOX_JS` finds the box by that label or by document order between the `Category` and `Tags` headings, waits up to 10 s for it to mount, types the source's category (retrying with its last word), clicks a rendered `li.MuiAutocomplete-option` with a real mouse (`CATEGORY_OPTIONS_JS`), and reads the value back — no category, no publish |
| the `Publish` result | measured: Publish opens the **"Publish your model"** sync dialog; choosing the CC-only option and Confirm produces the toast **"Model published & synced!"** and lands on `/en/@<user>/verifying` — i.e. **manual review**, not a public page yet. Success is the toast **or** that URL; the driver reports `pending_review: True`, and the ledger records the submission as published with the note "MakerWorld is reviewing" |

## The published end state (measured 2026-10-25)

A fresh submission does **not** appear at `/en/models/<id>` immediately: it enters
the account's **Verifying** queue (`Published Models (n)` stays 0 until review
clears). The live listing was found later at
`https://makerworld.com/en/models/3495491-mini-bookshelf-whimsy-fairy`.

Editing a published model goes through `/en/my/models/<designId>/edit`
(title `Edit Model - MakerWorld`). `Publish` there does **not** open the sync
dialog; it opens **"Model Data Changed?"** — a geometry notification question
whose default answer is "Yes. Users must be notified…". A description-only edit
answers **No** and Confirm, which still sends the listing back to Verifying. The
driver does not edit published listings; this was a one-off manual correction and
is recorded here so the two dialogs are not confused.

## Dry-run conventions

- `MAKERWORLD_DRY_RUN=1` — stop right after the draft exists (proves the file
  path end to end; leaves a draft on the account, delete it with
  `DELETE /api/v1/design-service/my/draft/<id>`).
- `MAKERWORLD_NO_SUBMIT=1` — fill every field, never press `Publish`, leave the
  wizard open for a human, screenshot to `downloads/_shots/mw_before_submit.png`.
- `POST /upload/makerworld/plan` — the measured requirements checked with **no
  browser at all**; this is what the MakerWorld dashboard's "Check MakerWorld plan"
  button calls.

## The dashboard is its own page: `/makerworld`

`makerworld/index.html`, served by `GET /makerworld` — its own title
(`MakerWorld Pipeline V<n>`, bumped on every update like Creality's marker), its own
layout, its own memory panel. The Creality dashboard at `/` carries no MakerWorld
publish markup at all: the card that briefly lived there is gone and the only thing
linking the two pages is one topbar href. `tests/test_makerworld.py` asserts both
halves, so the separation cannot rot back into a merged page.

| step on the page | what it calls | what it must not do |
|---|---|---|
| 0 · Find models to remix | `POST /discover` with `site:"makerworld"` (trending / popular / hottest, headless, no login, `&licenses=CC0,BY,BY-SA` read back off the page) | never fetch, plan or publish by itself — publishing is the Auto card's decision, taken once |
| 1 · Source design (MakerWorld url) | `POST /fetch` with `edit_images:false` | never the AI re-edit — that is a Creality-side choice |
| 2 · Listing | name/description prefilled by `loadSource()` from `/fetch`; tags and category typed by hand | the description is the source's own text (API `summary` → `boostcontent`) verbatim — never generated, never truncated; the page says so |
| Auto · Find and publish | `POST /makerworld/auto/start` → `ensure_browser()` (opens the logged-in debug Chrome **before** the run is announced, and answers 503 if it cannot) → `discover()` (licence-filtered) → `downloader.fetch` → `makerworld_uploader.upload(dry_run=False)` | never the Creality loop's modules; never an item whose source page names no licence, or fewer than 3 of the original maker's photographs. It passes the fetched **description, first category and tags** straight into the upload, like the manual form. A download that fails reports the browser's own error (e.g. `ECONNREFUSED`), never "MakerWorld refused the file" |
| covers grid | the scraped originals (`_raw_orig_images`) | on this manual page, never presented as print photos |
| 3 · Photographs of your prints | `GET /makerworld/files` → images already in `downloads/`, plus the two opt-in restyles (`photo_background`, `photo_recolour`) | never fills itself from the gallery (the Auto run is the one path that uses the source's own photographs, because they are the original maker's real prints) |
| 4 · Check | `POST /upload/makerworld/plan` | opens no browser, creates no draft |
| 4 · Publish (starts disabled) | `POST /upload/makerworld` | unlocks only with a name, a model file, a cover, ≥3 print photos **and** the assertion tick |
| 5 · Memory | `GET /makerworld/published`, `POST /makerworld/forget` | never reads or writes Creality's `published_models.json` |

The Publish button's lock is the measured rule, not a courtesy: MakerWorld's form
says "Upload ≥3 photos of the printed model, covering 3 views", so a listing that
cannot satisfy it is not offered. Forget needs a name, url or design id — an empty
match is a 400, never "forget everything".

### Tidying the photographs you ticked (`prepare_print_photos`)

The listing needs photographs of a print this account actually made, and the only
edits allowed on them are cosmetic: `makerworld_flow.prepare_print_photos(paths,
background, recolour)` runs the local finisher (`editor.edit_image`) over the
ticked files and nothing else. Measured behaviour, all of it asserted in
`tests/test_makerworld.py`:

| case | what happens |
|---|---|
| neither option set | the list is returned untouched in 0.0 s — no segmentation, no new files |
| `background` only | that backdrop is composited behind the product (`gray` / `white` / `graphite` / `charcoal` / `wood`) |
| `recolour` only | `background` defaults to `keep`: the workbench in the shot stays **byte-identical** and only the plastic shifts (Lab constant offset, measured sharpness ratio 0.98) |
| a photo the QA refuses | the ORIGINAL file ships and the note says "left as shot (…)", so the restyle can never push the listing under the ≥3 rule |
| a typo in either option | `ValueError` naming the valid values, before a single photo is opened (the plan route returns it as a 400) |

Edited files are written beside the photograph as
`<stem>_edited[_<background>][_<colour>].png` (plus `_mask`/`_repair` sidecars) and
the default wood run still writes `<stem>_edited.png`, so the photograph on disk is
never overwritten. Restyling happens in `POST /upload/makerworld` **before**
`UPLOAD_LOCK` is taken: about 4 s per photo at 200×200, off the browser's clock, so
a slow image cannot stall the wizard.

### The AI studio (`/makerworld/studio`) — the one page that draws, not edits

`makerworld/ai_studio.html`, served by `GET /makerworld/studio`. It calls
`puter.ai.txt2img(prompt, {input_image: <base64 data url>})` from the browser: no
API key is stored or needed (Puter asks for its own free account sign-in), and the
source picture goes as a data URI, so nothing has to be hosted publicly. Results
are saved by `POST /makerworld/ai/save`, which writes into `downloads/` under
`ai_<stem>_<timestamp>.png` — the prefix is the point: a generated picture can
never be mistaken in the photo picker for a photograph of a real print. The route
takes a data URL only (`data:image/(png|jpeg|webp);base64,`), rejects anything else
with a 400, caps the write at 20 MB, and flattens the name through `Path().name`
plus a character filter, so `../../windows.png` cannot escape `downloads/`.

The prompt is **standing**, not written fresh each time: `buildPrompt()` always
says "remove every word, letter, number, logo, badge and watermark from the
image; keep the object itself identical: same shape, same proportions, same
walls, same layer lines; 〈change the printed object to X filament〉; replace the
background with 〈backdrop〉; photorealistic product photograph, soft studio
light, sharp focus on the object". The colour and backdrop come from the two
selects, the free-text box only appends, and the sentence builder runs on page
load so the box is never empty. A test pins the three fixed clauses to the
unconditional part of the list, so none of them can quietly become optional.

What this page does **not** do: it is not wired into the publish flow, it is not
run by the Auto loop, and it publishes nothing. `tests/test_makerworld.py` holds
those three facts (own page, labelled as AI, `ai_` prefix, traversal blocked, bad
data url → 400). The dashboard links to it from card 3 with the wording "draws a
picture from a prompt instead of editing one you took".
