# MakerWorld pipeline — handoff

Written 2026-10-26. This file is the whole map of **one** pipeline: the MakerWorld
find-and-publish-as-a-remix flow served at **http://127.0.0.1:8000/makerworld**.

## Where it lives on this computer

```
C:\Users\henry\Documents\free\app\Cruality Pipeline
```

- Branch `main`, remote `origin` → `https://github.com/hreyner7-collab/GitHub-Automator.git`.
  Everything below is committed and pushed there.
- Python is the repo-local venv — there is no system python on this machine:
  `./.venv/Scripts/python.exe -X utf8 …`
- `downloads/` (git-ignored) holds the scraped source material: `{slug}_img_N.{png,jpg}`.

**Scope.** The folder also contains the older Creality Cloud pipeline (`/`, `/upload`,
`uploader.py`, `creality_api.py`, `creality_rules.py`, `ledger.py`, `GATES.md`). It is **not**
part of this handoff and the MakerWorld work borrows nothing from it: the two share no rule, no
page, no ledger and no route. Leave it alone.

## The rule this pipeline exists to satisfy

The user's words, verbatim:

> The remixes are not getting the exact same image. All I want is for it to be the exact same
> model. And just a changed background, that's it. Like, actually get the model and copy and
> paste it, basically.

So: **the model's own pixels come out of the source listing's photograph and only the backdrop is
rebuilt.** Nothing draws a new product, nothing recomputes it from a mesh, no Blender.

Two more standing rules:
- The listing's description is the **source's own text, word for word** — never a generated blurb.
- Publish as a **remix** with the source declared, unattended (the auto run), and **with no API
  key** — the platform work drives the user's already-logged-in Chrome over CDP.

## Files that ARE the MakerWorld pipeline

| file | what it does |
|---|---|
| `main.py` | the FastAPI app. MakerWorld routes: `/upload/makerworld/plan` (:889), `/upload/makerworld` (:961), `/makerworld` (:1152), `/makerworld/studio` (:1160), `/makerworld/ai/save` (:1176), `/makerworld/files` (:1206), `/makerworld/published` (:1240), `/makerworld/forget` (:1247), `/makerworld/auto/start` (:2263), `/makerworld/auto/stop` (:2306), `/makerworld/auto/status` (:2312) |
| `makerworld_flow.py` | licence-filtered discovery, the photo rules (`MIN_REAL_PHOTOS = 3`, `MAX_PHOTOS = 16`, `CUT_BACKGROUNDS = white/gray/graphite/charcoal/wood`), the already-published memory, the remix form's measured facts |
| `photo_critic.py` | **the eight critics** below, plus `improve()`, the retry that moves the backdrop |
| `editor.py` | the cut itself: rembg + GrabCut candidates, contact shadow, text-band detector, its own QA that raises when every candidate fails. Deterministic. `BACKGROUNDS` at :75 |
| `makerworld_uploader.py` | drives Chrome over CDP through the 4-step wizard, attaches files, ticks the licence, submits |
| `makerworld_ledger.py` | `artifacts/makerworld_published.json` (override with `MAKERWORLD_LEDGER`) |
| `photo_gen.py` | the `generate` mode only: a keyless free-endpoint drawer for listings that have no usable photograph |
| `makerworld/index.html` | the page — **`MakerWorld Pipeline V12`**. Read from disk on every request, so an HTML edit is live without restarting the server (a `.py` edit is not) |
| `makerworld/ai_studio.html` | the AI studio, a deliberately separate page; nothing publishes from it |
| `tests/test_makerworld.py` | **92 behavioural tests**, no browser, no account contact |
| `MAKERWORLD-FLOW-CONFIG.md` | what MakerWorld's form really does, every threshold with the measurements behind it, and the pass-by-pass picture yield |
| `GATES-MAKERWORLD.md` | acceptance gates G1…G15: exact command, `EXPECT:`, and the measured evidence |

State right now: the server on port 8000 (started 26/10 23:11:50, after the last code write) answers
`GET /makerworld` with 200 and the V12 page, so nothing here needs a restart.

## The eight critics (every cut is measured against its own source photograph)

From `photo_critic.py:61-79`. A failing check always names the remedy it wants.

| check | what it measures | limit | remedy |
|---|---|---|---|
| model in the photo | IoU: fresh u2net segmentation of the **source photo** vs the mask the composite used | ≥ 0.55 | remask |
| shape | IoU: fresh segmentation of the **shipped file** vs the same mask | ≥ 0.85 | remask |
| background | mean ΔE between the old backdrop and the new one, outside the model | ≥ 18.0 | move to another sweep |
| backdrop is clean | std of the new backdrop's lightness | ≤ 18.0 | move to another sweep |
| colour kept | mean ΔE of the model's own pixels, before vs after | ≤ 9.0 | drop |
| coverage | share of the frame the model fills | 6–95% | remask |
| no words | verified text surviving **outside** the model | ≤ 0.15% | drop |
| no leftovers | pixels still matching the source's backdrop tone, away from the model | ≤ 0.10% | remask |

