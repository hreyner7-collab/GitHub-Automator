# Gates: publish to MakerWorld, learned from MakerWorld, without disturbing Creality

OWNS: makerworld_flow.py, makerworld_ledger.py, makerworld_uploader.py,
photo_critic.py, makerworld/index.html, scripts/check_mw_provenance.py,
scripts/check_mw_filters.py,
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
  EXPECT: 41 passed
  EVIDENCE: 2026-10-25, exit 0 — `40 passed, 3 warnings in 1.43s` before the debug-browser
  gate was added; the file now collects 41, and the new gate passed in the 13-test
  selection (`13 passed, 28 deselected`). The first run of this gate was `31 passed`. Eight
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
  found (5). It publishes with `dry_run=False` and a picture set **drawn for that
  listing** as `print_photos` (changed 2026-10-26, see G12 — it used to send the
  source listing's own photographs), and it refuses to start while another run is
  publishing (one browser, one form). `POST /makerworld/auto/start {"count":0}` → 400 before any browser is touched,
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
  FIXED 2026-10-25: the first unattended run after that (count 1) skipped both designs it
  tried — the log shows `logged-in download failed: Error: BrowserType.connect_over_cdp:
  connect ECONNREFUSED 127.0.0.1:9222`, i.e. Chrome's debug port was closed and nothing had
  started it, while the dashboard blamed a login. `POST /makerworld/auto/start` now calls
  `makerworld_ensure_browser()` before announcing the run (503 with a plain reason if it
  cannot), `downloader._mw_debug_browser()` repeats that guard on the download path, and the
  item's `why` carries the downloader's real `file_error`. Verified live with the port
  closed: `_mw_download_via_chrome('3464636', …)` opened Chrome, saved a 1.6 MB 3MF
  (`PK` header) and left the port up.

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

- [x] G12: every picture on a MakerWorld listing is drawn for it — no original image reaches the form
  **SUPERSEDED 2026-10-26 by G14.** The user reversed the *content* of this rule the
  next day — "The remixes are not getting the exact same image. All I want is for it
  to be the exact same model. And just a changed background… actually get the model
  and copy and paste it" — so the default picture mode is now `same_model`, not
  `generate`. What still stands, and is what G14 builds on: the pictures are settled
  **before the browser opens**, `photo_mode` fails closed, the acknowledgement ticks
  are answered by the route, and a set that comes up short stops the publish instead
  of being padded with someone else's photograph.
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "draw or photo or duplicate or cc0 or Plan or tick"
  EXPECT: 25 passed
  EVIDENCE: 2026-10-26, exit 0 — `25 passed, 41 deselected in 6.13s`; the whole file is
  `66 passed in 6.11s` and the full suite `594 passed in 91.44s`. Asked for by the user ("whenever I click upload or find and publish, it
  will always regenerate… not a single original image, only new ones", MakerWorld only). Both
  buttons settle their pictures **before the browser opens**: `photo_mode="generate"` is the
  default and sends `image_paths: []` and `print_photos: []`, so the scraped gallery and the
  ticked photographs are not in the payload at all. `photo_gen` (new) draws from the listing's
  own name and description through the keyless free endpoint; `split_photo_sets` puts the covers
  and the print-photo slot on different files; a short set is a 502 that publishes nothing
  (`test_a_short_draw_refuses_the_publish_and_opens_no_browser` runs the real route with the
  driver and the ledger both watched and asserts neither was touched) and in the loop the row
  state `photos-failed`, which stops that find and continues the run. Measured live on 26 Oct:
  3 pictures in 186 s, one 402 recovered by the back-off. The endpoint ignores `nologo=true` and
  stamps its name in the lowest 5% of the frame, so `_clean` cuts that strip (band detail
  3954 → 3.1 on a real drawing) and the page says so. `photo_mode` fails closed
  (`_makerworld_photo_mode`: a typo is not a licence to attach another maker's photo), the plan
  route checks the set that will be drawn (`photos_drawn`) instead of the empty file lists,
  `/makerworld/files` names both kinds of non-print file (`generated`, `from_source`) and the
  picker refuses to tick either as "a print I made", and a duplicate verdict only locks the
  ledger when it names a design that is not the draft. The two acknowledgements are answers the
  route requires, not button plumbing: `assert_ai` for `generate`, `assert_prints` for
  `as_shot`/`restyle`, and the Auto card's own tick for the unattended start — each missing is a
  400 raised *before* a picture is drawn, `ensure_browser()` runs or the form driver is called
  (`test_nothing_is_sent_or_started_without_the_tick` watches all three).

- [x] G13: the dashboard's own run reaches Chrome, fills the form and publishes
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "cover or frozen or wake or wall or tick"
  EXPECT: 9 passed
  EVIDENCE: 2026-10-26, exit 0 — `9 passed, 62 deselected in 3.16s`; the whole file is
  `71 passed in 7.66s` and the full suite `599 passed in 93.85s`. Asked for by the user:
  *"open the browser and click it… monitor what it does step by step… you say 'oh, it got
  stuck here, I'll fix it'… stop when it successfully uploads, because at the moment it's
  just not uploading."* Two causes, both measured on the live account, neither visible in
  the row text the run produced.
  **(a) A wedged tab hung the Chrome attach.** `connect_over_cdp` initialises every page
  target before returning, so one discarded background tab in `chrome_debug_profile` meant
  it never returned, and the download's 180 s `join()` expired first and discarded the real
  reason — which is why eight runs said *"the download did not arrive within 3 minutes"*
  while the same work by hand succeeded. `makerworld_uploader.cdp_attach()` bounds it
  (25 s × 3 tries) and wakes every page target with `PUT /json/activate/<id>` between
  attempts; the same attach came back in **0.3 s**. The download now records the stage it
  reached and says *"stalled while <stage>"* instead of inventing a timeout.
  **(b) The cover was attached to the model-file input.** On live draft 10203098 the Raw
  Model Files box carries the same `.dropzone` class as the 4:3 cover, so the root
  `.dropzone:not(.appUpload)` resolved to file input **0** and `.dropzone.appUpload` matched
  **nothing** (`appUpload` is the dropzone's parent). `set_input_files` reports success on
  the wrong input, so the run pressed Publish against an empty required box and MakerWorld
  answered *"Please set the model cover"* — the same note the ledger carries for the
  Christmas Gnome run at 16:19. Both roots are now scoped inside the covers section
  (`.js-scroll-cover .dropzone:not(.appUpload .dropzone)` → input 2, `.appUpload .dropzone`
  → input 3) and the wizard asks the form itself, through `COVER_STATE_JS`, whether the box
  MakerWorld marks `require` holds an image; if not it stops and names the box.
  LIVE, after both fixes and nothing else changed: `POST /makerworld/auto/start {"count":2,
  "assert_ai":true}` clicked from the page ended `done` with **"2 of 2 published"** — *Handl
  For The Jar* and *Cthulhu Halloween Cosplay Mask*, each reaching `/en/@<user>/verifying`
  and recorded `published` with `pending_review: True`. Of the two rows that did not
  publish, one is the CC0 refusal working as designed and the other is a real MakerWorld
  miss (`TimeoutError: 90000ms exceeded while waiting for event "download"` — the site
  offered no file for that design), now reported with the stage named.
  FOUND AND NOT TOUCHED: both publishes return the same `/verifying` URL, so the ledger's
  identity merge folded the two designs into **one** row (its `identity.source_urls` holds
  both). `find(source_url=…)` still answers FOUND for each, so nothing will be published
  twice, but the row's name, `source_url` and `count: 9` misreport two listings as one, and
  `forget()` would drop both together. A pending-review `published_url` is not an identity
  key and should not be merged on.

- [x] G14: the listing shows the source's own model, with only the background changed — and a panel that looks at it
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "cut or critic or panel or same_model or gallery_files or without_the_tick or cuts_the_source_model or run_loop or auto_run or the_run or photo_mode"
  EXPECT: 20 passed
  EVIDENCE: 2026-10-26, exit 0 — `20 passed, 72 deselected in 10.55s`; the whole file is
  `92 passed in 20.09s` and the full suite `620 passed in 118.61s`. Asked for by the user:
  *"All I want is for it to be the exact same model. And just a changed background,
  that's it. Like, actually get the model and copy and paste it"* and then
  *"Run multiple sub-agents. Looks at the original image and the new image, and it says
  'oh, that doesn't look right' and then it tells you what to do… I need to change the
  background to a different color."*
  **The model is copied, not drawn.** `photo_mode="same_model"` is the default on the page
  (V12) and in the Auto loop. `main._makerworld_source_files()` takes the source listing's
  gallery (`{slug}_img_N` under `downloads/`, on disk, an image the form accepts — nothing
  else, and never an already-cut `_edited` file), and
  `makerworld_flow.cut_out_photos()` → `photo_critic.improve()` → `editor.edit_image()`
  copies the product's pixels and repaints only around them. The backdrop rotates
  (white / gray / graphite / charcoal / wood) so a set is not one frame five times, and
  `keep` is refused by name in this mode — keeping the source's background *is* sending the
  source's photograph. `recolour` is never passed: the plastic keeps the colour it was
  photographed in.
  **The critics look, and name a remedy.** `photo_critic.py`: eight independent
  measurements of the shipped file beside its source — the model in the new picture
  is the one the photograph shows (IoU of a fresh segmentation of the **source** vs
  the mask the composite used, ≥ 0.55, and reported as unjudgeable when the
  photograph itself will not segment), shape (the same against the **output**, ≥
  0.85 — the old 0.90 sat on the segmenter's own run-to-run noise and refused good
  cuts), background moved (ΔE ≥ 18), backdrop flat (L-std ≤ **18**), colour kept
  (ΔE ≤ 9), coverage (6–95%), no words (≤ 0.15%), no leftovers (≤ 0.10%, with the
  contact-shadow band around the model excluded on purpose). A failure is not a log
  line: `background` and `remask` → rebuild the cut on the sweep furthest from both
  the plastic and the photo's own backdrop, from the ones not yet spent on this
  picture (the finisher is deterministic — measured, two runs on one backdrop are
  byte-identical, so a retry that keeps the backdrop can only repeat the verdict);
  `drop` → stop at once, because another backdrop cannot argue with a fact about the
  photograph. A picture that still fails is **dropped and named** — never sent as
  the source's untouched file.
  **The flatness limit is a measurement, not a guess.** It was 26 and a reviewer
  showed no shipped picture came near it. Re-measured on the composition masks of 20
  real pairs: shipped outputs 4.1–12.3 (4.5–15.1 across a whole panel run); the
  failure it names, the photographed backdrop pasted back into the far field,
  21.9–30.8. 18.0 sits in the gap, so every picture ever posted still passes and the
  case the critic describes now refuses itself. A *small* leftover patch (5.3 on this
  statistic) is the leftovers critic's job, and catches there.
  Live on a real listing photo: attempt 1 measured shape **0.73** → *"does not look
  right"* → rebuilt → shape **0.95**, passed. On the synthetic pair in the test: clean
  measures 0.955 / move 55.7 / colour 0.0 / leftovers 0.00%, and the same file with a
  150×100 block of the old backdrop painted back in measures **10.93%** and is refused
  with `remask` — the control is what proves the detector is not decoration.
  **The refusals are the route's.** `assert_source` is demanded before any cut runs (400,
  and `assert_ai` is not a substitute); fewer than four trustworthy source pictures is a
  409 that names the count; a panel that refused too much is a 502 that says a refused
  picture is never sent as the source's own file; and in every case the watched driver and
  the watched ledger are both still empty. The loop's row carries `pictures: "same model,
  new background"` and the panel's per-picture verdicts in `looked_at`, so the dashboard
  shows the argument rather than a claim. `/makerworld/files` marks a finisher output as
  `from_source` too, so the picker can tell the user which files came off someone else's
  listing.
  NOT YET DONE: no listing has been *published* in this mode — the pipeline was run locally
  over the real photos in `downloads/`, and a live publish needs the user to ask for it
  (AGENTS.md). The page is V12 and the server has not been restarted, so what is served
  right now is still the pre-reviewer page and routes.

- [x] G15: the page and the route answer the same question about the same pictures
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_makerworld.py -k "moved_copy or studio_drawing or ticked_five_times or plan_route_answers or cancelled_auto or undecodable or nan or never_read or another_spelling or never_loaded or spliced or javascript"
  EXPECT: 12 passed
  EVIDENCE: 2026-10-26, exit 0 — `12 passed, 80 deselected in 3.72s`. Three review agents
  read the route, the panel and the page against the
  measured rules; every finding below was reproduced here before it was fixed, and
  each fix has a test that fails without it.
  **The panel was re-measured end to end after the changes** — the same fourteen listing
  photographs, the same backdrop rotation: **6 of 14 accepted**, the same six as passes 3
  and 4, 5 pictures retried over 9 extra attempts, and **no picture dropped by the
  tightened flatness limit** (the accepted set measured 4.5–12.9 against 18.0). That run
  is the control that says a critic given a real threshold did not quietly cost the route
  its yield.
  **Leaks, all four ways a picture that is not the user's print could reach the
  print slot.** The own-photo guard had checked the *name* and the *folder*, so a
  copy of `downloads/slug_img_1.png` placed anywhere else on the disk walked in as
  the user's own photograph of their print — the name is now the whole test
  (`test_a_moved_copy_of_a_scraped_photograph_is_still_refused`). A studio drawing
  (`ai_*`) was refused by the click handler and accepted by the route. Repeats filled
  the slot — five ticks of one photograph were "5 views covering 3 angles" — and one
  file could sit in the cover and the photo slot together, which is the shape the
  form answers with *"Please set the model cover"*; lists are deduplicated, the slots
  kept disjoint, the pair capped at the measured 16. And the finisher's recolour tag
  (`…_img_1_edited_gray_red`) was one level deeper than the pattern allowed, so a
  restyled cut was classified as somebody's own photograph.
  **Check-plan disagreed with publish, twice.** The page ships no cover paths in any
  mode, and the plan route did not do the cover split the publish route does, so
  every own-photo check answered "no cover image" — a check that always fails is a
  check nobody reads. And a gallery spliced from two listings raised its `ValueError`
  through the plan route with nothing to catch it: HTTP 500 where the sentence about
  two source galleries was owed.
  **A cancelled MakerWorld run left the dashboard saying `running`** — the worker
  cleared its state in `except Exception`, and `asyncio.CancelledError` is a
  `BaseException` — so the overlap guard refused every later manual publish with 409
  until the loop was started and stopped again. The `finally` now lands on
  `stopped` / `interrupted`. `/makerworld/ai/save` answered an undecodable base64
  body with a 500 for the same kind of reason.
  **The page could publish the wrong design.** `_src` held whatever the last scrape
  returned, so typing another listing's URL into the box and pressing publish sent
  the FIRST design's model file, description and licence as a remix of the second.
  Every read of the scrape goes through one function that compares it against the
  box, and the box's keystroke re-runs the readiness check. Along the same line:
  ticks whose files have since gone or been named a drawing are pruned when the
  folder is read, the backdrop picker is hidden in the mode that discards its value,
  the "4 of the source's own pictures" count is the files really on disk once the
  folder has been read, the CC0 refusal is shown when the listing loads instead of at
  publish, and the drawer's arithmetic on the page is the route's clamp rather than
  an invented `photo_count + 2`.
  FOUND AND NOT CHANGED: `/makerworld/files` reads only `downloads/`, so a file the
  user keeps elsewhere is not in the picker at all — which is the reason the route
  must remain the guard rather than the listing.

<!--
Notes for whoever works this ledger:
- The one finding that changes what the pipeline can publish: MakerWorld requires
  photographs of an actual print ("Upload ≥3 photos of the printed model, covering 3
  views"), and this pipeline's edited images are studio composites of the creator's
  renders. `MIN_REAL_PHOTOS = 3` and a separate `print_photos` input exist so the
  account is never asked to present a render as a print photo. The manual dashboard
  exposes that as an explicit tick. CHANGED TWICE 2026-10-26, at the user's instruction
  and after they asked MakerWorld support: first nothing but a drawn set (G12), then —
  on the same day — the current rule (G14): the listing shows **the source's own model**
  cut out of the source's own photograph with the background rebuilt here
  (`photo_mode="same_model"`, the default), and every result is measured against its
  source before it is used. See MAKERWORLD-FLOW-CONFIG.md.
  The rule that survived both changes is the one that matters for the account: a file
  is never presented as something it is not. The untouched photograph of another maker
  is never attached, a drawn picture cannot be ticked as "a print I made", the
  acknowledgement for the mode in use is on the publish button **and demanded by the
  route** (as is the unattended run's own tick, before it starts), and a set that comes
  up short stops the publish instead of being padded with someone else's photograph.
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
