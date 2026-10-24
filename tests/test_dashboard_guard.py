"""The dashboard's restart policy, tested without a server.

The refused-connection bug was a lifecycle bug: nothing checked the port and
nothing checked that the server ANSWERED. These tests pin the policy that fixes
it (dashboard_guard.decide) and the parsers it stands on, using captured
command output. No server is started and no browser is opened.
"""
import time

import pytest

import dashboard_guard as guard


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    """The supervisor module, with its real paths redirected into tmp_path.

    VERIFIED DEFECT: the tests used to append their fake decisions to the real
    artifacts/dashboard.log and to take and then DELETE the real
    artifacts/dashboard_supervisor.lock -- so running the suite could erase a
    running supervisor's lock and write "stopped PID 11720" lines into the log a
    human reads to diagnose a real restart. Tests must never touch the app's own
    artifacts.
    """
    import importlib
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    try:
        sup_mod = importlib.import_module("dashboard_supervisor")
    finally:
        sys.path.remove(str(root / "scripts"))
    monkeypatch.setattr(sup_mod, "LOG_PATH", tmp_path / "dashboard_supervisor.log")
    monkeypatch.setattr(sup_mod, "CHILD_LOG_PATH", tmp_path / "dashboard.log")
    monkeypatch.setattr(sup_mod, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(sup_mod, "LOCK_PATH", tmp_path / "supervisor.lock")
    # Same reason: a real artifacts/auto_heartbeat.json would decide how many
    # timed-out probes a test thinks the dashboard gets before it is restarted.
    monkeypatch.setattr(sup_mod, "HEARTBEAT_PATH", tmp_path / "auto_heartbeat.json")
    return sup_mod

class TestIsStaleBuild:
    def test_explicitly_current_is_not_stale(self):
        assert guard.is_stale_build({"build_is_current": True}) is False

    def test_explicitly_not_current_is_stale(self):
        assert guard.is_stale_build({"build_is_current": False}) is True

    @pytest.mark.parametrize("body", [{}, None, {"build": "unknown"}])
    def test_shy_of_information_is_not_stale(self, body):
        # A dashboard old enough to lack the stamp must not be killed for it.
        assert guard.is_stale_build(body) is False


class TestRunInProgress:
    def test_discovering_and_running_are_active(self):
        assert guard.run_in_progress({"state": "discovering"})
        assert guard.run_in_progress({"state": "running"})

    def test_a_midway_item_blocks_the_restart(self):
        # The run may already be done/errored while its last item still uploads.
        assert guard.run_in_progress(
            {"state": "done", "counts": {"working": 1}}) is True

    def test_idle_with_no_working_items_is_free(self):
        assert guard.run_in_progress(
            {"state": "idle", "counts": {"working": 0}}) is False

    def test_unreadable_answers_wait(self):
        # The cost of waiting a cycle is small; the cost of guessing wrong
        # is a killed publish, so an unreadable /auto/status means wait.
        assert guard.run_in_progress({}) is True
        assert guard.run_in_progress(None) is True


# Real `netstat -ano` shapes: an IPv4 listener, an IPv6 listener on another
# port, a client connection on the SAME port, and a listener we do not care
# about. Only the first row may ever be read as "the dashboard's PID".
NETSTAT = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       10696
  TCP    127.0.0.1:8000         127.0.0.1:54329        ESTABLISHED     555
  TCP    127.0.0.1:9222         0.0.0.0:0              LISTENING       777
  TCP    [::1]:8000             [::]:0                 LISTENING       888
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       4
"""


def test_the_listening_row_is_read_not_the_client_row():
    assert guard.parse_netstat_pid(NETSTAT, 8000) == 10696


def test_a_free_port_reports_nothing():
    assert guard.parse_netstat_pid(NETSTAT, 8123) is None
    assert guard.parse_netstat_pid("", 8000) is None


def test_junk_output_is_not_mistaken_for_a_listener():
    assert guard.parse_netstat_pid("TCP 127.0.0.1:8000 ESTABLISHED x", 8000) is None


def test_restart_policy_stops_thrashing_inside_the_window():
    now = 1000.0
    assert not guard.should_stop_restarting([], now)
    # four restarts in the window is still worth trying
    assert not guard.should_stop_restarting([now - 50, now - 40, now - 30, now - 20], now)
    # the fifth inside the window is not
    assert guard.should_stop_restarting(
        [now - 50, now - 40, now - 30, now - 20, now - 10], now)


def test_old_restarts_fall_out_of_the_window():
    now = 1000.0
    old = [now - 600, now - 500, now - 400, now - 300, now - 200]
    assert not guard.should_stop_restarting(old, now)


def test_decision_table():
    # 1. an answer on the port from our live child -> nothing to do
    assert guard.decide(True, 10, 10, True, True) == guard.HEALTHY
    # 2/3. an answer, but not from our child -> adopt, never start a second copy
    assert guard.decide(True, 10, None, False, False) == guard.ADOPT
    assert guard.decide(True, 10, 99, False, True) == guard.ADOPT
    # 4. no answer and our own child holds the port -> it hung
    assert guard.decide(False, 10, 10, True, True) == guard.RESTART
    # 5. no answer and the port belongs to someone else -> never kill it
    assert guard.decide(False, 10, None, False, False) == guard.BLOCKED
    # 6. no answer and the port is free -> start
    assert guard.decide(False, None, None, False, False) == guard.START


def test_adopting_is_preferred_over_starting_a_second_copy():
    """The 'my changes did not take effect' trap: a second copy dies on the port
    while the first keeps serving old code. Answering means adopt."""
    assert guard.decide(True, 4242, None, False, False) != guard.START


def test_a_5xx_answer_still_counts_as_running(monkeypatch):
    """A server replying with an error IS running; restarting it would loop."""
    import urllib.error

    def boom(url, timeout=None):
        raise urllib.error.HTTPError(url, 500, "server error", {}, None)

    monkeypatch.setattr(guard.urllib.request, "urlopen", boom)
    assert guard.http_status("http://127.0.0.1:8000/") == 500


def test_stop_process_refuses_a_process_that_is_not_ours(monkeypatch):
    """Never kill by PID alone: the port could be held by somebody else's work.
    Stopping is allowed only when the image name says it is ours."""
    monkeypatch.setattr(guard, "process_alive", lambda pid: True)
    monkeypatch.setattr(guard, "process_detail",
                        lambda pid: {"pid": pid, "image": "explorer.exe",
                                     "ours": False})
    killed = []
    monkeypatch.setattr(guard, "_run", lambda *a, **k: killed.append(a))
    assert guard.stop_process(4321) is False
    assert killed == [], "nothing may be terminated for a foreign image"


def test_stop_process_is_a_no_op_for_a_process_that_is_already_gone(monkeypatch):
    monkeypatch.setattr(guard, "process_alive", lambda pid: False)
    assert guard.stop_process(999) is True
    assert guard.stop_process(None) is True


def test_the_supervisor_stops_both_the_wrapper_and_the_serving_process(supervisor,
                                                                       monkeypatch):
    """VERIFIED on this machine: `.venv/Scripts/python.exe` re-executes the real
    interpreter, so Popen's PID is a thin WRAPPER and the dashboard serving the
    port is its child. Stopping only the tracked PID orphaned a live server on
    the port, so both are stopped."""
    stopped = []
    monkeypatch.setattr(guard, "stop_process", lambda pid, **k: stopped.append(pid) or True)
    sup = supervisor.Supervisor(port=8000)
    sup.child_pid = 11240     # the wrapper Popen handed back
    sup.server_pid = 11720    # the PID /health reported: the real server
    sup.stop_child()
    assert stopped == [11720, 11240], stopped
    assert sup.child_pid is None and sup.server_pid is None


def test_a_wrapper_that_is_alive_keeps_the_child_alive(supervisor, monkeypatch):
    """Liveness must consider both PIDs: on a re-exec venv the wrapper can stay
    up while the server is gone, and vice versa."""
    monkeypatch.setattr(guard, "http_status", lambda url, timeout=None: None)
    monkeypatch.setattr(guard, "port_holder", lambda port: 4242)
    monkeypatch.setattr(guard, "port_holder_detail",
                        lambda pid: {"pid": pid, "image": "python.exe", "ours": True})
    monkeypatch.setattr(guard, "process_alive", lambda pid: pid == 4242)
    sup = supervisor.Supervisor(port=8000)
    sup.child_pid = 111
    sup.server_pid = 4242
    assert sup.probe() == guard.RESTART


def test_a_supervisor_next_to_a_running_dashboard_watches_it(supervisor, monkeypatch):
    """Started while a dashboard is already up (at logon, or by hand): it must
    adopt that server and keep watching it. It must not start a second copy (which
    dies on the port and leaves you serving old code) and it must not exit (which
    would leave a live server with nobody watching it)."""
    monkeypatch.setattr(guard, "http_status", lambda url, timeout=None: 200)
    monkeypatch.setattr(guard, "port_holder", lambda port: 5150)
    monkeypatch.setattr(guard, "health_json",
                        lambda url=None, timeout=None: {"pid": 5150, "build": "abc123"})
    monkeypatch.setattr(guard, "process_alive", lambda pid: False)
    sup = supervisor.Supervisor(port=8000)
    assert sup.cycle() is True, "adopting must not end the supervision"
    assert sup.server_pid == 5150


def test_process_alive_compares_the_pid_column_exactly(monkeypatch):
    """A substring test reads a dead PID as alive (tasklist prints memory sizes
    like "<1,234 K", and Windows recycles PIDs). A phantom-alive PID blocks the
    supervisor's lock, so the dashboard would never be started again."""
    real = ('"python.exe","1234","Console","1","5,716 K"\r\n')
    monkeypatch.setattr(guard.os, "name", "nt")
    monkeypatch.setattr(guard, "_run", lambda *a, **k: real)
    assert guard.process_alive(1234) is True, "the real PID must be found"
    # a memory size that CONTAINS the digits must not be mistaken for the PID
    other = ('"python.exe","99","Console","1","1,234 K"\r\n')
    monkeypatch.setattr(guard, "_run", lambda *a, **k: other)
    assert guard.process_alive(1234) is False


def test_a_lock_held_by_a_recycled_pid_does_not_block_a_new_supervisor(supervisor, monkeypatch):
    """If the lock's PID now belongs to some other program, the lock is stale."""
    lock = supervisor.LOCK_PATH
    lock.write_text("424242\n2026-10-15 10:00:00\n", encoding="utf-8")
    monkeypatch.setattr(guard, "process_alive", lambda pid: True)   # PID reused
    monkeypatch.setattr(guard, "process_detail",
                        lambda pid: {"pid": pid, "image": "explorer.exe",
                                     "ours": False})
    try:
        assert supervisor.acquire_lock() is True, "a recycled PID must not block startup"
        assert lock.read_text(encoding="utf-8").strip().split("\n")[0] == str(__import__("os").getpid())
    finally:
        supervisor.release_lock()


def test_no_answer_is_none_not_zero(monkeypatch):
    def refused(url, timeout=None):
        raise ConnectionRefusedError("nothing there")

    monkeypatch.setattr(guard.urllib.request, "urlopen", refused)
    assert guard.http_status("http://127.0.0.1:8000/") is None
    assert guard.health_json("http://127.0.0.1:8000/health") == {}


class TestAutoResetGrace:
    """`auto_reset` serves an update on disk without being asked — but it must
    not race a tree that is being edited.

    2026-10-24: while the pipeline was being repaired, every edit made the
    running server read as stale, and the supervisor restarted it for each one.
    Five restarts in sixty seconds tripped the crash breaker, which exited the
    supervisor and left the dashboard DOWN with nothing watching it — the cure
    for staleness became the outage.
    """

    def _sup(self, sup_mod, monkeypatch, disks, state="idle"):
        """A supervisor that talks to a fake dashboard, with the restart side
        effects recorded instead of run."""
        calls = []
        queue = list(disks)

        def fake_health_json(url):
            if url.endswith("auto/status"):
                return {"state": state, "counts": {"working": 0}}
            return {"build_is_current": False, "build": "aaaaaaa", "pid": 1,
                    "disk_fingerprint": queue.pop(0) if queue else disks[-1]}

        monkeypatch.setattr(sup_mod.guard, "health_json", fake_health_json)
        sup = sup_mod.Supervisor(port=8000, supervise=False)
        sup.stop_child = lambda: calls.append("stop")
        sup.clear_port_holder = lambda: calls.append("clear") or True
        sup.note_restart = lambda: calls.append("note") or True
        sup.start = lambda: calls.append("start") or True
        sup.write_report = lambda _reason: calls.append("report")
        return sup, calls

    def test_the_first_stale_reading_restarts_nothing(self, supervisor, monkeypatch):
        sup, calls = self._sup(supervisor, monkeypatch, ["fp1"])
        assert sup.auto_reset() is True
        assert calls == [], "a stale reading must be watched, not acted on"
        assert sup.stale_since is not None

    def test_an_actively_edited_tree_is_never_raced(self, supervisor, monkeypatch):
        """Each edit is a NEW stale build, so the clock restarts and the
        supervisor keeps waiting — exactly the case that used to thrash."""
        sup, calls = self._sup(supervisor, monkeypatch, ["fp1", "fp2", "fp3"])
        for _ in range(3):
            sup.stale_since = time.time() - (supervisor.STALE_RESET_GRACE_S + 1)
            assert sup.auto_reset() is True
        assert calls == [], "a fingerprint that keeps changing must not restart: %s" % calls

    def test_a_build_that_stayed_stale_is_reset(self, supervisor, monkeypatch):
        sup, calls = self._sup(supervisor, monkeypatch, ["fp1", "fp1"])
        sup.auto_reset()                       # first look: start the clock
        sup.stale_since = time.time() - (supervisor.STALE_RESET_GRACE_S + 1)
        assert sup.auto_reset() is True
        assert calls == ["stop", "clear", "note", "start"], calls
        assert sup.stale_since is None, "a completed reset clears the pending one"

    def test_a_run_in_progress_still_defends_the_restart(self, supervisor, monkeypatch):
        sup, calls = self._sup(supervisor, monkeypatch, ["fp1", "fp1"],
                               state="running")
        sup.auto_reset()
        sup.stale_since = time.time() - (supervisor.STALE_RESET_GRACE_S + 1)
        assert sup.auto_reset() is True
        assert calls == [], "a mid-run restart would leave a half-published model"

    def test_a_current_build_resets_the_pending_staleness(self, supervisor, monkeypatch):
        sup, _calls = self._sup(supervisor, monkeypatch, ["fp1"])
        sup.auto_reset()
        assert sup.stale_since is not None
        monkeypatch.setattr(supervisor.guard, "is_stale_build",
                            lambda _health: False)
        assert sup.auto_reset() is True
        assert sup.stale_since is None, "once it is current, the pending reset goes away"


def _beat(path, state="running", age=0.0, job="20261024-050936", published=7):
    """Write the heartbeat file the way `_auto_worker` does, and return it."""
    import json

    path.write_text(json.dumps({"job_id": job, "state": state,
                                 "at": time.time() - age,
                                 "published": published, "requested": 20}),
                    encoding="utf-8")
    return path


class TestRunHeartbeat:
    """`run_heartbeat` is the only evidence of a live run that works while the
    dashboard is not answering — asking it is exactly what just timed out.
    """

    def test_a_fresh_beat_of_an_active_state_is_read(self, supervisor, monkeypatch):
        monkeypatch.setattr(supervisor, "HEARTBEAT_PATH",
                            _beat(supervisor.HEARTBEAT_PATH))
        hb = supervisor.run_heartbeat()
        assert hb is not None and hb["job_id"] == "20261024-050936"
        assert hb["published"] == 7

    @pytest.mark.parametrize("state", ["discovering", "running"])
    def test_both_active_states_count_as_a_run(self, supervisor, monkeypatch, state):
        monkeypatch.setattr(supervisor, "HEARTBEAT_PATH",
                            _beat(supervisor.HEARTBEAT_PATH, state=state))
        assert supervisor.run_heartbeat() is not None

    @pytest.mark.parametrize("state", ["done", "error", "idle", "stopped", None])
    def test_a_run_that_said_it_finished_is_not_a_run(self, supervisor, monkeypatch,
                                                      state):
        monkeypatch.setattr(supervisor, "HEARTBEAT_PATH",
                            _beat(supervisor.HEARTBEAT_PATH, state=state))
        assert supervisor.run_heartbeat() is None, \
            "a finished run must not shield a hung server forever"

    def test_a_beat_past_its_freshness_is_worth_nothing(self, supervisor, monkeypatch):
        """Discovery can go quiet for minutes, so a beat is trusted for
        HEARTBEAT_FRESH_S — but only that long, or a wedged run would hold the
        port and the models with it."""
        monkeypatch.setattr(supervisor, "HEARTBEAT_PATH", _beat(
            supervisor.HEARTBEAT_PATH, age=supervisor.HEARTBEAT_FRESH_S + 1))
        assert supervisor.run_heartbeat() is None, \
            "a wedged run stops being evidence once it stops writing"

    def test_a_beat_from_the_future_is_not_evidence(self, supervisor, monkeypatch):
        monkeypatch.setattr(supervisor, "HEARTBEAT_PATH",
                            _beat(supervisor.HEARTBEAT_PATH, age=-60.0))
        assert supervisor.run_heartbeat() is None

    @pytest.mark.parametrize("body", ["", "not json", "[]",
                                      '{"state": "running"}', '{"state": 7}'])
    def test_a_file_that_is_not_a_beat_is_no_beat(self, supervisor, monkeypatch, body):
        supervisor.HEARTBEAT_PATH.write_text(body, encoding="utf-8")
        assert supervisor.run_heartbeat() is None

    def test_no_file_at_all_is_no_beat(self, supervisor, monkeypatch):
        assert not supervisor.HEARTBEAT_PATH.exists()
        assert supervisor.run_heartbeat() is None


class TestHungProbes:
    """Silence earns a restart only after it has lasted, and never while an
    auto run is heartbeating.

    VERIFIED DEFECT (live 2026-10-24 05:11:14): the log reads
    "the dashboard is holding port 8000 but not answering — restarting", ninety
    seconds into the user's 20-model publish. One /health probe that took over
    3s — which is normal while the same process drives a browser, waits on
    downloads and writes the ledger — killed the run and left its models
    half-published. That is "when i do 1 to 5 it is fine but when i do 20 it
    stops working". The counter the code talked about was never incremented, so
    a single timeout was enough to pull the rug.
    """

    def _sup(self, sup_mod):
        """A supervisor whose only input is a scripted decision, and whose
        restart side effects are recorded instead of run.

        The heartbeat file starts out absent (the fixture redirects it into
        tmp_path), which is the idle case these tests compare against.
        """
        calls = []
        sup = sup_mod.Supervisor(port=8000, supervise=False)
        sup.probe = lambda: sup_mod.guard.RESTART
        # HEALTHY and ADOPT both fall through to auto_reset, which would poll
        # the real dashboard for a stale build. These tests are about counting.
        sup.auto_reset = lambda: True
        sup.stop_child = lambda: calls.append("stop")
        sup.clear_port_holder = lambda: calls.append("clear") or True
        sup.note_restart = lambda: calls.append("note") or True
        sup.start = lambda: calls.append("start") or True
        sup.write_report = lambda _reason: calls.append("report")
        return sup, calls

    def _log(self, supervisor):
        return supervisor.LOG_PATH.read_text(encoding="utf-8") \
            if supervisor.LOG_PATH.exists() else ""

    def test_one_timed_out_probe_restarts_nothing(self, supervisor):
        sup, calls = self._sup(supervisor)
        assert sup.cycle() is True
        assert calls == [], "one slow probe is a busy machine, not a dead server"
        assert sup.silent_probes == 1
        assert "busy machine" in self._log(supervisor)

    def test_an_answer_between_resumes_the_tally(self, supervisor):
        """Only CONSECUTIVE silence is hung: a server that answers once was
        alive, so the count goes back to the start."""
        sup, calls = self._sup(supervisor)
        for _ in range(supervisor.HUNG_PROBES - 1):
            assert sup.cycle() is True
        sup.probe = lambda: supervisor.guard.HEALTHY
        assert sup.cycle() is True
        assert sup.silent_probes == 0
        sup.probe = lambda: supervisor.guard.RESTART
        for _ in range(supervisor.HUNG_PROBES - 1):
            assert sup.cycle() is True
        assert calls == [], "silence interrupted by an answer is not HUNG_PROBES in a row"

    def test_prolonged_silence_is_still_restarted(self, supervisor):
        """The other half of the fix: a dashboard that is genuinely wedged and
        publishing nothing must not become unkillable."""
        sup, calls = self._sup(supervisor)
        for _ in range(supervisor.HUNG_PROBES):
            assert sup.cycle() is True
        assert calls == ["stop", "clear", "note", "start"], calls
        assert sup.silent_probes == 0
        assert "not answered %d probes" % supervisor.HUNG_PROBES \
            in self._log(supervisor)

    def test_a_beating_run_buys_the_dashboard_many_more_probes(self, supervisor,
                                                              monkeypatch):
        hb = {"job_id": "20261024-050936", "state": "running", "published": 7}
        monkeypatch.setattr(supervisor, "run_heartbeat", lambda *a, **k: hb)
        sup, calls = self._sup(supervisor)
        for _ in range(supervisor.HUNG_PROBES):
            assert sup.cycle() is True
        assert calls == [], "an idle server would be restarted here; a running one must not be"
        for _ in range(supervisor.RUN_HUNG_PROBES - supervisor.HUNG_PROBES - 1):
            assert sup.cycle() is True
        assert calls == []
        text = self._log(supervisor)
        assert "20261024-050936" in text and "half-published" in text, \
            "the log has to say WHY it is waiting, or it reads as a hang"

    def test_a_beating_run_that_really_is_dead_does_get_restarted(self, supervisor,
                                                                  monkeypatch):
        """RUN_HUNG_PROBES is two minutes of silence; past it even a fresh
        heartbeat loses, because the models are not publishing either."""
        monkeypatch.setattr(supervisor, "run_heartbeat",
                            lambda *a, **k: {"job_id": "x", "state": "running",
                                             "published": 1})
        sup, calls = self._sup(supervisor)
        for _ in range(supervisor.RUN_HUNG_PROBES):
            assert sup.cycle() is True
        assert calls == ["stop", "clear", "note", "start"], calls

    def test_a_cold_beat_on_disk_protects_nothing(self, supervisor):
        """Through the real reader, not a stub: a heartbeat file that has gone
        cold falls back to the ordinary patience."""
        _beat(supervisor.HEARTBEAT_PATH, age=supervisor.HEARTBEAT_FRESH_S + 1)
        assert supervisor.run_heartbeat() is None
        sup, calls = self._sup(supervisor)
        for _ in range(supervisor.HUNG_PROBES):
            assert sup.cycle() is True
        assert calls == ["stop", "clear", "note", "start"], calls

    def test_a_fresh_beat_on_disk_buys_the_run_patience(self, supervisor):
        _beat(supervisor.HEARTBEAT_PATH, published=3)
        sup, calls = self._sup(supervisor)
        for _ in range(supervisor.HUNG_PROBES):
            assert sup.cycle() is True
        assert calls == [], "the run was still writing when the port went quiet"
        assert "3 published" in self._log(supervisor)

    def test_the_run_silences_the_counter_when_it_answers(self, supervisor,
                                                          monkeypatch):
        """The shield is the missing answer, not the missing run: once the
        dashboard replies the tally resets, whoever was heartbeating."""
        monkeypatch.setattr(supervisor, "run_heartbeat",
                            lambda *a, **k: {"job_id": "j", "state": "running",
                                             "published": 2})
        sup, calls = self._sup(supervisor)
        for _ in range(supervisor.HUNG_PROBES - 1):
            assert sup.cycle() is True
        assert sup.silent_probes == supervisor.HUNG_PROBES - 1
        sup.probe = lambda: supervisor.guard.HEALTHY
        assert sup.cycle() is True
        assert calls == [] and sup.silent_probes == 0
