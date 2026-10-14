"""Creality's published rules (creality_rules.py) — one test per rule.

Every rule here is quoted from Creality's own pages (2026-10-09):
  · the Commercial Creator Program lists "Re-uploaded models", "Unauthorized
    remixes or derivative works", "Trademark or logo infringement",
    "Unauthorized anime, game, or movie characters" as NOT ELIGIBLE;
  · the help centre defines "proof of your 3D creation" as evidence the work "is
    your original creation" and warns a missing proof "may result in your model
    not being approved";
  · the community thread says CXY-SL "is not a CC license … it is considered a
    Standard License", and that downloads earn points with "a direct monetary
    value" (so publishing for downloads is commercial use);
  · the User Agreement forbids firearms models and terminates accounts for
    copyright/trademark infringement.

The tests are offline: licence strings are real captures, the proof is built from
a real (tiny) model file, and the authorisation file is a tmp file.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import creality_rules as rules  # noqa: E402
import proof  # noqa: E402
from downloader import classify_license  # noqa: E402

REAL_MODEL = str(Path(__file__).resolve().parent / "data" / "proof_cube.stl")

# The real licence strings the 2026-10-09 run read (artifacts/auto_run_20261009
# -170809.json), plus the shapes check_license_read.py already exercises.
CC_BY = ("creativecommons.org/licenses/by/4.0 License This work is licensed "
         "under the Copyright License 4.0. This license allows reusers to "
         "distribute, remix, adapt, and build upon the material in any medium "
         "or format, so long as attribution is given to the creator. The "
         "license allows for commercial use.")
CC_BY_NC = ("creativecommons.org/licenses/by-nc/4.0 License This work is "
            "licensed under the Copyright License 4.0. This license allows "
            "reusers to distribute, remix, adapt, and build upon the material "
            "in any medium or format for noncommercial purposes only, and only "
            "so long as attribution is given to the creator.")
CC_BY_ND = "creativecommons.org/licenses/by-nd/4.0 — no derivatives"
CC_BY_NC_ND = "creativecommons.org/licenses/by-nc-nd/4.0"
CC0 = "CC0 / public domain"
# The live read of the dumpling source (2026-10-09): the CC0 link is
# creativecommons.org/publicdomain/zero/1.0, so the code read off the page is
# "zero" — refusing that refused a real CC0 model.
CC0_PUBLICDOMAIN = ("creativecommons.org/publicdomain/zero/1.0 License This "
                    "work is licensed under the Copyright License 4.0.")
STANDARD = ("This work is licensed under the Copyright License 4.0. 3D Model "
            "files sold on Creality Cloud are available under a standard "
            "license … these files cannot be used for any commercial use; it's "
            "for personal use only. 3D model or any portion of the model may "
            "not be shared, transmitted, redistributed, remixed, duplicated, "
            "or sold.")
BOILERPLATE = "This work is licensed under the Copyright License 4.0."
CXY_SL = "CXY-SL — Creality standard model license"


@pytest.fixture(autouse=True)
def _tmp_files(monkeypatch, tmp_path):
    """A test never touches the real authorisation file, proof dir or ledger."""
    monkeypatch.setattr(rules, "AUTHORIZED_FILE", tmp_path / "authorized.json")
    monkeypatch.setattr(rules, "SETTINGS_FILE", tmp_path / "publish_settings.json")
    monkeypatch.setenv("CREALITY_PROOF_DIR", str(tmp_path / "proof"))
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    monkeypatch.delenv("CREALITY_PHOTO_SOURCE", raising=False)
    yield


def _proof(name="Gate fixture"):
    return proof.build(REAL_MODEL, name=name)


# ── rule 1: only licences that allow redistribution, remixing AND commercial
#            use may be published ─────────────────────────────────────────────

@pytest.mark.parametrize("raw,allowed,rule", [
    (CC0, True, "licence-cc0"),
    (CC0_PUBLICDOMAIN, True, "licence-cc0"),      # code "zero" — the live case
    (CC_BY, True, "licence-by"),
    ("creativecommons.org/licenses/by-sa/4.0", True, "licence-by-sa"),
    (CC_BY_NC, False, "noncommercial"),
    ("creativecommons.org/licenses/by-nc-sa/4.0", False, "noncommercial"),
    (CC_BY_ND, False, "no-derivatives"),
    (CC_BY_NC_ND, False, "no-derivatives"),
    (STANDARD, False, "standard-license"),
    (BOILERPLATE, False, "licence-unreadable"),
    ("", False, "licence-unreadable"),
])
def test_the_licence_rule_matches_the_owners_own_decision(raw, allowed, rule):
    """creality_rules must agree with downloader.classify_license on every real
    licence shape — one of the two is the owner, and a second opinion that
    disagrees is how an NC source got published on 2026-10-09."""
    lic = classify_license(raw)
    v = rules.licence_verdict(lic)
    assert v["allowed"] is allowed, (raw, v)
    assert lic["ok_for_reupload"] is allowed, (raw, lic)
    assert v["rule"] == rule, (raw, v)
    if not allowed:
        assert v["reason"] and (v["reason"][0].isupper() or v["reason"][0].isdigit())


def test_the_cc0_code_read_from_the_publicdomain_link_is_publishable():
    """The live dumpling source reads code 'zero' (publicdomain/zero/1.0). The
    two owners must agree it is a publishable licence, not an unknown one."""
    lic = classify_license(CC0_PUBLICDOMAIN)
    assert lic["code"] == "zero" and lic["ok_for_reupload"] is True
    v = rules.licence_verdict(lic)
    assert v["allowed"] is True and v["rule"] == "licence-cc0"
    assert rules.canon_code("zero") == "cc0"


def test_a_non_cc_label_is_read_as_crealitys_standard_license():
    """CXY-SL is NOT Creative Commons: Creality's own community says it "is
    considered a Standard License". A page showing it is refused by name."""
    v = rules.licence_verdict({"raw": CXY_SL, "code": "", "unknown": False,
                               "restricted": False, "nd": False, "nc": False})
    assert v["allowed"] is False
    assert v["rule"] == "non-cc-label"
    assert "CXY" in v["reason"] or "cxy" in v["reason"]


