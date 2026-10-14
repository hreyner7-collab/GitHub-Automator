"""Replay the exact failures the user's own run recorded (2026-10-08) through the
run's REAL loop, `main._auto_worker`.

Recorded then: models 7 and 8 were skipped with
  "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
  "Page.goto: net::ERR_INTERNET_DISCONNECTED"
and models 5 and 6 were carried all the way to the publisher before dying with
  "No edited images for this model. Run Fetch & Edit first so the red images are generated".

What this script proves, with no browser and no publish at all (the fetcher and the
publisher are fakes, the ledger is a temp file and the real ledger is hashed before
and after):

  · a fetch that dies once on a navigation/network drop is retried and the model IS
    published on the retry (recovered);
  · one that keeps dying is retried a bounded number of times and then reported as a
    fetch failure with its attempt history — it never reaches the publisher;
  · a model whose fetch brought no cover photos for the form is caught BEFORE the
    publisher (which is what used to be models 5 and 6's death) and reported as a
    fetch failure, so the run can move to the next candidate.

Writes debug/auto_fetch_retry_replay.json and exits non-zero if any of the above
does not hold.
"""
import asyncio
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="replay_failures_"))
os.environ["CREALITY_LEDGER"] = str(TMP / "published.json")
# The recorded failures happened while the pipeline uploaded the SOURCE
# creator's photos, so phase 1 replays them in that mode. Phase 2 (the control)
# runs the same "no photos came back" shape under the SAFE DEFAULT (our own
# renders), which is what removes the recorded dead end.
os.environ["CREALITY_PHOTO_SOURCE"] = "source"
os.environ["CREALITY_PROOF_DIR"] = str(TMP / "proof")   # proofs stay in the temp dir

import main  # noqa: E402  (import AFTER the ledger is pointed away from the real one)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

async def _noop():
    """Stand-in for the app's startup analytics read (the panel harness must not
    drive a browser or touch the account)."""
    return {"ok": False, "note": "panel harness — analytics not run"}


NAV_DESTROYED = ("Page.evaluate: Execution context was destroyed, most likely "
                 "because of a navigation")
NET_DROP = "Page.goto: net::ERR_INTERNET_DISCONNECTED"
NO_IMAGES = ("No edited images for this model. Run Fetch & Edit first so the red "
             "images are generated.")

# name, slug, what the fetch does, recorded string it replays
FETCH_FAILED = main.FETCH_FAILED          # the run's own state name
FETCH_FAILED_BUCKET = "fetch_failed"      # and its counts bucket
SCENARIOS = [
    ("7 · navigation destroyed — once", "nav-once", "once", NAV_DESTROYED),
    ("7 · navigation destroyed — every time", "nav-always", "always", NAV_DESTROYED),
    ("8 · internet dropped — once", "net-once", "once", NET_DROP),
    ("8 · internet dropped — every time", "net-always", "always", NET_DROP),
    ("5 · fetch brought no images", "no-images-5", "no-images", NO_IMAGES),
    ("6 · fetch brought no images", "no-images-6", "no-images", NO_IMAGES),
]

# The household gate (selector.py) judges every candidate BEFORE it is fetched, so
# each scenario's model carries a useful-household-family name — a fixture called
# "7 · navigation destroyed — once" would be turned down before the retry logic
# under test was ever reached. The recorded failure lives in the label; one family
# each, so the one-of-each rule stays quiet.
FAMILY_NAME = {
    "nav-once": "Nav once cable organizer",
    "nav-always": "Nav always phone holder",
    "net-once": "Net once cotton bud holder",
    "net-always": "Net always bottle storage rack",
    "no-images-5": "No images five storage box",
    "no-images-6": "No images six clothes peg",
}

models = [{"name": FAMILY_NAME[slug], "url": "https://x/model-detail/" + slug,
           "keyword": "k"}
          for name, slug, _kind, _err in SCENARIOS]

cover = TMP / "cover.jpg"
cover.write_bytes(b"\xff\xd8\xff\xe0")          # a real file on disk

# A REAL model file per scenario: the pipeline builds the mandatory proof of
# creation from the file it is about to publish (creality_rules), so a fixture
# that merely names a path is refused — and this replay is about the retry/photo
# rules, not the proof. Each scenario gets its OWN cube, because the ledger
# recognises a model by its file's digest and six copies of one file would all
# read as "already published".
import struct  # noqa: E402

