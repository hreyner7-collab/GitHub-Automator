# Gates: publish to MakerWorld, learned from MakerWorld, without disturbing Creality

OWNS: makerworld_flow.py, makerworld_ledger.py, makerworld_uploader.py,
makerworld/index.html, scripts/check_mw_provenance.py, scripts/check_mw_filters.py,
scripts/check_mw_licence_tokens.py, scripts/check_mw_licence_readback.py,
scripts/check_mw_licence_api.py, MAKERWORLD-STUDY/,
MAKERWORLD-FLOW-CONFIG.md, GATES-MAKERWORLD.md, main.py (its routes and the MakerWorld
auto run), discover.py (its MakerWorld listings and the licence read-back),
downloader.py (_fetch_makerworld's licence, description, category, tag and creator capture only),
creality/index.html (MakerWorld markup removed, marker bumped),
tests/test_makerworld.py, tests/test_originals_dashboard.py (de-pinned version marker)

Scope: give the pipeline a second publish target — MakerWorld — whose every
control name is traceable to a measurement taken off the live wizard on
2026-10-25, kept in its own modules, its own memory file and its own routes,
with the Creality target byte-for-byte unchanged. Nothing in these gates reads the account or
publishes a listing; G9 is the one gate that opens pages, and it opens them logged-out in a
throwaway headless Chromium, at the user's explicit request.

Baseline: 527 tests passing before this work; the Creality publish path
(`uploader.py` 2755 lines, `ledger.py`) as the only target.

- [x] G1: every control name the MakerWorld driver clicks or reads was measured, not invented
  CHECK: .venv/Scripts/python.exe -X utf8 scripts/check_mw_provenance.py --selftest
  EXPECT: MakerWorld provenance verification passed
  EVIDENCE: 2026-10-25, exit 0 — `checked 31 control names, all of them present in the
  measured dumps` against `evidence: 18 files in MAKERWORLD-STUDY/`, and the control
  ran first: `control refused as required: INVENTED_BUTTON ('Publish Everything Now')`,
  which proves the checker reports a fabricated selector instead of waving it through.
  The dumps are the wizard's own page state (`r01_open` → `r04_step2`), the network log
  of the walk (`remix_walk.log`, including `POST /api/v1/design-service/my/draft` →
  `{"id":10186871}`), the nav Upload menu's real hrefs (`upload_menu.json`) and the
  drafts list before/after the probe was deleted.

- [x] G2: MakerWorld's own requirements are refused before a browser opens
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py
  EXPECT: 40 passed
  EVIDENCE: 2026-10-25, exit 0 — `40 passed, 3 warnings in 1.43s` (re-run after the
  description gates landed; the first run of this gate was `31 passed`). Eight
  refusal cases each assert the plan blocks (no model file, wrong extension, no cover,
  fewer than 3 print photos, remix with no source, a Thingiverse source under
  `?type=remix`, no name, ND licence). Two more prove the refusal does not touch Chrome:
  they monkeypatch `makerworld_uploader._debug_port_up` to record calls and assert
  `calls == []`. A source row is chosen by name, not position (`pick_source_row`): the
  exact row wins even when a prefix look-alike is listed first, and a numeric design id
  only matches as a whole token, so `1043887` does not select `10438871`.

- [x] G2b: no control the driver reaches for is undefined — checked without a browser
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "driver or defined or control"
  EXPECT: 2 passed
  EVIDENCE: 2026-10-25, exit 0 — `2 passed, 28 deselected in 0.57s`. Rewriting the Model
  Origin block left it calling four JS constants (`SOURCE_ROW_SELECTOR`, `SOURCE_ROWS_JS`,
  `SOURCE_ROW_CLICK_JS`, `ORIGIN_STATE_JS`) that were never defined: a NameError that only
  fires inside the live wizard, after a draft already exists. The test parses the driver
  with `ast` and asserts every module-level UPPER name it loads is assigned. Proven
  against the real defect, not just
  a synthetic one — running the same checker over the driver with those four definitions
  cut out reports exactly `['ORIGIN_STATE_JS', 'SOURCE_ROWS_JS', 'SOURCE_ROW_CLICK_JS',
  'SOURCE_ROW_SELECTOR']`, and over the committed driver `[]`.

- [x] G3: the pipeline does not publish the same model to MakerWorld twice, and that
      memory is not the Creality one
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "ledger or memory or retryable or unlock or identities"
  EXPECT: 6 passed
  EVIDENCE: 2026-10-25, exit 0 — `6 passed, 24 deselected in 1.91s` (two of the six are the
  dashboard's own memory route, which reads and unlocks this file and never the Creality
  one). A MakerWorld listing is matched by source URL, listing URL,
  numeric design id, name+designer or the model file's sha256; `outcome: "failed"` stays
  retryable while `published` locks; `forget()` is the only unlock. The separation test
  asserts `ledger.ledger_path()` differs from the MakerWorld file and that a MakerWorld
  record leaves `ledger.entries()` with no trace of the design id.

- [x] G4: the Creality target is untouched
  CHECK: git status --porcelain -- uploader.py ledger.py editor.py creality_api.py discover.py timing.py page_model.py
  EXPECT: (no output)
  EVIDENCE: 2026-10-25 — empty. The shared files edited are `main.py` (additive
  MakerWorld routes and the auto run), `creality/index.html` (MakerWorld markup removed)
  and `downloader.py`, whose diff is confined to `_fetch_makerworld`, its new MakerWorld
  helpers (`_mw_download_via_chrome`, `_mw_html_to_text`, `_mw_api_design`) and its return
  shape; no Creality fetch function is touched, which the full suite (G5) exercises.
  `tests/test_originals_dashboard.py` used to pin the version marker to the literal `V16`,
  which contradicts AGENTS.md's bump rule; it now asserts the `<title>` and `#ui-version`
  agree, whatever the number is, so the next bump cannot break it.

- [x] G5: the repair did not break the run (full suite, browser-free)
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q
  EXPECT: 567 passed
  EVIDENCE: 2026-10-25, exit 0 — `567 passed, 3 warnings in 87.32s` (527 before this work;
  +40 in tests/test_makerworld.py, the rest from the description/API gates; the 3 warnings
  are FastAPI `on_event` deprecations that predate this work).

- [x] G6: the dashboard serves the build that contains this work and says so
  EVIDENCE: 2026-10-25, re-measured after the Model Origin rewrite — `/health` answers
  `pid=6224 code_fingerprint=53105cd1910c disk_fingerprint=53105cd1910c`, i.e. the running
  process serves exactly the code on disk, which is this work. `GET /` contains
  `Creality Pipeline V17` and `makerworld-section`, and the `#ui-version` span renders the
  same V17, and
  `POST /upload/makerworld/plan` on the live server answers
  `{"ok":false,"blockers":["requirements.txt is not a format MakerWorld's step 1
  accepts…","no cover image to upload…","only 0 real print photo(s); MakerWorld requires
  at least 3 photographs of the printed model…"` — a real refusal from the running build,
  with no browser opened.
  CLOSED: after the description work landed the supervisor restarted on its own and
  `/health` answers `build=5d50f19+edits pid=12948 code_fingerprint=df93d46284c0
  disk_fingerprint=df93d46284c0 build_is_current=true` with
  `checks.browser_debug_port_up=true`; `GET /makerworld` serves
  `<title>MakerWorld Pipeline V4</title>`, the `#mw-version` badge reads `V4`, the
  Description copy says "copied from the source listing", and neither
  `slice(0, 2000)` nor "added automatically" is in the served page.

- [x] G7: the gaps in the study are declared, not papered over
  EVIDENCE: MAKERWORLD-FLOW-CONFIG.md carries a "What is NOT measured" table, and each
  gap has a runtime behaviour instead of an assumption: the licence answer→name mapping is
  read back from the form and a mismatch refuses the submit (`LICENSE_READ_JS` is scoped
  between the Bambu Handy question and Visibility because the Model Origin warning *names*
  CC-BY-ND and CC-BY-NC-ND as examples); the remix-source suggestion markup is reported in
  `verify.origin.rows`, the row is clicked only while it still reads the picked name (if
  MakerWorld re-renders the list between reading and clicking, the driver refuses rather
  than clicking an unknown row), and nothing publishes until the Model Origin block is
  re-read and found to name the source; success is a design id in
  the URL, not a toast.

- [x] G8: MakerWorld has its own dashboard at its own address, and Creality's has none of it
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "(page or route) and not step1"
  EXPECT: 7 passed
  EVIDENCE: 2026-10-25, exit 0 — `7 passed, 33 deselected in 1.19s`. `makerworld/index.html` is its
  own page (`MakerWorld Pipeline V1` in its `<title>`, its own `#mw-version` marker that the
  test requires to agree with it), wired only to `/upload/makerworld*`, `/makerworld/files`,
  `/makerworld/published`, `/makerworld/forget` — and asserted NOT to contain
  `fetch('/upload'`, `/edit-images`, `/auto/start`, `/published/sync` or `crealitycloud`, so
  it cannot be the Creality form wearing new labels. The Creality page is asserted the other
  way round: no `/upload/makerworld`, no `makerworld-section`, no `print_photos`, only one
  `href="/makerworld"` link. Live on the running server: `GET /makerworld` → HTTP 200 with
  `<title>MakerWorld Pipeline V1</title>`, `GET /` → HTTP 200 with
  `<title>Creality Pipeline V18</title>` and no MakerWorld card,
  `GET /makerworld/published` → `{"count":0,…,"path":"…artifacts\\makerworld_published.json"}`
  (its own file, still empty because nothing has been published), and
  `GET /makerworld/files` listing the real images in `downloads/` with absolute paths.
  The publish button ships `disabled` and only unlocks on a name + model file + cover + ≥3
  print photos + the assertion tick; `POST /makerworld/forget` with a blank match is a 400,
  never a wipe.
  Step 0 of the page finds models the way the Creality dashboard does — `POST /discover` with
  `site:"makerworld"`, which reads MakerWorld's trending/popular/hottest listing in a throwaway
  headless Chromium (no login, no session of yours, nothing downloaded) and hands the clicked
  card to the remix source box. A test asserts that function's body contains no `/upload`,
  `/fetch` or `/edit-images`: that card only ever lists — publishing is the separate Auto
  control in G10. Re-measured 2026-10-25 after G9/G10 landed: `7 passed, 29 deselected in
  1.28s`. The ban list is now anchored on the call site (`('/auto/`) because
  `/makerworld/auto/start` ends in the Creality route's own name, and the page's marker is
  `MakerWorld Pipeline V4`.
  CLOSED: the discovery scrape has now been exercised live — see G9, which is the measurement.

- [x] G9: discovery only ever lists designs whose licence permits the remix
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "licence or licene or filter or discovery"
  EXPECT: 4 passed
  EVIDENCE: 2026-10-25, exit 0 — `4 passed, 32 deselected in 0.10s`. Measured off the live
  listing with a logged-out headless Chromium (`scripts/check_mw_filters.py`,
  `check_mw_licence_tokens.py`, `check_mw_licence_readback.py`): MakerWorld's own filter writes
  the URL param `licenses=`, whose tokens are the chip labels verbatim (`licenses=BY` was read
  straight off the address bar after one click) except Public Domain, which is `CC0`. All three
  ranked listings in `discover.SITES["makerworld"]` now carry
  `&licenses=CC0,BY,BY-SA`, and `discover()` reads the panel back before harvesting: the three
  wanted chips must be `active` and the five others must not, or it raises instead of returning
  a list. That read-back is not decoration — an unrecognised `licenses=` value is silently
  ignored by the site, which is exactly how the first attempt (`licenses=CC0,CC-BY,CC-BY-SA`,
  the names MakerWorld uses in its own prose) came back with only Public Domain green and a full
  page of unfiltered models.
  Live confirmation: `discover.discover("makerworld", 6)` returned 6 designs, and reading the
  source pages of four of them showed `creativecommons.org/licenses/by/4.0/` on at least one —
  remixable. Three of the four named no licence in text at all, because MakerWorld renders it as
  a CC *link*, not a CC *name*; that is why `downloader._fetch_makerworld` now reads the anchors
  and why an unreadable licence stops an item in the batch (G10) rather than falling back.
  MakerWorld's own words, measured in `MAKERWORLD-STUDY/r04_step2_dump.json`: "Please make sure
  the source model's license does NOT contain 'NoDerivatives' attribute. For example CC-BY-ND,
  CC-BY-NC-ND, Standard Digital File License are not allowed."

- [x] G10: type a number, it finds that many and publishes them
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "run_loop or auto_run or the_run"
  EXPECT: 3 passed
  EVIDENCE: 2026-10-25, exit 0 — `3 passed, 33 deselected in 1.11s`.
  `main._makerworld_auto_worker` + `POST /makerworld/auto/start|stop` + `GET
  /makerworld/auto/status`, mirroring the shape of `_auto_worker` but calling only the
  MakerWorld modules (a test greps the worker's own body for `uploader_upload`, `edit_all`,
  `creality_rules` and a bare `ledger.` and requires none). The whole loop is driven in a test
  with fakes — no Chrome, no network, no account — over five finds, and the states come back
  `published, skipped, rejected, skipped, published`: the CC BY source publishes, the page that
  names no licence stops, the BY-ND source is refused with MakerWorld's own sentence, the design
  with no downloadable file stops, and the run ends at the number typed (2), not the number
  found (5). It publishes with `dry_run=False` and the source listing's own photographs as
  `print_photos`, and it refuses to start while the Creality run is publishing (one browser,
  one form). `POST /makerworld/auto/start {"count":0}` → 400 before any browser is touched,
  measured live: `Type how many models you want, 1 or more (you sent 0).`
  LIVE: a real listing has now gone through the wizard end to end at the user's explicit
  request (2026-10-25). Design **3495491** was published from the source's own photographs
  and file, passed MakerWorld's review, and appeared at
  `https://makerworld.com/en/models/3495491-mini-bookshelf-whimsy-fairy` in Published Models
  (1). The measured end state: `Publish` opens the **"Publish your model"** sync dialog,
  the CC-only option + Confirm produce **"Model published & synced!"**, and the account
  lands on `/en/@<user>/verifying` — success is that toast **or** the verifying URL, and the
  ledger records it as published with `pending_review: True`. The edit path is different and
  now mapped too: `/en/my/models/<id>/edit` → Publish opens **"Model Data Changed?"**
  (default "Yes. Users must be notified…"), and a description-only change answers **No** →
  Confirm. Both dialogues are in MAKERWORLD-FLOW-CONFIG.md.

- [x] G11: the listing carries the source's own description, unchanged, on the dashboard and in a batch
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "description"
  EXPECT: 3 passed
  EVIDENCE: 2026-10-25, exit 0 — `3 passed, 37 deselected in 0.14s`. `plan_description`
  returns the source text only; the text comes
  from the public design API's `summary` (`<boostcontent>` inside `<boostme>`), not from
  rendered HTML, and the driver types it line by line and records `same_as_source`.
  `makerworld/index.html` prefills the field with that text **untruncated**, its copy says
  "copied from the source listing" instead of promising an added credit line, and the Auto
  worker passes the fetched description/category/tags straight through. The live proof is
  design 3495491: its listing now reads exactly the source's sentence
  ("As i've made this model available for commercial use, if you enjoy it, please leave a 5
  star review or give a boost!") — edited on the owner edit page, saved, and the model went
  back to Verifying (1) for re-review.

<!--
Notes for whoever works this ledger:
- The one finding that changes what the pipeline can publish: MakerWorld requires
  photographs of an actual print ("Upload ≥3 photos of the printed model, covering 3
  views"), and this pipeline's edited images are studio composites of the creator's
  renders. `MIN_REAL_PHOTOS = 3` and a separate `print_photos` input exist so the
  account is never asked to present a render as a print photo. The manual dashboard
  exposes that as an explicit tick; the auto run (G10) satisfies it a different way — it
  uploads the photographs the SOURCE listing already carries, taken by the maker of the
  original, and stops on any design that has fewer than three. No image API and no
  generated picture is part of either path.
- MakerWorld's `?type=remix` only accepts a source that is already a MakerWorld
  design (measured placeholder text), so a Thingiverse/Printables original can only
  go out as `?type=original` — the flow module refuses the mismatch instead of
  letting the form fail late.
- MakerWorld's guidelines (committed copies in MAKERWORLD-STUDY/) ban unauthorised
  distribution and automated manipulation, with takedown and suspension as the
  stated consequence. The driver fills the form the way a person does, in the
  user's own logged-in Chrome, one listing at a time. As of 2026-10-25 the user asked
  for the batch to run unattended (G10) — no per-item approval — so the "does this
  listing go out" decision is theirs, taken once for the run, and the automated
  manipulation the guidelines ban is precisely what this now does. The account
  suspension risk is real, it is theirs to weigh, and nothing in the loop re-asks.
  What the loop still refuses on its own are the things a person would be asked to
  certify: a source whose licence forbids derivatives or commercial use, a source
  whose licence it cannot read, a design with no file, and a design with fewer than
  three of the original maker's own photographs.
- The live account HAS now been exercised at the user's explicit request (2026-10-25):
  one listing published (design 3495491), passed review, then corrected to carry the
  source's own description through the owner edit page, which returned it to Verifying.
  The study walk's throwaway draft 10186871 was deleted through the site's own
  `DELETE /api/v1/design-service/my/draft/10186871` and the re-list answered
  `{"hits":[],"total":0}`. Later publishes should still start with
  MAKERWORLD_NO_SUBMIT=1 when the wizard itself is being changed.
-->
