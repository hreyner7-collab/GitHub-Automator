# Dashboard audit — what was checked, what was found, what was fixed

Scope: the whole Python surface of the dashboard (11 modules, 9,590 lines before
this work) plus the served page (`creality/index.html`, 1,114 lines), targeted at
one outcome: **the dashboard is always reachable, always runs the code you think
it runs, and never silently does the wrong thing.**

## How the audit was done (so a reader can re-run it)

| Method | Command | What it finds |
|---|---|---|
| Static analysis | `.venv/Scripts/python.exe -m pyflakes main.py discover.py uploader.py downloader.py editor.py ledger.py analytics.py proof.py creality_rules.py selector.py timing.py verify.py page_model.py dashboard_guard.py browser_opener.py` | undefined names, unused/duplicate imports, shadowing, dead locals |
| Structural sweep | `tests/test_no_blocking_probes.py` | blocking helpers called from `async def` (freezes every request) |
| Pattern sweep | `grep -rn "write_text(" \| grep -v ".tmp"` | writes that are not atomic (truncatable by a crash) |
| Pattern sweep | `grep -rn "except\s*:"` | bare excepts that swallow everything |
| Pattern sweep | `grep -rn "time.sleep("` | blocking sleeps on the request path |
| Behaviour | `tests/` (323 tests) | the pipeline's own rules, offline, no browser, no account |

**Honest coverage note.** Every module was read around its risk points and swept
by the tools above; I did not read all 9,590 lines one at a time. The three
`editor.py` dead locals below are the kind of thing a full line-by-line pass would
also have turned up — they are cosmetic, and they are recorded rather than
guessed at.

## Findings

### HIGH — blocking probes called from async code (8 sites) — FIXED

`_debug_port_up()` (a 2-second HTTP probe) and `ensure_browser()` (launches Chrome
and polls for **up to 20 seconds**, using `time.sleep`) are synchronous. They were
being called directly inside `async def`, so the whole event loop stopped: every
request from the browser — the run panel, the status poll, the health check —
hung for as long as it took. This is the same defect class the project hit before
(PLAN.md: "/fetch and /health blocked the event loop (froze the dashboard)").

Sites fixed: `main.py` `health()`, `main.py` `diagnostics()`, `uploader.py`
`upload()`, `analytics.py` `open_signin()`, `login_status()`,
`fetch_download_counts()`, `refresh()`, `_auto_loop()`. All now use
`await asyncio.to_thread(...)`.

Pinned by `tests/test_no_blocking_probes.py`, which parses the modules and fails
if a blocking helper is ever called from async code again — and which proves it
can fail by feeding itself the defect.

### HIGH — "sometimes it works, sometimes it doesn't" was an unverified-step problem — FIXED (as checks)

The pipeline advanced from step to step on the assumption the last one worked. The
project's own run logs show the cost: `artifacts/auto_run_20261010-143814.json`
refuses three models with *"the publish form never reached step 2, so the model's
source cannot be declared"* — a Next click that silently did nothing, discovered
only later, reported as a different failure.

Added `verify.py`: post-conditions read from ONE page read (`step_two_open`,
`name_applied`, `licence_rows_ticked`, `covers_present`, `consent_ticked`), plus a
bounded retry that redoes the action and reads the page back before carrying on.
Wired into the upload path at the step-2 gate (where it can skip three 2.5s waits)
and immediately before Submit, recorded in the upload's evidence.

Deliberately a **record, not a new hard gate**: a failing check is written into the
run log and the submit proceeds exactly as before. Promoting these to hard gates
needs one live run to confirm the checks agree with the site — that is the honest
next step, and turning them on blind could refuse a publish that works today.

### HIGH — the run log was written non-atomically — FIXED

`_log()` in `main.py` used a plain `write_text`. A crash mid-write truncates the
file, and that file is what seeds "already published" on the next start — so a
truncated log silently loses the memory of what is already on the account. Now a
temp file plus swap (`_write_run_log`), with serialization done *before* the swap
so an unwritable body leaves the previous good log untouched. Test:
`tests/test_timing.py::test_run_log_write_is_atomic_and_keeps_the_previous_log_on_failure`.

### HIGH — nothing owned the server's lifecycle — FIXED

No supervisor, no port check, no start at logon, no version anywhere, and
`scripts/_start_dash.ps1` launched it through `pythonw.exe` with every error
discarded. A dead or never-started server was indistinguishable from a broken
app. Fixed by `dashboard_guard.py`, `scripts/dashboard_supervisor.py`, the Startup
entry, the build stamp in `/health`, and the panel in the page header.

### MEDIUM — `verify` shadowing in `uploader.upload()` — FIXED

`upload()` has a long-standing local variable `verify` (the evidence dict it
returns). Adding `import verify` at module level made the new calls inside that
function resolve to the *local* dict, and every call was wrapped in `try/except`
— so the checks would have silently done nothing, forever, with no error anywhere.
Caught by pyflakes (`redefinition of unused 'verify' from line 28`), fixed by
importing under an alias (`import verify as step_verify`) with a comment saying
why. This is the audit's best advertisement for itself: a passing test suite would
not have noticed.

### MEDIUM — the proof sidecar was written non-atomically — FIXED

`proof.py` wrote `<slug>_proof.json` with `write_text`; a crash mid-write leaves
truncated JSON beside a perfectly good PNG. Now temp file plus swap.

### MEDIUM — fixed sleeps in the listing harvest — FIXED

The harvest slept a flat 2,500ms per pass (up to 200 passes per keyword) and only
then counted the cards, so even a listing that had finished loading cost 2.5s a
pass. Now one early probe (150ms) catches the common case, and anything slower
falls back to the same 2,500ms budget — so the worst case is unchanged and the
common case is much cheaper. Measured at the browser-call level in
`tests/test_harvest_cost.py`, which records the waits asked for and the reads
made, and documents the one honest trade (+1 read per pass where nothing grows).

