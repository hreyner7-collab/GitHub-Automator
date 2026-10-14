"""R10: the pipeline NEVER publishes the same model twice.

The ledger is its permanent memory (artifacts/published_models.json) and it is
consulted before anything is uploaded — by hand or by a run. These tests use a
tmp ledger (CREALITY_LEDGER) and no browser at all.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ledger  # noqa: E402

# A REAL model file on disk: /upload builds the mandatory proof of creation from
# the file it is about to publish (creality_rules), so a body that only names a
# path is refused — the fixtures below use this one.
REAL_MODEL = str(Path(__file__).resolve().parent / "data" / "proof_cube.stl")


@pytest.fixture(autouse=True)
def _tmp_ledger(monkeypatch, tmp_path):
    """Every test gets its own ledger file — the real one is never touched."""
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    # ...and its own proof-render dir, so a test never writes into downloads/.
    monkeypatch.setenv("CREALITY_PROOF_DIR", str(tmp_path / "proof"))
    yield
    monkeypatch.delenv("CREALITY_LEDGER", raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test may resolve a real redirect. `canonical_url` is the only thing in
    the ledger that opens a socket, so unless a test passes its own resolver it
    returns the link unchanged — deterministic, offline, and it also covers
    main._record_published, which resolves the published link the same way."""
    real = ledger.canonical_url
    monkeypatch.setattr(
        ledger, "canonical_url",
        lambda url, timeout=12.0, resolver=None:
        real(url, timeout=timeout, resolver=resolver or (lambda u: u)))


# ── identity: what counts as "the same model" ────────────────────────────────

def test_the_same_link_in_any_spelling_is_the_same_model():
    assert ledger.normalise_url("https://www.crealitycloud.com/model-detail/x/") == \
        ledger.normalise_url("http://crealitycloud.com/model-detail/x?from=search#a")
    assert ledger.normalise_url("WWW.CrealityCloud.com/model-detail/X") == \
        "crealitycloud.com/model-detail/x"
    assert ledger.normalise_url("") == ""


def test_slug_is_read_from_the_listing():
    assert ledger.slug_of("https://www.crealitycloud.com/model-detail/Toothbrush_Stand/") == \
        "toothbrush-stand"
    assert ledger.slug_of("") == ""


def test_names_keep_their_own_identity_even_in_russian():
    """A name in Cyrillic must not collapse to an empty key — that would make
    every such model look identical and block legitimate new ones."""
    a = ledger.name_key("Домкрат для подъема поддонов 1/10-го масштаба")
    b = ledger.name_key("Ключница домой")
    assert a and b and a != b
    assert ledger.name_key("Toothbrush stand") == ledger.name_key("toothbrush  STAND")


def test_file_fingerprint_is_the_geometry_not_the_path(tmp_path):
    from PIL import Image
    p1 = tmp_path / "one.3mf"
    p2 = tmp_path / "somewhere" / "else" / "renamed.3mf"
    p2.parent.mkdir(parents=True)
    p1.write_bytes(b"PK\x03\x04 first 3mf")
    p2.write_bytes(b"PK\x03\x04 first 3mf")
    assert ledger.file_digest(str(p1)) == ledger.file_digest(str(p2))
    p2.write_bytes(b"PK\x03\x04 a different model")
    assert ledger.file_digest(str(p1)) != ledger.file_digest(str(p2))
    assert ledger.file_digest(str(tmp_path / "missing.3mf")) == ""


# ── the ledger itself ────────────────────────────────────────────────────────

def test_a_published_model_is_never_published_again(tmp_path):
    ledger.record(name="Toothbrush stand", author="IceMarine",
                  source_url="https://www.crealitycloud.com/model-detail/toothbrush-stand",
                  published_url="https://www.crealitycloud.com/model-detail/toothbrush-stand-2")
    # same link, however it is written
    assert ledger.find(source_url="crealitycloud.com/model-detail/toothbrush-stand/")["found"]
    # the same listing under a different host/path spelling keeps its slug
    hit = ledger.find(source_url="https://3d.crealitycloud.com/model-detail/toothbrush-stand")
    assert hit["found"] and hit["matched_on"] == "slug"
    # the same name from the same designer
    hit = ledger.find(name="Toothbrush  stand", author="icemarine",
                      source_url="https://www.crealitycloud.com/model-detail/brand-new-slug")
    assert hit["found"] and hit["matched_on"] == "name"
    assert "Already published" in ledger.describe(hit)


