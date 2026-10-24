"""A listing that a run refused, or could not fetch, is not opened again by the
next Find Models.

Regression: a model that ended as "skipped" (no licence read, no model file) or
"fetch-failed" was written down nowhere, so every run offered it again from the
listing and opened its page again. The same few listings came back run after run
(12 runs in the log for one listing). These tests use a temporary store and never
open a page or touch the account.

The record has two tiers: a refusal a listing deserves keeps its week, a listing
the pipeline merely had bad luck with is forgiven after a couple of hours.
"""
import json
import time

import discover
import main

URL = "https://www.crealitycloud.com/model-detail/cable-organizer-x"
DAY = 24 * 3600
NOW = 1_000_000.0


def test_refused_listing_is_remembered_and_skipped(tmp_path):
    store = tmp_path / "tried_listings.json"
    assert not discover.was_tried_recently(URL, path=store, now=NOW)

    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=NOW)

    assert discover.was_tried_recently(URL, path=store, now=NOW + DAY)


def test_locale_form_of_the_same_listing_is_also_skipped(tmp_path):
    store = tmp_path / "tried_listings.json"
    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=NOW)

    german = URL.replace("crealitycloud.com/", "crealitycloud.com/de/")
    assert discover.was_tried_recently(german, path=store, now=NOW)


def test_cooldown_expires_and_first_decision_date_is_kept(tmp_path):
    store = tmp_path / "tried_listings.json"
    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=NOW)
    # a later run refusing the same listing must not push the cooldown back
    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=NOW + 3 * DAY)

    entry = json.loads(store.read_text(encoding="utf-8"))[discover._english_url(URL)]
    assert entry["when"] == NOW
    assert not discover.was_tried_recently(
        URL, path=store, now=NOW + discover.TRIED_COOLDOWN_SECONDS + 1)


def test_judge_card_does_not_open_a_remembered_listing(tmp_path):
    store = tmp_path / "tried_listings.json"
    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=NOW)

    verdict = discover.judge_card(
        "Cable organizer X", URL, set(),
        is_done=lambda u, n: discover.was_tried_recently(u, path=store, now=NOW))

    assert verdict["state"] == "skipped"


def test_first_use_remembers_what_earlier_runs_decided(tmp_path):
    finished = "2026-10-09 21:02:21"
    when = time.mktime(time.strptime(finished, "%Y-%m-%d %H:%M:%S"))
    refused = URL + "-refused"
    published = URL + "-published"
    (tmp_path / "auto_run_20261009-210221.json").write_text(json.dumps({
        "finished": finished,
        "items": [
            {"url": refused, "name": "Refused", "state": "skipped",
             "reason": "no licence was read"},
            {"url": published, "name": "Published", "state": "published"},
        ],
    }), encoding="utf-8")
    store = tmp_path / "tried_listings.json"

    assert discover.was_tried_recently(refused, path=store, now=when + DAY)
    assert not discover.was_tried_recently(published, path=store, now=when + DAY)


def test_listing_a_stopped_run_already_read_is_remembered(tmp_path, monkeypatch):
    """Regression: a run stopped AFTER it read a page left the item 'queued', and
    nothing was written, so the next run opened the same page again."""
    store = tmp_path / "tried_listings.json"
    monkeypatch.setattr(discover, "TRIED_PATH", store)
    monkeypatch.setitem(main.AUTO, "items", [
        {"url": URL + "-read", "name": "Read", "state": "queued",
         "reason": "run stopped after this model was read"},
        {"url": URL + "-retry", "name": "Retry", "state": "queued",
         "reason": "run stopped while retrying this model's fetch"},
        {"url": URL + "-dup", "name": "Dup", "state": "duplicate",
         "reason": "Already published"},
        {"url": URL + "-failed", "name": "Failed", "state": "failed",
         "reason": "Creality rejected the submit"},
        {"url": URL + "-inflight", "name": "Inflight", "state": "fetching"},
        {"url": URL + "-undecided", "name": "Undecided", "state": "queued",
         "reason": ""},
    ])

    main._remember_outcomes()

    saved = json.loads(store.read_text(encoding="utf-8"))
    for tail in ("-read", "-retry", "-dup", "-failed", "-inflight"):
        assert discover._english_url(URL + tail) in saved, tail
    assert discover._english_url(URL + "-undecided") not in saved


def test_seed_remembers_listings_a_stopped_run_opened(tmp_path):
    """A run that was stopped mid-page is remembered so the very next run does not
    read that page again — but as bad luck, not as a refusal, so its cooldown is
    TRIED_TRANSIENT_COOLDOWN_SECONDS rather than the week a real refusal gets."""
    finished = "2026-10-13 17:15:00"
    when = time.mktime(time.strptime(finished, "%Y-%m-%d %H:%M:%S"))
    stopped = URL + "-stopped"
    (tmp_path / "auto_run_20261013-171500.json").write_text(json.dumps({
        "finished": finished,
        "items": [
            {"url": stopped, "name": "Stopped", "state": "queued",
             "reason": "run stopped after this model was read"},
            {"url": URL + "-plain", "name": "Plain", "state": "queued",
             "reason": ""},
        ],
    }), encoding="utf-8")
    store = tmp_path / "tried_listings.json"

    assert discover.was_tried_recently(stopped, path=store, now=when + 60)
    assert not discover.was_tried_recently(
        stopped, path=store,
        now=when + discover.TRIED_TRANSIENT_COOLDOWN_SECONDS + 1)
    assert not discover.was_tried_recently(URL + "-plain", path=store, now=when + DAY)