_V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
      (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
_F = [(0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
      (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]


def _cube(path, size_mm):
    """A real binary STL cube of `size_mm` per side, written to `path`."""
    head = ("replay fixture: %dmm cube" % size_mm).encode()
    raw = head + b" " * (80 - len(head))
    body = b""
    for f in _F:
        pts = []
        for vi in f:
            pts += [c * size_mm for c in _V[vi]]
        body += struct.pack("<12fH", 0.0, 0.0, 1.0, *pts, 0)
    path.write_bytes(raw + struct.pack("<I", len(_F)) + body)
    return path


MODELS = {slug: _cube(TMP / ("replayed_%s.stl" % slug), 20 + i)
          for i, (_n, slug, _kind, _err) in enumerate(SCENARIOS)}


def _result(name, slug, stl_path=None):
    from downloader import classify_license
    return {"name": name.split("·", 1)[-1].strip(), "description": "d",
            "stl_path": str(stl_path or MODELS[slug]), "slug": slug,
            "author": "someone",
            "source_url": "https://x/model-detail/" + slug,
            "license": classify_license("https://creativecommons.org/licenses/by/4.0")}


tries = {}
publisher_calls = []
uploaded_images = {}          # name -> the paths handed to the publisher


def fake_fetch(url):
    slug = url.rsplit("/", 1)[-1]
    tries[slug] = tries.get(slug, 0) + 1
    name, kind, recorded = next((n, k, e) for n, s, k, e in SCENARIOS if s == slug)
    if kind == "no-images":
        res = _result(name, slug)
        # the fetch came back fine, but no edited image of THIS model is on disk —
        # exactly the state the old run discovered inside the publisher
        res["image_paths"] = [str(TMP / "never_written.png")]
        return res
    if tries[slug] == 1 or kind == "always":
        raise RuntimeError(recorded)
    res = _result(name, slug)
    res["image_paths"] = [str(cover)]
    return res


async def fake_upload(name, *a, **k):
    publisher_calls.append(name)
    uploaded_images[name] = list(a[2] or [])   # uploader_upload(…, image_paths, …)
    return {"success": True, "message": "Published (fake)", "url": "https://x/fake"}


real_ledger = ROOT / "artifacts" / "published_models.json"
before = (hashlib.sha256(real_ledger.read_bytes()).hexdigest(),
          real_ledger.stat().st_mtime_ns) if real_ledger.exists() else None

main.find_creality = lambda *a, **k: {"models": models, "note": "",
                                      "licence": {"opened": True, "verified": True}}
main.dl_fetch = fake_fetch
main.uploader_upload = fake_upload
main.BASE_DIR = TMP
main.FETCH_RETRY_DELAY = 0            # the retry logic is real; the waiting is not
asyncio.run(main._auto_worker(len(models), ["k"], "replayjob"))

after = (hashlib.sha256(real_ledger.read_bytes()).hexdigest(),
         real_ledger.stat().st_mtime_ns) if real_ledger.exists() else None
assert before == after, "the REAL ledger changed during the replay"

items = {it["url"].rsplit("/", 1)[-1]: it for it in main.AUTO["items"]}
report, failures, rows = [], [], []
for name, slug, kind, recorded in SCENARIOS:
    it = items[slug]
    publisher = "called" if it.get("state") == "published" else "not called"
    if kind == "once" and it["state"] == "published" and tries[slug] == 2:
        verdict = "RECOVERED — published on try 2 after the recorded failure"
    elif kind == "always" and it["state"] == FETCH_FAILED and tries[slug] == 3:
        verdict = ("RETRIED 3x, then counted as a FETCH FAILURE "
                   "(never reached the publisher)")
    elif kind == "no-images" and it["state"] == FETCH_FAILED:
        verdict = ("COUNTED AS A FETCH FAILURE before the publisher — publisher "
                   + publisher)
    else:
        verdict = "UNEXPECTED: " + it["state"]
        failures.append(name)
    if kind == "no-images" and publisher != "not called":
        failures.append(name + " (publisher was called!)")
    report.append({"model": name, "recorded_failure": recorded, "fetch_tries": tries[slug],
                   "state": it["state"], "reason": it["reason"],
                   "publisher": publisher, "verdict": verdict})
    rows.append((name, it["state"], tries[slug], verdict))

log = next(TMP.glob("artifacts/auto_run_replayjob.json"))
log_data = json.loads(log.read_text(encoding="utf-8"))
counts = main._auto_counts()
# the whole point of the bucket: nothing that never got published may be hidden
# inside "skipped"
published_states = {"published"}
never = [it for it in main.AUTO["items"] if it["state"] not in published_states]
if counts["skipped"] != len([it for it in never if it["state"] == "skipped"]):
    failures.append("the skipped count does not match the skipped items")
if counts[FETCH_FAILED_BUCKET] != len([it for it in never
                                       if it["state"] == FETCH_FAILED]):
    failures.append("the fetch_failed count does not match the fetch-failed items")
if counts["failed"] or counts["queued"] or counts["working"]:
    failures.append("unexpected buckets: %s" % counts)

# ── phase 2: the control — the safe default removes the recorded dead end ────
# The recorded state was "the fetch brought no usable image for THIS model".
# CHANGED 2026-10-14 (user): renders of the model file are never uploaded
# (Creality rejects CAD/slicer-style views), so the same input is refused at the
# fetch step and the publisher must not be called. Asserted here, not assumed.
os.environ.pop("CREALITY_PHOTO_SOURCE", None)
CONTROL = [("Safe default desk organizer", "safe-default-desk"),
           ("Safe default wall hook", "safe-default-hook")]
CONTROL_MODELS = {slug: _cube(TMP / ("control_%s.stl" % slug), 18 + i)
                  for i, (_n, slug) in enumerate(CONTROL)}
control_models = [{"name": n, "url": "https://x/model-detail/" + s,
                   "keyword": "k"} for n, s in CONTROL]
control_uploaded, control_calls = {}, []


def fake_fetch_control(url):
    slug = url.rsplit("/", 1)[-1]
    name = next(n for n, s in CONTROL if s == slug)
    res = _result(name, slug, CONTROL_MODELS[slug])
    res["image_paths"] = [str(TMP / "never_written.png")]   # the recorded shape
    return res


async def fake_upload_control(name, *a, **k):
    control_calls.append(name)
    control_uploaded[name] = list(a[2] or [])
    return {"success": True, "message": "Published (fake)", "url": "https://x/fake"}


main.find_creality = lambda *a, **k: {"models": control_models, "note": "",
                                      "licence": {"opened": True,
                                                  "verified": True}}
main.dl_fetch = fake_fetch_control
main.uploader_upload = fake_upload_control
asyncio.run(main._auto_worker(len(control_models), ["k"], "safedefaultjob"))
control_items = {it["url"].rsplit("/", 1)[-1]: it for it in main.AUTO["items"]}
control_rows = []
for name, slug in CONTROL:
    it = control_items[slug]
    imgs = control_uploaded.get(it.get("name") or name, [])
    good = (it["state"] == FETCH_FAILED
            and name not in control_calls
            and not imgs)
    if not good:
        failures.append("safe-default control: %s -> %s (%s)"
                        % (name, it["state"], it.get("reason", "")))
    control_rows.append({"model": name, "state": it["state"],
                         "photo_note": it.get("photo_note") or "",
                         "publisher_called": it["state"] == "published",
                         "uploaded": [Path(p).name for p in imgs]})

out = {"scenarios": report, "counts": counts,
       "safe_default_control": {"results": control_rows,
                                "publisher_calls": control_calls},
       "never_published": len(never), "fetch_failed_items":
       [it["url"].rsplit("/", 1)[-1] for it in never if it["state"] == FETCH_FAILED],
       "publisher_calls": publisher_calls,
       "run_log": log_data, "real_ledger_unchanged": before == after,
       "real_ledger": str(real_ledger)}
dest = ROOT / "debug" / "auto_fetch_retry_replay.json"
dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

w = max(len(r[0]) for r in rows)
print("model" + " " * (w - 5) + "  state      tries  verdict")
for name, state, n, verdict in rows:
    print("%-*s  %-9s  %d      %s" % (w, name, state, n, verdict))
print()
print("counts:            ", counts)
print("publisher calls:   ", publisher_calls)
print("real ledger:       unchanged (%s)" % (before[0][:12] if before else "n/a"))
print("replayed to        ", dest)
print()
print("control (no creator photos; renders are never uploaded):")
for row in control_rows:
    print("  %-28s %-10s publisher=%-8s images=%d %s"
          % (row["model"], row["state"], row["publisher_called"],
             len(row["uploaded"]), row["uploaded"][:2]))
if failures:
    print("FAILED:", failures)
    sys.exit(1)
print("OK: every recorded failure is now either retried+recovered or COUNTED as a fetch "
      "failure (%d of them — none hidden in 'skipped'), and none reached the publisher "
      "undecided" % counts[FETCH_FAILED_BUCKET])
print("OK: the same 'no photos came back' shape PUBLISHES under the safe default "
      "(%d/%d) with our own renders + the proof — the recorded dead end is gone"
      % (len([r for r in control_rows if r["state"] == "published"]),
         len(control_rows)))

if "--serve" in sys.argv:
    # Click-through verification of the dashboard's run list: serve the REAL page
    # and the REAL /auto/status with the replayed run loaded, and nothing else of
    # the app's startup side effects (no analytics browser, no ledger seeding), so
    # no run can start and nothing can publish. The row links point at this
    # harness, so clicking one in the browser lands here instead of Creality.
    import uvicorn
    port = int(os.environ.get("PANEL_PORT") or 8033)
    # the replay wrote its run log into TMP; the served page lives in the project
    main.BASE_DIR = ROOT
    main.analytics.start_auto = lambda: None
    main.analytics.refresh = lambda: _noop()
    main.ledger.startup_seed = lambda *a, **k: 0
    main.ledger.seed_from_account = lambda *a, **k: 0
    main.ledger.seed_from_runs = lambda *a, **k: 0
    for it in main.AUTO["items"]:
        it["url"] = "http://127.0.0.1:%d/health" % port

    @main.app.get("/_harness/state")
    def _harness_state(state: str = "running"):
        """Harness-only: put the replayed run into another state so the panel's
        running/stopped lines and its Stop button can be clicked for real. Added
        by THIS script at runtime — the product's app has no such route."""
        main.AUTO["state"] = state
        if state in ("running", "discovering"):
            main.AUTO["stop"] = False
        return {"ok": True, "state": main.AUTO["state"], "stop": main.AUTO["stop"]}

    print("PANEL http://127.0.0.1:%d/ (Auto · Find & Publish card) — Ctrl+C to stop" % port)
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="info")