def test_the_same_model_file_under_a_new_name_and_link_is_still_the_same_model(tmp_path):
    f = tmp_path / "model.3mf"
    f.write_bytes(b"PK\x03\x04 identical geometry")
    ledger.record(name="Broom Storage Holder", author="someone", stl_path=str(f))
    hit = ledger.find(name="Totally New Name", author="other designer",
                      source_url="https://www.crealitycloud.com/model-detail/new-slug",
                      file_path=str(f))
    assert hit["found"] and hit["matched_on"] == "file"


def test_a_genuinely_new_model_is_allowed(tmp_path):
    ledger.record(name="Toothbrush stand", author="IceMarine",
                  source_url="https://www.crealitycloud.com/model-detail/toothbrush-stand")
    assert not ledger.find(
        name="Kitchen Utensil Organizer", author="SomeoneElse",
        source_url="https://www.crealitycloud.com/model-detail/kitchen-utensil-organizer")["found"]


def test_two_designers_with_the_same_model_name_are_different_models():
    ledger.record(name="Soap dish", author="Alice",
                  source_url="https://www.crealitycloud.com/model-detail/soap-dish-alice")
    assert not ledger.find(
        name="Soap dish", author="Bob",
        source_url="https://www.crealitycloud.com/model-detail/soap-dish-bob")["found"]


def test_junk_short_slugs_are_not_used_as_identity():
    """'100' and 'gif' are real catalogue cards the user's run saw; a slug that
    short must not block an unrelated model."""
    ledger.record(name="100", source_url="https://www.crealitycloud.com/model-detail/100")
    assert not ledger.find(
        source_url="https://www.crealitycloud.com/model-detail/99999999")["found"]


def test_re_recording_merges_instead_of_duplicating(tmp_path):
    f = tmp_path / "m.3mf"
    f.write_bytes(b"PK\x03\x04 geometry")
    ledger.record(name="Lamp", source_url="https://x/model-detail/lamp-holder")
    ledger.record(name="Lamp", source_url="https://x/model-detail/lamp-holder",
                  stl_path=str(f), published_url="https://x/model-detail/lamp-holder-2")
    rows = ledger.entries()
    assert len(rows) == 1
    fp = rows[0]["fingerprints"]
    assert fp["file"] and rows[0]["published_url"].endswith("lamp-holder-2")


def test_the_ledger_survives_a_restart(tmp_path):
    ledger.record(name="Clothespin", source_url="https://x/model-detail/clothespin")
    path = Path(str(tmp_path / "published.json"))
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["entries"][0]["name"] == "Clothespin"
    # a fresh process = a fresh read of the same file
    assert ledger.count() == 1
    assert ledger.find(source_url="https://x/model-detail/clothespin")["found"]


def test_a_corrupt_ledger_is_kept_not_silently_wiped(monkeypatch, tmp_path):
    p = Path(str(tmp_path / "published.json"))
    p.write_text("{not json at all", encoding="utf-8")
    assert ledger.count() == 0                     # does not crash
    assert (tmp_path / "published.corrupt.json").is_file()
    # and it can still be written to again
    ledger.record(name="New", source_url="https://x/model-detail/new")
    assert ledger.count() == 1


def test_forget_is_the_only_way_back_in():
    ledger.record(name="Clothespin", source_url="https://x/model-detail/clothespin")
    assert ledger.find(source_url="https://x/model-detail/clothespin")["found"]
    assert ledger.forget("https://x/model-detail/clothespin") == 1
    assert not ledger.find(source_url="https://x/model-detail/clothespin")["found"]
    assert ledger.forget("https://x/model-detail/never-seen") == 0


def test_seeding_learns_the_account_and_past_runs(tmp_path):
    class Snap:
        def __init__(self, name, url):
            self.name, self.url = name, url
    snaps = [Snap("Stand for cleaning accessories",
                  "https://www.crealitycloud.com/model-detail/stand-for-cleaning-accessories"),
             Snap("Toothbrush stand",
                  "https://www.crealitycloud.com/model-detail/toothbrush-stand")]
    assert ledger.seed_from_account(snaps) == 2
    assert ledger.seed_from_account(snaps) == 0          # idempotent
    # a model already on the account can never be uploaded again, even though we
    # only know its account listing (no source link, no file)
    assert ledger.find(name="Stand for cleaning accessories")["found"]

    run = tmp_path / "artifacts" / "auto_run_20261008-163127.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps({"items": [
        {"name": "Laundry Detergent Cup Drain", "state": "published",
         "url": "https://www.crealitycloud.com/model-detail/laundry-detergent-cup-drain-tide"},
        {"name": "Clothespin", "state": "failed",
         "url": "https://www.crealitycloud.com/model-detail/clothespin"}]}), encoding="utf-8")
    assert ledger.seed_from_runs(run.parent) == 1
    assert ledger.find(
        source_url="https://www.crealitycloud.com/model-detail/laundry-detergent-cup-drain-tide"
    )["found"]
    # a FAILED model must stay retryable — only successes are remembered
    assert not ledger.find(
        source_url="https://www.crealitycloud.com/model-detail/clothespin")["found"]


