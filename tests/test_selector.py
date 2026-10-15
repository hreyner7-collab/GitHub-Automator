"""The household gate (selector.py): useful household items only.

The user's words: "household items only ... a toilet ... a garage ... that's not
the household item that is useful ... filter out properly, no rubbish item". So
these tests hold the gate to the user's own list — cable organizers, phone
holders, cotton-bud holders, bottle/storage boxes, clothes clips, hooks, desk
organizers go out; toilets, bathroom sets, garage gear, novelties and trinkets
never do — and prove a rejected model cannot reach the uploader even if
discovery were bypassed.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import selector  # noqa: E402

# A REAL model file: publishing now requires a proof of creation built from the
# file itself (creality_rules), so a fixture that only names a path is refused.
REAL_MODEL = str(Path(__file__).resolve().parent / "data" / "proof_cube.stl")


@pytest.fixture(autouse=True)
def _tmp_ledger(monkeypatch, tmp_path):
    """The real published_models.json is never touched by a test."""
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    monkeypatch.setenv("CREALITY_PROOF_DIR", str(tmp_path / "proof"))   # no writes into downloads/
    # the photo switch is the dashboard's saved setting: a test must read its
    # own choice, never the real artifacts/publish_settings.json
    import creality_rules
    monkeypatch.setattr(creality_rules, "SETTINGS_FILE",
                        tmp_path / "publish_settings.json")
    monkeypatch.delenv("CREALITY_PHOTO_SOURCE", raising=False)
    yield
    monkeypatch.delenv("CREALITY_LEDGER", raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test resolves a real redirect (the ledger's only socket)."""
    import ledger
    real = ledger.canonical_url
    monkeypatch.setattr(
        ledger, "canonical_url",
        lambda url, timeout=12.0, resolver=None:
        real(url, timeout=timeout, resolver=resolver or (lambda u: u)))


# ── what the run put on the account, and must never put there again ──────────

def test_the_runs_own_rubbish_is_turned_down_with_a_reason():
    for name, needle in (("Toilet", "bathroom"),
                         ("Set of accessories for the bathroom", "bathroom"),
                         ("Garage 3D Printing Projects", "garage"),
                         ("Dice tower", "novelty"),
                         ("Vase decor", "decorative"),
                         ("Filament spool holder", "printer"),
                         ("Bike phone mount", "garage")):
        v = selector.classify(name)
        assert not v["allowed"], name
        assert needle in v["reason"], (name, v["reason"])


def test_a_reject_rule_beats_a_family_match():
    """"Toilet paper holder" is a holder — and still rubbish."""
    assert not selector.classify("Toilet paper holder")["allowed"]
    assert not selector.classify("Soap dish")["allowed"]


# ── the user's own families ──────────────────────────────────────────────────

def test_the_families_the_user_named_are_kept():
    cases = {
        "Cable organizer box": "cable-organizer",
        "Cable management clips": "cable-organizer",
        "Headphone stand": "cable-organizer",
        "Phone holder stand": "phone-holder",
        "Cotton bud holder": "swab-holder",
        "Water bottle storage rack": "bottle-storage",
        "Cup holder for desk": "bottle-storage",
        "Storage box with lid": "storage-box",
        "Drawer organizer": "storage-box",
        "Clothes Peg": "clothes-clip",
        "Clothespin": "clothes-clip",
        "Bag clips": "clothes-clip",
        "Wall hooks set": "hook",
        "Key holder wall mount": "hook",
        "Desk organizer pen holder": "desk-organizer",
        "Pencil cup": "desk-organizer",
    }
    for name, family in cases.items():
        v = selector.classify(name)
        assert v["allowed"], (name, v["reason"])
        assert v["family"] == family, (name, v["family"])


def test_common_cable_and_phone_wording_is_kept():
    # names the live run turned down as "not one of the useful household families"
    cases = {
        "Ender 3 Cable Chain": "cable-organizer",
        "D Printed Usb Cable Reel": "cable-organizer",
        "Cable Protection": "cable-organizer",
        "Charging Cable Protector 01": "cable-organizer",
        "Benchtop Zip Tie Holder": "cable-organizer",
        "Organizador de cabos": "cable-organizer",
        "Clip Cavi": "cable-organizer",
        "Organizador de móviles modular": "phone-holder",
    }
    for name, family in cases.items():
        v = selector.classify(name)
        assert v["allowed"], (name, v["reason"])
        assert v["family"] == family, (name, v["family"])


def test_russian_titles_are_judged_the_same_way():
    """The account already holds Russian titles; the search returns them too."""
    kept = {"Ключница домой": "hook",
            "Органайзер для кабелей": "cable-organizer",
            "Держатель для телефона": "phone-holder",
            "Подставка для бутылок": "bottle-storage",
            "Прищепки для белья": "clothes-clip",
            "Коробка для хранения": "storage-box",
            "Органайзер настольный": "desk-organizer"}
    for name, family in kept.items():
        v = selector.classify(name)
        assert v["allowed"] and v["family"] == family, (name, v)
    for name in ("Туалет", "Гараж для машин", "Ваза", "Игрушка котик"):
        assert not selector.classify(name)["allowed"], name


def test_a_model_outside_every_family_is_turned_down_by_name():
    v = selector.classify("Card holder")
    assert not v["allowed"] and v["source"] == "no-family"
    assert "cable" in v["reason"]          # it names the families it would accept