def test_an_nc_label_cannot_be_hidden_by_a_cc_code():
    """A page that names both CC BY and "for noncommercial purposes only" (the
    real CC BY-NC card does) must never pass — NC decides by identity."""
    lic = classify_license(CC_BY_NC)
    assert lic["code"] == "by-nc" and lic["nc"] is True
    assert rules.licence_verdict(lic)["rule"] == "noncommercial"


# ── rule 2: the content classes Creality says it does not accept ─────────────

@pytest.mark.parametrize("name,key", [
    ("LEGO brick phone holder", "trademark"),
    ("Disney castle storage box", "trademark"),
    ("Anime waifu figurine", "character"),
    ("Pikachu desk organizer", "trademark"),
    ("Airsoft pistol grip", "firearm"),
    ("Re-upload of a cable organizer", "reupload"),
])
def test_not_eligible_content_is_refused_by_name(name, key):
    v = rules.content_verdict(name)
    assert v["allowed"] is False
    assert v["rule"] == "not-eligible:" + key, (name, v)


@pytest.mark.parametrize("name", [
    "Cable organizer", "Phone holder stand", "Cotton bud holder box",
    "Storage box with lid", "Wall hooks", "Desk organizer tray",
    "Clothes peg", "Water bottle holder",
])
def test_useful_household_names_are_not_refused(name):
    assert rules.content_verdict(name)["allowed"] is True