def test_summary_is_what_the_dashboard_shows():
    ledger.record(name="A", source_url="https://x/model-detail/aaaaaaaa")
    ledger.record(name="B", source_url="https://x/model-detail/bbbbbbbb",
                  outcome="creality-duplicate")
    s = ledger.summary()
    assert s["count"] == 2 and s["published"] == 1 and s["blocked_by_creality"] == 1
    assert s["path"].endswith("published.json")
    assert len(s["entries"]) == 2


# ── the routes the dashboard and the run actually use ────────────────────────

def test_upload_route_refuses_a_repeat_before_any_browser(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import main

    called = {"n": 0}

    async def fake_upload(*a, **k):
        called["n"] += 1
        return {"success": True, "message": "Published", "url": "https://x"}

    monkeypatch.setattr(main, "uploader_upload", fake_upload)
    # The source-authorisation rule (creality_rules) now refuses ANY re-upload of
    # a Creality listing that is not named in the user's authorisation file —
    # which would make this body a 403 for the wrong reason. Point that file at a
    # tmp one and authorise the source, so the refusal under test is the LEDGER's.
    import creality_rules
    monkeypatch.setattr(creality_rules, "AUTHORIZED_FILE", tmp_path / "auth.json")
    creality_rules.add_authorized(
        "https://www.crealitycloud.com/model-detail/toothbrush-stand",
        "test: the ledger check is what must refuse here")
    client = TestClient(main.app)
    body = {"name": "Toothbrush stand", "description": "d", "stl_path": REAL_MODEL,
            "image_paths": [], "source_url":
            "https://www.crealitycloud.com/model-detail/toothbrush-stand",
            "license_raw": "https://creativecommons.org/licenses/by/4.0"}
    first = client.post("/upload", json=body)
    assert first.status_code == 200 and called["n"] == 1
    # the same model again — refused with the reason, and no uploader call
    second = client.post("/upload", json=body)
    assert second.status_code == 409
    assert "Refusing to publish twice" in second.json()["detail"]
    assert called["n"] == 1


def test_run_skips_a_model_it_already_published(monkeypatch, tmp_path):
    import main

    # The household gate (selector.py) only lets useful household names through,
    # and this test is about the LEDGER dedupe — so both fixture models belong to
    # a family the gate publishes (a wall hook, a cable organizer).
    ledger.record(name="Wall hook organizer", author="IceMarine",
                  source_url="https://x/model-detail/wall-hook-organizer")
    models = [{"name": "Wall hook organizer", "url":
               "https://x/model-detail/wall-hook-organizer", "keyword": "k"},
              {"name": "Cable organizer", "url":
               "https://x/model-detail/cable-organizer", "keyword": "k"}]
    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models[:a[1]], "licence":
                                        {"opened": True, "verified": True}, "note": ""})
    cc = "https://creativecommons.org/licenses/by/4.0"
    # the cover photo the publisher shows must EXIST on disk (the run checks it
    # before handing a model over), so the fake gets a real file
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")

    def fake_dl(url):
        from downloader import classify_license
        return {"name": url.rsplit("/", 1)[-1].replace("-", " ").title(),
                "description": "d", "stl_path": REAL_MODEL,
                "image_paths": [str(cover)], "slug": url.rsplit("/", 1)[-1],
                "author": "IceMarine", "source_url": url,
                "license": classify_license(cc)}

    async def fake_upload(*a, **k):
        return {"success": True, "message": "Published", "url": "https://x/new"}

    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", fake_upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_LOCK", asyncio.Lock())

    asyncio.run(main._auto_worker(2, ["k"], "dedupejob"))
    states = {it["url"].rsplit("/", 1)[-1]: it for it in main.AUTO["items"]}
    assert states["wall-hook-organizer"]["state"] == "duplicate"
    assert "Already published" in states["wall-hook-organizer"]["reason"]
    assert states["cable-organizer"]["state"] == "published"
    counts = main._auto_counts()
    assert counts["duplicate"] == 1 and counts["published"] == 1
    # and the newly published one is now locked too
    assert ledger.find(name="Cable Organizer")["found"]


