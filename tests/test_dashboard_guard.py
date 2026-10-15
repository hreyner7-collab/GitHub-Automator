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


def test_no_answer_is_none_not_zero(monkeypatch):
    def refused(url, timeout=None):
        raise ConnectionRefusedError("nothing there")

    monkeypatch.setattr(guard.urllib.request, "urlopen", refused)
    assert guard.http_status("http://127.0.0.1:8000/") is None
    assert guard.health_json("http://127.0.0.1:8000/health") == {}