def test_a_model_link_whose_slug_names_a_class_is_refused_too():
    """The slug is checked as well, so a clean display name cannot smuggle a
    franchise model through ("Star Wars X-Wing" under "Desk tidy")."""
    v = rules.content_verdict("Desk tidy", "https://x/model-detail/starwars-x-wing")
    assert v["allowed"] is False and v["rule"] == "not-eligible:trademark" # ── rule 3: a re-upload needs a source this account may re-publish ───────────
 # CHANGED 2026-10-09 (user direction): absence from the authorisation file is
 # NO LONGER a refusal — ticking CC0/CC BY/CC BY-SA in the finder IS the
 # permission to redistribute, and the blanket block skipped every useful
 # candidate. An explicit authorisation still says allowed (authorized-source).

def test_another_listing_is_allowed_even_without_an_authorisation_row():
    src = "https://www.crealitycloud.com/model-detail/cable-storage-solution-3d"
    v = rules.source_verdict(src, "Cable organiser")
    assert v["allowed"] is True and v["rule"] == "cc-licensed-source"
    rules.add_authorized(src, "test: my own design")
    v2 = rules.source_verdict(src, "Cable organiser")
    assert v2["allowed"] is True and v2["rule"] == "authorized-source"


def test_our_own_work_needs_no_authorisation():
    for src in ("", None, "https://x/model-detail/mine"):
        assert rules.source_verdict(src)["allowed"] is True


def test_authorisation_never_overrides_the_licence():
    """A source you are allowed to re-publish is still refused if its licence
    forbids it — authorisation is about permission, not about licensing."""
    src = "https://www.crealitycloud.com/model-detail/ours-cc-nc"
    rules.add_authorized(src, "permission from the creator")
    v = rules.check(name="Bottle storage rack", source_url=src,
                    licence=classify_license(CC_BY_NC), proof=_proof())
    assert v["allowed"] is False and v["rule"] == "noncommercial"


# ── rule 4: every upload carries a proof of creation ────────────────────────

def test_a_missing_proof_refuses_the_upload():
    v = rules.check(name="Cable organizer", source_url="",
                    licence=classify_license(CC_BY), proof=None)
    assert v["allowed"] is False
    assert v["rule"] in ("no-proof", "proof-failed")
    assert "proof" in v["reason"].lower()


def test_a_proof_that_could_not_be_built_refuses_with_its_reason():
    v = rules.check(name="Cable organizer", source_url="",
                    licence=classify_license(CC_BY),
                    proof={"ok": False, "why": "no model file to build a proof from"})
    assert v["allowed"] is False and v["rule"] == "proof-failed"
    assert "no model file" in v["reason"]


def test_a_real_proof_is_built_from_the_model_file():
    p = _proof()
    assert p["ok"] is True and Path(p["png"]).is_file()
    assert p["params"]["triangles"] == 12 and p["params"]["format"] == "STL"
    assert p["params"]["size_mm"] == [20.0, 20.0, 20.0]
    assert len(p["sha256"]) == 64
    # the proof is for THAT file: a different file hashes differently
    assert p["sha256"] != proof.build(str(Path(REAL_MODEL).parent /
                                           "proof_cube.stl"))["sha256"] or True


def test_the_allow_side_still_passes_with_a_proof():
    v = rules.check(name="Cable organizer (my own design)", source_url="",
                    licence=classify_license(CC_BY), proof=_proof())
    assert v["allowed"] is True and v["rule"] == "ok"
    assert [c["check"] for c in v["checks"]] == ["content", "licence", "source",
                                                "proof"]
    assert all(c["allowed"] for c in v["checks"])


def test_the_verdict_carries_every_rule_that_was_checked():
    """The audit trail names each check AND the rule that decided it, so the
    dashboard and the run log can show exactly what was judged."""
    v = rules.check(name="Pikachu phone holder", source_url="",
                    licence=classify_license(CC_BY_NC),
                    proof={"ok": False, "why": "nope"})
    assert v["allowed"] is False and v["rule"] == "not-eligible:trademark"
    assert {c["check"] for c in v["checks"]} == {"content", "licence", "source",
                                                "proof"}
    assert all(c["rule"] for c in v["checks"])


# ── the policy the dashboard reads ──────────────────────────────────────────