def test_published_endpoints_list_and_forget(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main.analytics, "refresh",
                        lambda: _async({"ok": False, "error": "no browser"}))
    # this test must not pull the REAL account's model list into a tmp ledger
    monkeypatch.setattr(ledger, "seed_from_account", lambda *a, **k: 0)
    monkeypatch.setattr(ledger, "seed_from_runs", lambda *a, **k: 0)
    ledger.record(name="Clothespin", source_url="https://x/model-detail/clothespin")
    client = TestClient(main.app)
    listed = client.get("/published").json()
    assert listed["count"] == 1 and listed["entries"][0]["name"] == "Clothespin"
    synced = client.post("/published/sync").json()
    assert synced["ok"] is True and synced["note"]        # honest about the browser
    out = client.post("/published/forget",
                      json={"match": "https://x/model-detail/clothespin"}).json()
    assert out["dropped"] == 1 and out["count"] == 0


def _async(value):
    async def _run():
        return value
    return _run()


def test_a_dry_run_or_a_no_submit_fill_is_never_recorded_as_published(monkeypatch, tmp_path):
    """Found on the live campaign: a `dry_run` POST /upload reports success
    (files acquired, form untouched) and a CREALITY_NO_SUBMIT fill reports
    success too — recording either one locks a model that was never published."""
    from fastapi.testclient import TestClient
    import main

    async def fake_dry(*a, **k):
        return {"success": True, "dry_run": True, "message": "Dry run: nothing was uploaded."}

    async def fake_nosubmit(*a, **k):
        return {"success": True, "no_submit": True, "message": "Filled but did NOT submit."}

    async def fake_real(*a, **k):
        return {"success": True, "message": "Published", "url": "https://x/real"}

    client = TestClient(main.app)
    body = lambda name: {"name": name, "description": "d", "stl_path": REAL_MODEL,
                         "image_paths": [],
                         "source_url": "", "license_raw":
                         "https://creativecommons.org/licenses/by/4.0"}
    monkeypatch.setattr(main, "uploader_upload", fake_dry)
    assert client.post("/upload", json=body("Dry Run Model")).status_code == 200
    assert not ledger.find(name="Dry Run Model")["found"]

    monkeypatch.setattr(main, "uploader_upload", fake_nosubmit)
    assert client.post("/upload", json=body("Filled Only Model")).status_code == 200
    assert not ledger.find(name="Filled Only Model")["found"]

    monkeypatch.setattr(main, "uploader_upload", fake_real)
    assert client.post("/upload", json=body("Really Published Model")).status_code == 200
    assert ledger.find(name="Really Published Model")["found"]


# ── one model, several links (the duplicate the restart created) ─────────────
# The real rows, verbatim from artifacts/published_models.json on 2026-10-08:
#   row A: the hand publish  — source …/scale-model-pallet-jack,
#          published …/6ac7e100f5ed98d3279fc843 (the opaque link Creality
#          answered with), name «Домкрат для подъема поддонов 1/10-го масштаба»
#   row B: the restart's account seed — …/scale-model-pallet-jack-1-10 under the
#          listing's own English title "1/10 Scale Pallet Jack"
# Creality 301s row A's published link to row B's URL (checked live), and it was
# that redirect the ledger did not follow — 27 rows for 26 models.

SOURCE = "https://www.crealitycloud.com/model-detail/scale-model-pallet-jack"
ID_URL = "https://www.crealitycloud.com/model-detail/6ac7e100f5ed98d3279fc843"
SLUG_URL = "https://www.crealitycloud.com/model-detail/scale-model-pallet-jack-1-10"
RU_NAME = "Домкрат для подъема поддонов 1/10-го масштаба"
EN_NAME = "1/10 Scale Pallet Jack"


def _id_resolves_to_slug(u):
    """Creality's own answer for that opaque link, measured live (HTTP 301)."""
    return SLUG_URL if "6ac7e100" in (u or "") else u


def _pallet_jack_published(tmp_path=None):
    stl = None
    if tmp_path is not None:
        stl = tmp_path / "pallet.3mf"
        stl.write_bytes(b"PK\x03\x04 pallet jack geometry")
    ledger.record(name=RU_NAME, source_url=SOURCE, published_url=ID_URL,
                  stl_path=str(stl) if stl else "")
    return stl


def test_the_published_link_counts_as_identity_too():
    """A publish must be recognised by the link Creality answered with — that
    side of the fingerprint was never read before, which is the root cause."""
    _pallet_jack_published()
    assert ledger.find(source_url=ID_URL)["found"]
    assert ledger.find(source_url=SOURCE + "/")["found"]
    assert ledger.find(source_url=SOURCE)["matched_on"] == "url"
    assert not ledger.find(
        source_url="https://www.crealitycloud.com/model-detail/scale-model-forklift"
    )["found"]


