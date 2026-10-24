"""The timing ledger: "faster" has to be a number.

The pipeline recorded no timings at all, so a speedup could not be measured.
These tests pin the accounting itself -- a run resets its own table, a phase
that RAISES is still recorded, and the run log carries the table.
"""
import json

import pytest

import timing


def _total(name: str) -> float:
    return next(r["total_s"] for r in timing.snapshot()["phases"] if r["phase"] == name)


def test_a_phase_is_recorded_with_its_cost():
    timing.reset()
    with timing.phase("fetch"):
        pass
    row = next(r for r in timing.snapshot()["phases"] if r["phase"] == "fetch")
    assert row["count"] == 1
    assert row["total_s"] >= 0


def test_repeated_phases_accumulate_and_count():
    timing.reset()
    for _ in range(3):
        with timing.phase("harvest.read"):
            pass
    row = next(r for r in timing.snapshot()["phases"] if r["phase"] == "harvest.read")
    assert row["count"] == 3


def test_a_phase_that_raises_is_still_timed():
    """A phase that fails slowly is exactly the one worth seeing, so the record
    happens in a finally -- never only on the happy path."""
    timing.reset()
    with pytest.raises(ValueError):
        with timing.phase("publish"):
            raise ValueError("upload blew up")
    assert any(r["phase"] == "publish" for r in timing.snapshot()["phases"])


def test_reset_clears_the_run_and_keeps_the_process_ledger():
    timing.reset()
    with timing.phase("discovery"):
        pass
    before = timing.process_snapshot()["phases"]
    timing.reset()
    assert timing.snapshot()["phases"] == []
    after = timing.process_snapshot()["phases"]
    assert after and any(r["phase"] == "discovery" for r in after)
    # the process ledger never shrinks across a reset
    assert next(r["count"] for r in after if r["phase"] == "discovery") >= \
        next(r["count"] for r in before if r["phase"] == "discovery")


def test_snapshot_is_ordered_slowest_first():
    timing.reset()
    timing.record("fast", 0.01)
    timing.record("slow", 5.0)
    names = [r["phase"] for r in timing.snapshot()["phases"]]
    assert names.index("slow") < names.index("fast")


def test_summary_line_names_the_slowest_phase_and_does_not_round_it_away():
    timing.reset()
    timing.record("publish", 42.5)
    timing.record("fetch", 0.02)
    line = timing.summary_line()
    assert "publish 42.5" in line
    assert "0.0s" not in line          # a 20ms phase must not read as "nothing"


def test_run_log_write_is_atomic_and_keeps_the_previous_log_on_failure(tmp_path):
    """The run log seeds "already published" on the next start, so a crash
    mid-write must never be able to truncate it. Serializing before the swap is
    what makes an unwritable body leave the old log intact."""
    import main

    log = tmp_path / "auto_run_20261015-120000.json"
    main._write_run_log(log, {"job_id": "ok", "timings": {"phases": []}})
    assert json.loads(log.read_text(encoding="utf-8"))["job_id"] == "ok"
    assert not (tmp_path / "auto_run_20261015-120000.json.tmp").exists()

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        main._write_run_log(log, {"job_id": "broken", "x": Unserializable()})
    # the previous, good log is still there and still readable
    assert json.loads(log.read_text(encoding="utf-8"))["job_id"] == "ok"


def test_a_step_mark_times_only_its_own_stretch():
    """Steps() is how one long function is cut up: each mark measures the
    stretch since the previous mark, so the marks add up to the call instead of
    nesting inside each other. Without this the 40 s publish stays one number."""
    import time

    timing.reset()
    m = timing.Steps("publish")
    time.sleep(0.12)
    m("covers")            # the slow step, on purpose
    time.sleep(0.02)
    m("submit")
    rows = {r["phase"]: r for r in timing.snapshot()["phases"]}
    assert rows["publish.covers"]["total_s"] >= 0.12 - 0.001
    assert rows["publish.submit"]["total_s"] < 0.09      # not charged for covers
    total = sum(r["total_s"] for r in rows.values())
    assert 0.13 <= total < 0.35                          # they partition, no double count
    timing.reset()


def test_step_marks_lands_in_the_process_ledger_too():
    """The dashboard answers "what is slow in general" from the process ledger,
    so a step mark must reach it and not only the current run's table."""
    timing.reset()
    m = timing.Steps("fetch")
    m("gallery")
    assert any(r["phase"] == "fetch.gallery"
               for r in timing.process_snapshot()["phases"])