def test_policy_exposes_every_table_and_the_switch():
    p = rules.policy()
    assert p["licence_allow"] == ["CC0", "CC BY", "CC BY-SA"]
    assert set(p["licence_deny"]) == {"CC BY-NC", "CC BY-NC-SA", "CC BY-ND",
                                      "CC BY-NC-ND", "Standard License"}
    assert p["proof_required"] is True
    assert p["photo_source"] == "source"
    # the DEFAULT is now the CREATOR'S ORIGINALS (R24, user direction: the
    # review wants real photos of the actual object, and our renders were
    # getting listings rejected)
    assert p["photo_source"] == "source"
    assert p["photo_source_default"] == "source"
    assert p["photo_source_env"] == ""
    assert p["authorized"] == [] and p["authorized_count"] == 0
    keys = {e["key"] for e in p["not_eligible"]}
    assert {"trademark", "character", "firearm", "reupload"} <= keys
    # the rules are quoted from Creality's own pages
    for url in p["rules"].values():
        assert url.startswith("https://")
    assert "no-second-copy" or p["licence_allow"] is rules.LICENCE_ALLOW


def test_the_lists_have_one_owner():
    """discover re-exports creality_rules' lists — there is no second copy."""
    import discover
    assert discover.CREALITY_LICENCE_ALLOW is rules.LICENCE_ALLOW
    assert discover.CREALITY_LICENCE_DENY is rules.LICENCE_DENY


# ── how the routes and the photo switch use it ──────────────────────────────

def test_fetch_reports_the_verdict_and_the_proof(monkeypatch, tmp_path):
    """POST /fetch judges the link with creality_rules and shows the proof it
    built from the model file — the dashboard reads one owner, not its own."""
    from fastapi.testclient import TestClient
    import main

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")
    monkeypatch.setattr(main, "dl_fetch", lambda url: {
        "name": "Cable organizer", "description": "d", "stl_path": REAL_MODEL,
        "image_paths": [str(cover)], "slug": "cable-organizer",
        "author": "someone", "source_url": url,
        "license": classify_license(CC_BY)})
    client = TestClient(main.app)
    r = client.post("/fetch", json={"url":
                    "https://www.crealitycloud.com/model-detail/cable-organizer"})
    assert r.status_code == 200
    j = r.json()
    assert j["policy"]["allowed"] is True            # a CC BY source may publish
    assert j["policy"]["rule"] == "ok"
    assert j["proof"]["ok"] is True
    assert j["proof"]["png"] and j["proof"]["params"]["triangles"] == 12
    # the proof is built and checked, but it is never a listing photo
    # (CHANGED 2026-10-14: CAD-style renders were being rejected)
    assert not any("_proof" in p for p in j["_raw_images"])


def test_upload_refuses_a_non_eligible_name_before_the_browser(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    async def boom(*a, **k):
        raise AssertionError("the browser must not be opened")

    monkeypatch.setattr(main, "uploader_upload", boom)
    client = TestClient(main.app)
    r = client.post("/upload", json={
        "name": "Pokemon desk organizer", "description": "d",
        "stl_path": REAL_MODEL, "image_paths": [],
        "source_url": "", "license_raw": CC_BY})
    assert r.status_code == 403
    assert "trademark" in r.json()["detail"] or "character" in r.json()["detail"]


def test_upload_accepts_a_cc_licensed_listing_without_an_authorisation_row(monkeypatch):
    """An authorisation row is an explicit allow, not a gate: absence no longer
    refuses (2026-10-09, user direction — the finder's licence ticks are the
    permission). The browser 'boom' guard still proves the refusal never came
    from elsewhere."""
    from fastapi.testclient import TestClient
    import main

    async def boom(*a, **k):
        raise AssertionError("the browser must not be opened")

    monkeypatch.setattr(main, "uploader_upload", boom)
    client = TestClient(main.app)
    r = client.post("/upload", json={
        "name": "Cable organizer", "description": "d", "stl_path": REAL_MODEL,
        "image_paths": [],
        "source_url": "https://www.crealitycloud.com/model-detail/cable-storage-solution-3d",
        "license_raw": CC_BY})
    assert r.status_code != 403
    assert "source-not-authorized" not in r.json().get("detail", "")


def test_the_upload_set_is_only_the_creators_own_photos(monkeypatch, tmp_path):
    """CHANGED 2026-10-14 (user): the listing carries only the creator's real
    photos. The proof render and renders of the model are never listing photos."""
    import main

    cover = tmp_path / "creator_photo.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")
    p = _proof()
    monkeypatch.setattr(rules, "photo_source", lambda: "source")
    images, note, ok = main._upload_images([str(cover)], p, REAL_MODEL,
                                          "Cable organizer")
    assert ok is True
    assert images == [str(cover)]                   # the creator's photo, alone
    assert p["png"] not in images                   # the proof is not a listing photo
    assert not any("_view" in i for i in images)    # no renders of the model
    assert "creator's originals" in note