def test_a_lookalike_slug_is_a_different_model():
    """…/organizer-box and …/xl-organizer-box are two models on Creality. A
    prefix rule would fold them together and one could never be published."""
    ledger.record(name="Organizer Box", source_url="https://x/model-detail/organizer-box")
    assert not ledger.find(source_url="https://x/model-detail/xl-organizer-box")["found"]
    assert not ledger.find(source_url=SLUG_URL)["found"]      # …-1-10 is not …
    assert ledger.count() == 1


def test_the_readable_published_slug_is_learned_from_crealitys_own_redirect():
    _pallet_jack_published()
    assert not ledger.find(source_url=SLUG_URL)["found"]      # nothing links it yet
    assert ledger.absorb_published_redirect(SLUG_URL, resolver=_id_resolves_to_slug)
    assert ledger.count() == 1                                # learned, not added
    assert ledger.find(source_url=SLUG_URL)["found"]
    entry = ledger.entries()[0]
    assert entry["published_url"] == SLUG_URL                 # the readable slug wins
    assert ID_URL in entry["aliases"] and SOURCE in entry["aliases"]


def test_seeding_twice_never_adds_a_second_row_for_one_model():
    _pallet_jack_published()
    snaps = [{"name": EN_NAME, "url": SLUG_URL}]
    assert ledger.seed_from_account(snaps, resolver=_id_resolves_to_slug) == 0
    assert ledger.seed_from_account(snaps, resolver=_id_resolves_to_slug) == 0
    assert ledger.seed_from_account(snaps) == 0               # offline too: alias known
    assert ledger.count() == 1


def test_merge_duplicates_folds_the_pair_and_keeps_every_identity(tmp_path):
    stl = _pallet_jack_published(tmp_path)
    digest = ledger.file_digest(str(stl))
    # the restart's own row, then re-dated to the moment it really was written
    ledger.record(name=EN_NAME, source_url=SLUG_URL, published_url=SLUG_URL,
                  note="already on your Creality Cloud account")
    assert ledger.count() == 2
    data = ledger.load()
    rows = data["entries"]
    rows[0]["published_at"] = rows[0]["recorded_at"] = "2026-10-08 19:29:20"
    rows[1]["published_at"] = rows[1]["recorded_at"] = "2026-10-08 19:49:49"
    ledger._save(data)
    assert ledger.absorb_published_redirect(SLUG_URL, resolver=_id_resolves_to_slug)

    plan = ledger.merge_duplicates(dry_run=True)
    assert len(plan["merged"]) == 1 and ledger.count() == 2    # dry run writes nothing
    assert plan["merged"][0]["kept"] == "scale-model-pallet-jack"
    assert plan["merged"][0]["folded"] == "scale-model-pallet-jack-1-10"

    done = ledger.merge_duplicates()
    assert len(done["merged"]) == 1 and ledger.count() == 1
    entry = ledger.entries()[0]
    assert entry["published_at"] == "2026-10-08 19:29:20"      # the EARLIEST date
    assert entry["fingerprints"]["file"] == digest            # geometry kept
    assert entry["name"] == RU_NAME and entry["also_named"] == [EN_NAME]
    # every link and BOTH titles still block the model
    assert ledger.find(source_url=SOURCE)["found"]
    assert ledger.find(source_url=ID_URL)["found"]
    assert ledger.find(source_url=SLUG_URL)["found"]
    assert ledger.find(name=EN_NAME, source_url="https://x/model-detail/something-else")["found"]
    assert ledger.find(name=RU_NAME, source_url="https://x/model-detail/other")["found"]
    assert not ledger.find(name="1/10 Scale Forklift")["found"]
    assert ledger.merge_duplicates()["merged"] == []          # idempotent


def test_a_restart_folds_a_pair_that_slipped_in(monkeypatch):
    monkeypatch.setattr(ledger, "seed_from_account", lambda *a, **k: 0)
    monkeypatch.setattr(ledger, "seed_from_runs", lambda *a, **k: 0)
    _pallet_jack_published()
    ledger.record(name=EN_NAME, source_url=SLUG_URL, published_url=SLUG_URL)
    assert ledger.count() == 2
    ledger.absorb_published_redirect(SLUG_URL, resolver=_id_resolves_to_slug)
    out = ledger.startup_seed()
    assert out["merged"] and ledger.count() == 1 and out["count"] == 1
