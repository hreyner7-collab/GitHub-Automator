"""A listing that a run refused, or could not fetch, is not opened again by the
next Find Models.

Regression: a model that ended as "skipped" (no licence read, no model file) or
"fetch-failed" was written down nowhere, so every run offered it again from the
listing and opened its page again. The same few listings came back run after run
(12 runs in the log for one listing). These tests use a temporary store and never
open a page or touch the account.
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
