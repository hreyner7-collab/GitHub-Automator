# Gates: publish to MakerWorld, learned from MakerWorld, without disturbing Creality

OWNS: makerworld_flow.py, makerworld_ledger.py, makerworld_uploader.py,
scripts/check_mw_provenance.py, MAKERWORLD-STUDY/, MAKERWORLD-FLOW-CONFIG.md,
GATES-MAKERWORLD.md, main.py (two new routes), creality/index.html (one new card),
tests/test_makerworld.py, tests/test_originals_dashboard.py (de-pinned version marker)

Scope: give the pipeline a second publish target — MakerWorld — whose every
control name is traceable to a measurement taken off the live wizard on
2026-10-25, kept in its own modules, its own memory file and its own routes,
with the Creality target byte-for-byte unchanged. Nothing in these gates opens a
browser, reads the account, or publishes a listing (AGENTS.md).

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
  EXPECT: 24 passed
  EVIDENCE: 2026-10-25, exit 0 — `24 passed in 0.27s`. Eight
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
  EVIDENCE: 2026-10-25, exit 0 — `2 passed, 22 deselected in 0.18s`. Rewriting the Model
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
  EXPECT: 4 passed
  EVIDENCE: 2026-10-25, exit 0 — `4 passed, 20 deselected in 0.14s`. A MakerWorld listing is matched by source URL, listing URL,
  numeric design id, name+designer or the model file's sha256; `outcome: "failed"` stays
  retryable while `published` locks; `forget()` is the only unlock. The separation test
  asserts `ledger.ledger_path()` differs from the MakerWorld file and that a MakerWorld
  record leaves `ledger.entries()` with no trace of the design id.

- [x] G4: the Creality target is untouched
  CHECK: git status --porcelain -- uploader.py ledger.py editor.py downloader.py creality_api.py discover.py timing.py page_model.py
  EXPECT: (no output)
  EVIDENCE: 2026-10-25 — empty. The only shared files edited are `main.py` (two new
  routes, additive, plus the `UPLOAD_LOCK` comment corrected now that the lock covers
  both targets) and `creality/index.html` (one new card). `tests/test_originals_dashboard.py`
  pinned the version marker to the literal `V16`, which contradicts AGENTS.md's bump rule
  and failed on V17; it now asserts the `<title>` and `#ui-version` agree, whatever the
  number is, so the next bump cannot break it.

- [x] G5: the repair did not break the run (full suite, browser-free)
  CHECK: .venv/Scripts/python.exe -X utf8 -m pytest -q
  EXPECT: 551 passed
  EVIDENCE: 2026-10-25, exit 0 — `551 passed, 3 warnings in 98.77s` (527 before; +24 in
  tests/test_makerworld.py; the 3 warnings are FastAPI `on_event` deprecations that
  predate this work).

- [x] G6: the dashboard serves the build that contains this work and says so
  EVIDENCE: 2026-10-25, re-measured after the Model Origin rewrite — `/health` answers
  `build=2490943+edits pid=6224 code_fingerprint=53105cd1910c disk_fingerprint=53105cd1910c
  build_is_current=true` (the supervisor reloaded itself on the code change; no restart was
  asked for or done), `GET /` contains
  `Creality Pipeline V17` and `makerworld-section`, and the `#ui-version` span renders the
  same V17, and
  `POST /upload/makerworld/plan` on the live server answers
  `{"ok":false,"blockers":["requirements.txt is not a format MakerWorld's step 1
  accepts…","no cover image to upload…","only 0 real print photo(s); MakerWorld requires
  at least 3 photographs of the printed model…"` — a real refusal from the running build,
  with no browser opened.

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

<!--
Notes for whoever works this ledger:
- The one finding that changes what the pipeline can publish: MakerWorld requires
  photographs of an actual print ("Upload ≥3 photos of the printed model, covering 3
  views"), and this pipeline's edited images are studio composites of the creator's
  renders. `MIN_REAL_PHOTOS = 3` and a separate `print_photos` input exist so the
  account is never asked to present a render as a print photo. The dashboard exposes
  that as an explicit tick the user must make.
- MakerWorld's `?type=remix` only accepts a source that is already a MakerWorld
  design (measured placeholder text), so a Thingiverse/Printables original can only
  go out as `?type=original` — the flow module refuses the mismatch instead of
  letting the form fail late.
- MakerWorld's guidelines (committed copies in MAKERWORLD-STUDY/) ban unauthorised
  distribution and automated manipulation, with takedown and suspension as the
  stated consequence. The driver fills the form the way a person does, in the
  user's own logged-in Chrome, one listing at a time, and never presses Publish
  unless the user asked for that listing.
- Nothing here was exercised against the live account. The study walk attached one
  throwaway STL, which created draft 10186871; it was deleted through the site's own
  `DELETE /api/v1/design-service/my/draft/10186871` and the re-list answered
  `{"hits":[],"total":0}`. First real publishes should run with
  MAKERWORLD_NO_SUBMIT=1.
-->
