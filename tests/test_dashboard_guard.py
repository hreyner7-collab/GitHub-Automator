"""The dashboard's restart policy, tested without a server.

The refused-connection bug was a lifecycle bug: nothing checked the port and
nothing checked that the server ANSWERED. These tests pin the policy that fixes
it (dashboard_guard.decide) and the parsers it stands on, using captured
command output. No server is started and no browser is opened.
"""
import dashboard_guard as guard

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


def test_the_supervisor_stops_both_the_wrapper_and_the_serving_process(monkeypatch):
    """VERIFIED on this machine: `.venv/Scripts/python.exe` re-executes the real
    interpreter, so Popen's PID is a thin WRAPPER and the dashboard serving the
    port is its child. Stopping only the tracked PID orphaned a live server on
    the port, so both are stopped."""
    import importlib
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    try:
        sup_mod = importlib.import_module("dashboard_supervisor")
    finally:
        sys.path.remove(str(root / "scripts"))

    stopped = []
    monkeypatch.setattr(guard, "stop_process", lambda pid, **k: stopped.append(pid) or True)
    sup = sup_mod.Supervisor(port=8000)
    sup.child_pid = 11240     # the wrapper Popen handed back
    sup.server_pid = 11720    # the PID /health reported: the real server
    sup.stop_child()
    assert stopped == [11720, 11240], stopped
    assert sup.child_pid is None and sup.server_pid is None


def test_a_wrapper_that_is_alive_keeps_the_child_alive(monkeypatch):
    """Liveness must consider both PIDs: on a re-exec venv the wrapper can stay
    up while the server is gone, and vice versa."""
    import importlib
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    try:
        sup_mod = importlib.import_module("dashboard_supervisor")
    finally:
        sys.path.remove(str(root / "scripts"))

    monkeypatch.setattr(guard, "http_status", lambda url, timeout=None: None)
    monkeypatch.setattr(guard, "port_holder", lambda port: 4242)
    monkeypatch.setattr(guard, "port_holder_detail",
                        lambda pid: {"pid": pid, "image": "python.exe", "ours": True})
    monkeypatch.setattr(guard, "process_alive", lambda pid: pid == 4242)
    sup = sup_mod.Supervisor(port=8000)
    sup.child_pid = 111
    sup.server_pid = 4242
    assert sup.probe() == guard.RESTART


def test_a_supervisor_next_to_a_running_dashboard_watches_it(monkeypatch):
    """Started while a dashboard is already up (at logon, or by hand): it must
    adopt that server and keep watching it. It must not start a second copy (which
    dies on the port and leaves you serving old code) and it must not exit (which
    would leave a live server with nobody watching it)."""
    import importlib
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    try:
        sup_mod = importlib.import_module("dashboard_supervisor")
    finally:
        sys.path.remove(str(root / "scripts"))

    monkeypatch.setattr(guard, "http_status", lambda url, timeout=None: 200)
    monkeypatch.setattr(guard, "port_holder", lambda port: 5150)
    monkeypatch.setattr(guard, "health_json",
                        lambda url=None, timeout=None: {"pid": 5150, "build": "abc123"})
    monkeypatch.setattr(guard, "process_alive", lambda pid: False)
    sup = sup_mod.Supervisor(port=8000)
    assert sup.cycle() is True, "adopting must not end the supervision"
    assert sup.server_pid == 5150


def test_no_answer_is_none_not_zero(monkeypatch):
    def refused(url, timeout=None):
        raise ConnectionRefusedError("nothing there")

    monkeypatch.setattr(guard.urllib.request, "urlopen", refused)
    assert guard.http_status("http://127.0.0.1:8000/") is None
    assert guard.health_json("http://127.0.0.1:8000/health") == {}