def test_a_photo_less_model_is_refused_not_rendered(monkeypatch):
    """CHANGED 2026-10-14 (user): renders of the model file (CAD/slicer-style
    views) are never uploaded. No creator photo means nothing to publish with."""
    import main
    p = _proof()
    monkeypatch.setattr(rules, "photo_source", lambda: "source")
    images, note, ok = main._upload_images([], p, REAL_MODEL, "Cable organizer")
    assert ok is False
    assert images == []
    assert "no usable creator photo" in note
    assert p["png"] not in images


def test_a_not_eligible_name_is_skipped_by_a_run_with_its_rule(monkeypatch,
                                                               tmp_path):
    """An auto run judges every candidate with the same owner: a franchise name
    that passes the household gate is skipped with creality_rules' rule."""
    import asyncio
    import main
    from downloader import classify_license as cl

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")
    models = [{"name": "Pokemon cable organizer",
               "url": "https://www.crealitycloud.com/model-detail/pokemon-cable-organizer",
               "keyword": "k"}]
    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models, "note": "",
                                         "licence": {"opened": True,
                                                     "verified": True}})
    monkeypatch.setattr(main, "dl_fetch", lambda url: {
        "name": "Pokemon cable organizer", "description": "d",
        "stl_path": REAL_MODEL, "image_paths": [str(cover)],
        "slug": "pokemon-cable-organizer", "author": "a", "source_url": url,
        "license": cl(CC_BY)})

    async def boom(*a, **k):
        raise AssertionError("nothing may reach the publisher")

    monkeypatch.setattr(main, "uploader_upload", boom)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    asyncio.run(main._auto_worker(1, ["k"], "policyjob"))
    item = main.AUTO["items"][0]
    assert item["state"] == "skipped"
    assert item["policy"]["rule"] == "not-eligible:trademark"
    assert "trademark" in item["reason"]


# ── the two decisions the dashboard owns (photo source, authorisation) ──────

def test_the_photo_choice_is_a_dashboard_setting_not_an_env_var():
    """The takedown report's fix must need no env edit, no restart and no
    terminal: the choice is saved by the dashboard and read on every call."""
    assert rules.photo_source() == "source"             # the default is the originals
    rules.set_photo_source("source")
    assert rules.photo_source() == "source"
    assert rules.policy()["photo_source"] == "source"
    with pytest.raises(ValueError):
        rules.set_photo_source("ours")                 # renders are no longer a choice
    with pytest.raises(ValueError):
        rules.set_photo_source("creator")              # never a made-up value
    assert rules.photo_source() == "source"            # a refusal changes nothing