def test_short_words_do_not_match_inside_longer_ones():
    """"car" must not fire inside "card", "tool" not inside "stool"."""
    assert selector.classify("Card holder")["source"] == "no-family"
    assert selector.classify("Bar stool")["source"] == "no-family"


# ── one of each per run ──────────────────────────────────────────────────────

def test_a_second_item_from_the_same_family_is_turned_down():
    used = set()
    assert selector.allow("Cable organizer", used_families=used)["allowed"]
    second = selector.allow("Cable clips tidy box", used_families=used)
    assert not second["allowed"]
    assert second["source"] == "duplicate-family"
    assert "already going out in this run" in second["reason"]
    # a different family is still fine in the same run
    assert selector.allow("Phone holder", used_families=used)["allowed"]


def test_the_default_searches_are_the_users_families_not_the_phrase_household_items():
    assert "household items" not in selector.DEFAULT_KEYWORDS
    assert selector.DEFAULT_KEYWORDS == [f["search"] for f in selector.FAMILIES.values()]
    assert "cable organizer" in selector.DEFAULT_KEYWORDS
    assert "water bottle holder" in selector.DEFAULT_KEYWORDS


def test_filter_candidates_splits_kept_from_rejected_with_reasons():
    out = selector.filter_candidates([
        {"name": "Toilet", "url": "https://x/model-detail/toilet"},
        {"name": "Storage box", "url": "https://x/model-detail/storage-box"},
        {"name": "Storage basket", "url": "https://x/model-detail/storage-basket"},
    ])
    assert [k["name"] for k in out["kept"]] == ["Storage box"]
    assert [r["name"] for r in out["rejected"]] == ["Toilet", "Storage basket"]
    assert out["kept"][0]["label"] == "storage box / basket / tray"
    assert "bathroom" in out["rejected"][0]["reason"]
    assert "already going out in this run" in out["rejected"][1]["reason"]


# ── the run's own guard: a rejected model can never reach the uploader ───────

def test_a_toilet_cannot_reach_the_uploader_even_if_discovery_hands_it_over(
        monkeypatch, tmp_path):
    """Discovery already drops rubbish, so this test bypasses it on purpose: the
    run itself must refuse, or a bug in one place would put a toilet on the
    account again."""
    import asyncio
    import main

    models = [{"name": "Toilet", "url": "https://x/model-detail/toilet",
               "keyword": "household items"},
              {"name": "Phone holder stand", "url": "https://x/model-detail/phone-holder",
               "keyword": "phone holder"}]
    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models, "licence":
                                         {"opened": True, "verified": True}, "note": ""})
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")

    def fake_dl(url):
        from downloader import classify_license
        return {"name": url.rsplit("/", 1)[-1].replace("-", " ").title(),
                "description": "d", "stl_path": REAL_MODEL,
                "image_paths": [str(cover)], "slug": url.rsplit("/", 1)[-1],
                "author": "Someone", "source_url": url,
                "license": classify_license("https://creativecommons.org/licenses/by/4.0")}

    uploaded = []

    async def fake_upload(name, *a, **k):
        uploaded.append(name)
        return {"success": True, "message": "Published", "url": "https://x/new"}

    monkeypatch.setattr(main, "dl_fetch", fake_dl)
    monkeypatch.setattr(main, "uploader_upload", fake_upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "UPLOAD_LOCK", asyncio.Lock())

    asyncio.run(main._auto_worker(2, ["household items"], "gatetest"))
    states = {it["name"]: it for it in main.AUTO["items"]}
    assert states["Toilet"]["state"] == "rejected"
    assert "bathroom" in states["Toilet"]["reason"]
    assert states["Phone holder stand"]["state"] == "published"
    assert len(uploaded) == 1                          # the form never saw a toilet
    assert "phone" in uploaded[0].lower()
    assert all("toilet" not in n.lower() for n in uploaded)
    import ledger
    assert not ledger.find(name="Toilet")["found"]
    counts = main._auto_counts()
    assert counts["rejected"] == 1 and counts["published"] == 1


def test_discoverys_own_refusals_are_listed_in_the_run_and_on_the_dashboard(
        monkeypatch, tmp_path):
    import asyncio
    import main

    monkeypatch.setattr(main, "find_creality", lambda *a, **k: {
        "models": [], "licence": {"opened": True, "verified": True}, "note": "",
        "rejected": [{"name": "Toilet", "url": "https://x/model-detail/toilet",
                      "reason": selector.classify("Toilet")["reason"],
                      "source": "reject:bathroom"}],
        "rejected_total": 7})
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    asyncio.run(main._auto_worker(2, ["household items"], "gatetest2"))
    assert main.AUTO["items"][0]["state"] == "rejected"
    assert "bathroom" in main.AUTO["items"][0]["reason"]
    assert main.AUTO["rejected_total"] == 7


def test_the_dashboard_can_read_what_the_gate_allows():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    pol = client.get("/selector").json()
    labels = [f["label"] for f in pol["families"]]
    assert "cable / headphone organizer" in labels
    assert "phone holder / stand" in labels
    assert any("bathroom" in r["reason"] for r in pol["rejects"])
    assert pol["default_keywords"] == selector.DEFAULT_KEYWORDS
    status = client.get("/auto/status").json()
    assert status["selector"]["default_keywords"] == selector.DEFAULT_KEYWORDS