def test_run_outcomes_are_written_to_the_record(tmp_path, monkeypatch):
    store = tmp_path / "tried_listings.json"
    monkeypatch.setattr(discover, "TRIED_PATH", store)
    monkeypatch.setitem(main.AUTO, "items", [
        {"url": URL, "name": "Cable organizer X", "state": "skipped",
         "reason": "no licence was read"},
        {"url": URL + "-fail", "name": "Fails", "state": main.FETCH_FAILED,
         "reason": "could not fetch this model"},
        {"url": URL + "-ok", "name": "Published", "state": "published"},
        {"url": URL + "-queued", "name": "Queued", "state": "queued"},
    ])

    main._remember_outcomes()

    saved = json.loads(store.read_text(encoding="utf-8"))
    assert discover._english_url(URL) in saved
    assert discover._english_url(URL + "-fail") in saved
    assert discover._english_url(URL + "-ok") not in saved
    assert discover._english_url(URL + "-queued") not in saved


def test_expired_refusal_is_refreshed_so_it_is_not_opened_every_run(tmp_path):
    """Regression: after the cooldown the old entry was never replaced, so the
    listing was opened again on every run from then on."""
    store = tmp_path / "tried_listings.json"
    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=NOW)
    later = NOW + discover.TRIED_COOLDOWN_SECONDS + 1
    assert not discover.was_tried_recently(URL, path=store, now=later)

    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=later)

    assert discover.was_tried_recently(URL, path=store, now=later + DAY)


def test_corrupt_record_is_kept_aside_not_wiped(tmp_path):
    """Regression: an unreadable record was read as empty, and the next write
    replaced it, forgetting every listing."""
    store = tmp_path / "tried_listings.json"
    store.write_text("{ not json", encoding="utf-8")

    discover.remember_tried(URL, "Cable organizer X", "no licence was read",
                            path=store, now=NOW)

    assert (tmp_path / "tried_listings.corrupt.json").read_text(
        encoding="utf-8") == "{ not json"
    assert discover.was_tried_recently(URL, path=store, now=NOW)
    assert not (tmp_path / "tried_listings.json.tmp").exists()


def test_bad_luck_is_forgiven_while_a_refusal_keeps_its_week(tmp_path):
    """The two tiers of the record. 2026-10-23: one run skipped 20 models on a
    transient failure and cooled every one of them down for a week, so the next
    scans had less to choose from and a 20-model request could not fill 20."""
    store = tmp_path / "tried_listings.json"
    luck = URL + "-luck"
    refused = URL + "-refused"
    discover.remember_tried(luck, "Flaky", "could not fetch this model",
                            path=store, now=NOW, transient=True)
    discover.remember_tried(refused, "Not ours", "licence is ND",
                            path=store, now=NOW)

    after_2h = NOW + discover.TRIED_TRANSIENT_COOLDOWN_SECONDS + 1
    assert discover.was_tried_recently(refused, path=store, now=after_2h)
    assert not discover.was_tried_recently(luck, path=store, now=after_2h)


def test_a_refusal_is_not_downgraded_by_later_bad_luck(tmp_path):
    """The week-long cooldown of a real refusal is the decision that matters: a
    later run that merely failed to read the page must not shorten it."""
    store = tmp_path / "tried_listings.json"
    discover.remember_tried(URL, "Cable organizer X", "licence is ND",
                            path=store, now=NOW)
    discover.remember_tried(URL, "Cable organizer X", "could not fetch this model",
                            path=store, now=NOW + DAY, transient=True)

    entry = json.loads(store.read_text(encoding="utf-8"))[discover._english_url(URL)]
    assert entry["when"] == NOW
    assert entry["transient"] is False
    assert discover.was_tried_recently(URL, path=store, now=NOW + 6 * DAY)


def test_a_stored_reason_without_the_flag_is_read_as_its_own_kind(tmp_path):
    """Records written before the two tiers existed carry no `transient` flag, so
    their stored reason decides — which unwinds the week-long cooldown a
    bad-luck run already put on real listings."""
    store = tmp_path / "tried_listings.json"
    store.write_text(json.dumps({
        discover._english_url(URL): {"name": "Old", "reason": "no usable model file"
                                                               " came back",
                                     "when": NOW},
        discover._english_url(URL + "-lic"): {"name": "Old refusal",
                                              "reason": "licence is ND",
                                              "when": NOW},
    }), encoding="utf-8")

    assert not discover.was_tried_recently(URL, path=store,
                                           now=NOW + discover.TRIED_TRANSIENT_COOLDOWN_SECONDS + 1)
    assert discover.was_tried_recently(URL + "-lic", path=store, now=NOW + DAY)