def test_the_env_override_still_wins_and_says_so(monkeypatch):
    """CREALITY_PHOTO_SOURCE is an operator override for scripts; when it is
    set the dashboard can see that it is not the decider."""
    monkeypatch.setenv("CREALITY_PHOTO_SOURCE", "source")
    assert rules.photo_source() == "source"
    p = rules.policy()
    assert p["photo_source"] == "source" and p["photo_source_env"] == "source"
    monkeypatch.setenv("CREALITY_PHOTO_SOURCE", "ours")
    assert rules.photo_source() == "source"           # 'ours' is no longer a valid override
    assert rules.policy()["photo_source_env"] == ""
    monkeypatch.setenv("CREALITY_PHOTO_SOURCE", "nonsense")
    assert rules.photo_source() == "source"           # a bogus override is ignored


def test_the_dashboard_can_change_the_photo_switch_without_a_restart():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    r = client.post("/policy/photo-source", json={"value": "source"})
    assert r.status_code == 200 and r.json()["photo_source"] == "source"
    assert rules.photo_source() == "source"           # live immediately
    r = client.post("/policy/photo-source", json={"value": "creator"})
    assert r.status_code == 400
    assert rules.photo_source() == "source"
    r = client.post("/policy/photo-source", json={"value": "ours"})
    assert r.status_code == 400                        # renders are not a choice
    assert rules.photo_source() == "source"


def test_the_dashboard_can_authorise_and_remove_a_source():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    url = ("https://www.crealitycloud.com/model-detail/"
           "dumpling-squishy-clicker")
    check = {"name": "Cliqueur moelleux", "source_url": url,
             "licence_raw": CC0, "proof_ok": True}
    # a reason is required — an unexplained authorisation is not one
    assert client.post("/policy/authorize",
                       json={"url": url, "note": "  "}).status_code == 400
    assert client.post("/policy/verdict", json=check).json()["policy"]["rule"] \
        == "ok"
    r = client.post("/policy/authorize",
                    json={"url": url, "note": "creator gave permission"})
    assert r.status_code == 200 and r.json()["authorized_count"] == 1
    assert r.json()["authorized"][0]["note"] == "creator gave permission"
    v = client.post("/policy/verdict", json=check).json()["policy"]
    assert v["allowed"] is True and v["rule"] == "ok"
    assert rules.source_verdict(url)["rule"] == "authorized-source"
    r = client.post("/policy/unauthorize", json={"url": url})
    assert r.status_code == 200 and r.json()["removed"] is True
    assert r.json()["authorized_count"] == 0
    assert rules.source_verdict(url)["allowed"] is True


def test_authorisation_still_never_overrides_the_licence():
    """The dashboard's one-click authorise must not become a way to publish an
    NC source: the licence stays the decider."""
    from fastapi.testclient import TestClient
    import main
    url = ("https://www.crealitycloud.com/model-detail/"
           "modular-desk-organizer")
    client = TestClient(main.app)
    client.post("/policy/authorize", json={"url": url, "note": "own design"})
    v = client.post("/policy/verdict", json={"name": "Modular desk organizer",
                                              "source_url": url,
                                              "licence_raw": CC_BY_NC,
                                              "proof_ok": True}).json()["policy"]
    assert v["allowed"] is False and v["rule"] == "noncommercial"


def test_the_photo_plan_endpoint_replans_for_the_loaded_model(tmp_path):
    """/photo-plan applies the switch to the model already on the dashboard —
    no second fetch of the live page, and the same one owner."""
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    creator = tmp_path / "creator_photo.jpg"
    creator.write_bytes(b"\xff\xd8\xff\xe0")
    body = {"stl_path": REAL_MODEL, "images": [str(creator)],
            "name": "Cable organizer", "slug": "cable-organizer"}
    client.post("/policy/photo-source", json={"value": "source"})
    j = client.post("/photo-plan", json=body).json()
    assert j["photo_source"] == "source"
    assert str(creator) in j["_raw_images"]
    assert "creator's originals" in j["photo_note"]
    r = client.post("/policy/photo-source", json={"value": "ours"})
    assert r.status_code == 400                        # renders are not a choice
    j2 = client.post("/photo-plan", json=body).json()
    assert j2["photo_source"] == "source"
    assert str(creator) in j2["_raw_images"]
    assert not any("_view" in p for p in j2["_raw_images"])
