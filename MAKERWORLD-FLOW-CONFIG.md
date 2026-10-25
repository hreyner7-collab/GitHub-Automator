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

**Model Name** `0/50` · **Tags** `Tags (0 / 50)` with a `0/100` input and
`Press "Enter" to separate tags` · **Category** "Quick search by entering
category keywords" · **Description** and **Model Videos** ("Max 30s,
mp4/mov"), **BOM**, **Community Post**, **Assembly Guide**, **Exclusive Model
Program** ("Not Eligible" for this account), **Visibility** `Public` / `Private`
(Public arrives checked).

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

**Description rule, measured:** "Please detail how you remixed the original
model. Otherwise, our moderators will remove your model." — enforced in
`plan_description`.

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
| category picker semantics (it is a keyword search) | filled only when a category is given; the form's own errors are reported |
| the `Publish` result page | success is **a design id in the URL** (`/en/models/<id>-<slug>`), not a toast |

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
| 1 · Source design (MakerWorld url) | `POST /fetch` with `edit_images:false` | never the AI re-edit — that is a Creality-side choice |
| covers grid | the scraped originals (`_raw_orig_images`) | never presented as print photos |
| 3 · Photographs of your prints | `GET /makerworld/files` → images already in `downloads/` | never fills itself from the gallery |
| 4 · Check | `POST /upload/makerworld/plan` | opens no browser, creates no draft |
| 4 · Publish (starts disabled) | `POST /upload/makerworld` | unlocks only with a name, a model file, a cover, ≥3 print photos **and** the assertion tick |
| 5 · Memory | `GET /makerworld/published`, `POST /makerworld/forget` | never reads or writes Creality's `published_models.json` |

The Publish button's lock is the measured rule, not a courtesy: MakerWorld's form
says "Upload ≥3 photos of the printed model, covering 3 views", so a listing that
cannot satisfy it is not offered. Forget needs a name, url or design id — an empty
match is a 400, never "forget everything".