Two things that are easy to get wrong and are load-bearing:
- **The finisher is deterministic**, so retrying on the same backdrop is a no-op. `improve()`
  therefore always moves the sweep (`white → gray → graphite → charcoal → wood`); `grey` is an
  alias, not a second attempt.
- **Text on the product is not a rejection.** Only `editor._verify_text_bands` may alter the
  product, and words are measured outside the mask, so a keychain or a stamped lid passes.

## Photo modes

`main.py:721-726`: `OWN_PHOTO_MODES = ("as_shot", "restyle")`, `MW_PHOTO_MODE = "same_model"`,
`ALL_PHOTO_MODES = (same_model, generate, as_shot, restyle)`.

- **`same_model`** — what the page's default option and the auto run both send. Cut the model out
  of the source photograph, rebuild only the backdrop, judge with the panel.
- **`generate`** — stays the request field's default **on purpose**: it is the only mode that
  cannot attach a picture nobody here made, so an unnamed or misspelled mode fails closed to it
  (`_makerworld_photo_mode`, `main.py:729`).
- **`as_shot` / `restyle`** — the user's own photographs, untouched or run through the finisher.
- Server-side, not just page-side: `assert_source` / `assert_ai` / `assert_prints` are each checked
  in the route, and the gallery guard (`_SOURCE_FILE_NAME`, `main.py:749`) refuses a scraped photo —
  or a moved copy of one, or any `_edited`/`_mask`/`_repair` variant however deep the recolour tag
  goes, or an `ai_` studio drawing, or the same file ticked twice — as an "own print".

## Checks to run before believing any change

```bash
cd "C:/Users/henry/Documents/free/app/Cruality Pipeline"
./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_makerworld.py -q          # 92 passed
./.venv/Scripts/python.exe -X utf8 -m pytest -q                                    # 620 passed
./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_makerworld.py -q -k "critic or plan or refused or ticked or cancelled"   # 17 passed — the reviewer-round gates
C:/Users/henry/bin/bun -e "new Function(require('fs').readFileSync(process.argv[1],'utf8'))" makerworld/index.html   # page JS, no browser
```

No test opens a browser or touches an account.

## The honest numbers

Measured over 5 full passes of the panel against **14 real listing photographs**:

- **6 of 14 come out right.** That is the yield, and tightening the flatness limit from 26 → 18
  did not change it (same six, 0 drops naming that critic).
- Five of the eight that go missing are refused **by the finisher itself** — every candidate mask
  fails its own QA before the panel is ever shown a picture. The rest fail on a fact a backdrop
  cannot change.
- A listing whose surviving cuts are fewer than three stops with `photos-failed` and the count in
  the sentence; the run skips it rather than padding with somebody else's photo.
- Cost per picture: median 15–17 s, worst 274 s.

Do not "fix" this toward a 100% claim. If a future change reports a yield, it must be measured the
same way, against real photographs, with the failures named.

## Open — and blocked on the user, not on code

1. **Nothing has been published through `same_model` yet.** The auto run and any publish need the
   user to ask for it in the moment; doing it unbidden breaks the standing rule.
2. **`downloads/butterfly_knife_trainer_img_0.jpg`** makes the finisher raise `Could not read
   image` — a decode failure on a real download. It is currently reported as a finisher refusal,
   which is honest but coarse. Splitting "unreadable file" from "failed QA" is a small, contained
   change in `editor.py`.
3. `/makerworld/files` only reads `downloads/`, so the **route** must stay the guard about what is
   a real own-photo; a page-only check is not enough.

## Rules the next agent must keep

From `AGENTS.md` in this folder (read it, it is the authority):
- Do not open, visit, navigate or screenshot any web page or browser tab, and do not start an
  upload, an auto run or a publish, unless the user asks for that action **in the current message**.
- Do not restart the dashboard server, and do not start anything that drives the browser, unless
  asked.
- Running tests, reading files and editing code is always allowed.
- When any change is finished, commit it and push to `origin main` **before** reporting it done.
- Stage files by name. Never `git add -A`, never `git commit -a`.
- Every update to the dashboard bumps its version marker by one (V12 today).
- Never persist an API key the user pastes into chat; use it inline and tell them to roll it.