### MEDIUM — `/health` launched a browser — FIXED

It ran `_chromium_available()` through `asyncio.to_thread`, which launches a
headless Chromium. Every poll of a health endpoint spawned a browser — slow for
the supervisor and for the page itself. `/health` is now fast and dependency-only
(`playwright_importable` via `find_spec`, no import, no launch), and the real
browser check lives at `/diagnostics/browser`, which runs only when clicked.
Pinned by `tests/test_diagnostics.py::test_the_fast_checks_never_start_a_browser`.

### LOW — dead assignments in `editor.py` — RECORDED, NOT FIXED

`editor.py:860 union_area`, `editor.py:951 p80_whole_only`, `editor.py:1057
best_mask` are assigned and never read. They are leftovers from an experiment
(the `EDITOR_DISABLE_COMPWIRE=1` branch also contains the no-op
`stats_comp = stats_comp`). Removing them cannot change behaviour, but it also
cannot improve it, and `editor.py` is a 1,101-line image pipeline whose output is
judged by eye. Recorded here rather than edited blind.

### RECORDED — measured opportunities that need one live run

- **The per-keyword 6s wait** after `page.goto` (`discover.py`, `listing.open`) is
  unconditional. It is timed now (`timing.phase("listing.open")`), so the next real
  run will say whether it can be replaced by a condition wait.
- **The 200-pass ceiling** per keyword: the scan normally ends by growth
  detection, but a listing that never stops growing would run the ceiling. The
  pass count and per-pass cost are now in the run log's `timings` table.
- **The uploader's remaining probes** (`CONSENT_JS`, `SUBMIT_JS`, `REJECTION_JS`,
  the 6-round Remix reveal): each is still its own round-trip. The page model is
  available to replace the *reading* of these; doing it blind, without a live run
  to confirm against the real form, is how a working publish gets broken.

## Found by verifying on the real machine (not by reading)

These four were invisible to every test the suite could run, and were found only
by restarting the dashboard and killing it on purpose. They are the strongest
argument for the plan's last step existing at all.

### HIGH — the launched PID was not the serving process — FIXED

On this venv `.venv/Scripts/python.exe` re-executes the real interpreter, so the
PID `Popen` returns is a thin WRAPPER and the dashboard that actually serves port
8000 is its child (observed: wrapper 11240 / server 11720). Stopping only the
tracked PID left a live server orphaned on the port while the supervisor reported
a dashboard it no longer owned. The supervisor now adopts the PID `/health`
reports, tracks both, stops both, and refuses to touch a process that is not
python/pythonw.

### HIGH — a phantom-alive PID could block startup forever — FIXED

`process_alive()` tested whether the PID's digits appeared *anywhere* in
`tasklist` output. `tasklist` prints memory sizes ("<1,234 K") and Windows
recycles PIDs, so a dead PID could read as alive. Both the supervisor's lock and
its "is my child still up?" check depend on that answer, and the effect was
reproduced: the supervisor refused to start and wrote nothing at all, while the
lock kept naming a PID that was gone. The PID column is now compared exactly
(`/FO CSV`), and a lock is respected only when its holder still looks like a
supervisor.

### HIGH — the launcher and the supervisor shared a log the server holds open — FIXED

The autostart batch appended its console to `artifacts/dashboard.log`, which the
running server holds as its stdout. `cmd`'s `>>` is denied while that handle is
open ("The process cannot access the file because it is being used by another
process"), so the launcher exited 1 inside a hidden window — the same class of
silent failure this whole audit is about. The supervisor's own writes were being
skipped on the same `except` path, so the decisions were missing from the log
exactly when a human went looking for them. Now: supervisor decisions in
`artifacts/dashboard_supervisor.log`, server output in `artifacts/dashboard.log`,
launcher console in `artifacts/dashboard_autostart_console.log`.

### MEDIUM — the tests wrote into the app's own artifacts — FIXED

The supervisor tests instantiated the real `Supervisor`, so running the suite
appended their fake decisions ("stopped PID 11720") to the real
`artifacts/dashboard.log` — the file a human reads to diagnose a real restart —
and took, then deleted, the real supervisor lock, which would let a second
supervisor start and fight the first over the port. The paths are redirected into
`tmp_path` by a fixture now, and the suite leaves the real artifacts
byte-identical (asserted).

## What is proven, and what is not

**Proven offline** (323 tests, no browser, no account): the port parser and the
restart policy; the atomic writes; the timing ledger; the one-call page model
including hidden, un-laid-out form controls; the post-condition checks and their
ability to catch the injected faults; the harvest cost model; the diagnostics
panel's checks and its promise never to start a browser on the fast path.

**Proven on the real machine** (this PC, port 8000): a killed server comes back by
itself with the current code in about 13 seconds; a supervisor started next to a
running dashboard adopts it instead of exiting or starting a second copy; the
launcher the Startup entry runs works while a server is up; `/diagnostics` reports
11 of 11 checks passing on the live server; the page carries the panel and the
build stamp.

**Not proven, and needs your next run:** that the live Creality form's page model
reads the way the checks expect, and that the step checks agree with the real site.
The instrumentation for that is in place — `UPLOAD_VERIFY["step_checks"]`,
`UPLOAD_VERIFY["page_model"]`, and the `timings` table in every run log.

**Known limitation of the build stamp:** it compares git revisions, so a commit
that changes only tests or scripts makes a running dashboard report "code changed
on disk — RESTART NEEDED". It over-warns rather than under-warns, which is the
safe direction for the question it answers ("is this process serving what I just
changed?"), and the fix is a stamp over the Python files themselves rather than
the revision.
