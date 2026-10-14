# Creality Cloud upload flow — the mapped truth (2026-10-10)

This is the map the pipeline uses for `uploader.py`. It was measured by
**actually driving the real form over CDP** (agent-browser against the logged-in
Chrome on port 9222, with screenshots in `debug/ab_step1.png` →
`debug/ab_remix_state.png`). When an update breaks an upload, come back here
before touching selectors.

## Step 1 — Upload Files (`/create-model-new?source=12` → redirects `/create-model?source=12`)

- **File-type radios** (`label.el-radio`, first two):
  - "Print Configuration File (…)" — DEFAULT checked on load.
  - The pipeline must click **"STL/CAD files or other types of 3MF files"** or the model-file input is absent.
- Section chain: `section.first-step > div.content-box.creality-print-content > div.upload-3mf-container > div.upload-3mf > div.el-upload.el-upload--text > div.el-upload-dragger` with `input.el-upload__input type=file name="file" accept=".stl,.obj,.ply,…" multiple`.
- **Attaching works via element.set_input_files() on that input** (`DOM.setFileInputFiles`). Element UI then **clears `input.files` (length 0)** while it starts the **real OSS multipart upload** (PUT/POST to `internal-creality-usa.oss-…/file3mf/…partNumber=… + uploadId=…`, all 200).
  - **Never verify the attachment by `input.files.length`** — verify by the page text (the file row shows name + size) or by the OSS uploads in the network log.
- **Next** = a `generic` div (no button role), text "Next". Clicking advances to step 2 once the file is registered.
- In-page pre-check: `document.body.innerText` contains the filename — still correct (the file row shows name+size).

## Step 2 — Fill in the information

`section.second-step`. All labels are **form-language dependent**; drive the ENGLISH form:

- **Model Source radios** (`label.el-radio`): Original / **Remix Models** / Non-original.
  - Click by **plain `.click()` on the label element (DOM click)** — measured to check the radio AND reveal the source input. Older "Element UI is temperamental" behavior was a tooling artifact; the DOM click works (2026-10-10).
- **Source-model search input**: `input[placeholder^='Paste the URL or directly search for models']`. Type with the native-value setter + `input` event (must bubble).
- Suggestions appear in **`.origin-choose-list` → `.origin-item`** rows (two-column grid): rounded thumbnail + name + "Author…".
  - Rows the form refuses carry a red sentence: "The license policy does not allow for derivative works or modifications".
- **Click the row whose NAME text equals the search value exactly** (e.g. "dumpling squishy clicker"), NOT prefix look-alikes ("Dumpling Clicker", "dumpling squishy clicker v2"). Row identity is the exact name; the form then renders the pill.
  - Selectors that match suggestion rows by `indexOf`-prefix or by position pick the wrong look-alike and register the wrong source.

## The registered-source pill (root cause of the 4 failed uploads)

After a row is accepted, the form renders the declared source as a **pill**:

```
div.origin-list > div.origin-item.flex-center
    .origin-name   -> model name text
    div.url        -> the full source URL as TEXT (https://www.crealitycloud.com/model-detail/<slug>)
```

**`_registered_mark()` now reads this pill FIRST** (uploader.py, 2026-10-10). The old code read only `.origin-choose-mark` (a card only some builds render beside the still-open suggestion list), found nothing on this build, so `declares_our_source()` returned `declared: false` — and the auto-run refused honestly with
`"Refusing to publish without declaring the source model (Remix Models)"`.

`.origin-choose-mark` remains as the fallback (older builds). `.origin-choose-list .origin-item` rows are SUGGESTIONS and must never be read as "registered".

## Failure reasons seen in auto_run items → real causes → fixes

| reason | real cause | fix |
|---|---|---|
| "the publish form never reached step 2" | step-2 never became visible on older retries | step-1 (files) is re-clicked up to 3× (R23); attaching a real file advances automatically |
| "Remix Models radio never revealed the source input after 4 attempts" | radio checked, but the `Paste the URL` input stayed hidden in some builds | DOM-click the radio label (measured 2026-10-10 to reveal it) |
| "the form does not show OUR source model" | `_registered_mark()` read `.origin-choose-mark` only | **reads the `div.origin-list` pill first** (this fix) |

## Photos, cover, licence, consent (unchanged)

- TWO REQUIRED cover slots: first two image inputs of the cover card (web 4:3, then app 3:4); both must be filled.
- The gallery is the `multiple` input inside `.upload-cover-last`.
- Licence card: `LICENSE_BADGE_JS` scoped to the block containing **both** the adaptations question and "This work is licensed under". Badge answers [Yes/Yes/Yes]=CC0, [Yes/No/No]=CC BY, [SA/No/No]=CC BY-SA.
- Consent: `label.el-checkbox` "I have read and agree to Copyright Statement." — tick state read from the row's checkbox + `is-checked`.

*Written 2026-10-10 by the agent that walked the form live; screenshots saved in `debug/`.*
