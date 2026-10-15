"""One owner for two questions the dashboard kept getting wrong.

WHY THIS EXISTS
---------------
The dashboard died silently and the browser showed ERR_CONNECTION_REFUSED with
no explanation, because nothing owned the server's lifecycle:

  * no check that the port was free before starting (a stale process left the
    new one to die on "address already in use", invisibly, behind pythonw),
  * no check that the server actually ANSWERED after it started,
  * no restart when it exited, and no start at logon,
  * and no version anywhere, so "did it pick up my change?" was unanswerable.

Everything here is a pure function or a single small OS probe, so the restart
POLICY can be tested without a server, a browser, or the account.

`decide()` is the whole policy; its result is one of the ACTION_* names and its
table is written out below so a reader can check it against the tests.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = "127.0.0.1"
PORT = 8000

# Restart policy: never thrash. More than RESTART_LIMIT restarts inside
# RESTART_WINDOW_S means the dashboard is broken, not unlucky — stop and report.
RESTART_WINDOW_S = 60.0
RESTART_LIMIT = 5

# The supervisor's own health poll. Short: we want to notice a hang quickly.
HTTP_TIMEOUT_S = 3.0

# Actions the supervisor can take (decide() returns exactly one).
HEALTHY = "healthy"       # something answers on the port and it is ours
ADOPT = "adopt"           # something answers, but not our child: leave it alone
START = "start"           # port free, nothing answering -> start the server
RESTART = "restart"       # our own child holds the port but does not answer (hung)
BLOCKED = "blocked"       # a foreign process owns the port; never kill it blindly
THRASH_STOP = "thrash_stop"   # too many restarts inside the window

# Image names we are willing to call "our own" when deciding whether a process
# holding the port is a stale dashboard rather than somebody else's server.
OUR_IMAGE_RE = re.compile(r"^(python|pythonw|py)(\.exe)?$", re.I)


def dashboard_url(host: str = HOST, port: int = PORT) -> str:
    return "http://%s:%d/" % (host, port)


def health_url(host: str = HOST, port: int = PORT) -> str:
    return dashboard_url(host, port) + "health"


def parse_netstat_pid(text: str, port: int = PORT) -> int | None:
    """The PID LISTENING on `port` inside `netstat -ano` output, or None.

    A real line looks like (columns vary, so it is parsed by field, not offset):

        TCP    127.0.0.1:8000    0.0.0.0:0    LISTENING    10696

    Only a LISTENING row counts: an ESTABLISHED row on the same port belongs to
    a client, and reporting its PID would name the wrong process.
    """
    suffix = ":%d" % port
    for raw in (text or "").splitlines():
        parts = raw.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(suffix):
            continue
        if "LISTENING" not in raw.upper():
            continue
        if parts[-1].isdigit():
            return int(parts[-1])
    return None


def _run(args: list[str], timeout: float = 15.0) -> str:
    """Run a console command without flashing a window. Never raises."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=timeout, creationflags=flags)
        return out.stdout or ""
    except Exception:
        return ""


def netstat_text() -> str:
    return _run(["netstat", "-ano"])


def port_holder(port: int = PORT, netstat: str | None = None) -> int | None:
    """PID listening on `port`, or None when the port is free/unknown."""
    return parse_netstat_pid(netstat if netstat is not None else netstat_text(), port)


def port_holder_detail(pid: int | None) -> dict:
    """Who holds the port, for an honest diagnostic message."""
    if not pid:
        return {"pid": None, "image": None, "ours": False}
    out = _run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"])
    image = None
    m = re.search(r'"([^"]+)","%d"' % pid, out or "")
    if m:
        image = m.group(1)
    elif out.strip():
        first = out.strip().splitlines()[0].split(",")
        if first and first[0].strip('"'):
            image = first[0].strip('"')
    return {"pid": pid, "image": image,
            "ours": bool(image and OUR_IMAGE_RE.match(image))}


def process_alive(pid: int | None) -> bool:
    """True while `pid` exists. False for None/invalid.

    The PID column is compared EXACTLY. A substring test ("is 123 in the output") 
    is wrong: tasklist prints memory sizes like "<1,234 K", and Windows recycles
    PIDs, so a dead PID could read as alive. That false positive is not cosmetic:
    the supervisor's own lock and its "is my child still up?" check both use this,
    and a phantom-alive PID silently stops the dashboard from ever being started
    again -- the exact failure this file exists to prevent.
    """
    if not pid:
        return False
    if os.name == "nt":
        out = _run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"])
        for line in (out or "").splitlines():
            fields = [f.strip().strip('"') for f in line.split(",")]
            if len(fields) >= 2 and fields[1] == str(pid):
                return True
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def http_status(url: str, timeout: float = HTTP_TIMEOUT_S) -> int | None:
    """HTTP status from `url`, or None when nothing answered.

    5xx still counts as an answer: a server that replies with an error IS
    running and must not trigger a restart loop.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0) or resp.getcode() or 0)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except Exception:
        return None


def health_json(url: str | None = None, timeout: float = HTTP_TIMEOUT_S) -> dict:
    """The dashboard's own /health body, or {} when it could not be read."""
    try:
        with urllib.request.urlopen(url or health_url(), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def process_detail(pid: int | None) -> dict:
    """The image name behind a PID, and whether we may treat it as ours.

    Same lookup as `port_holder_detail`, named for the general question: this is
    used to decide whether a process may be stopped, and the answer must come
    from the image name, never from a guess.
    """
    return port_holder_detail(pid)


def stop_process(pid: int | None, timeout_s: float = 10.0) -> bool:
    """Stop a process, but ONLY when it is one of ours. True once it is gone.

    VERIFIED BUG this exists for: on this machine `.venv/Scripts/python.exe`
    re-executes the real interpreter, so the PID handed back by Popen is a thin
    WRAPPER and the dashboard that actually serves the port is its child. Stopping
    only the tracked PID left a live server orphaned on the port (and the
    supervisor reporting a dashboard it no longer owned). Callers therefore stop
    BOTH the tracked PID and the PID that /health reports -- the process actually
    serving -- and this refuses to touch anything that is not python/pythonw.
    """
    if not pid or not process_alive(pid):
        return True
    if not process_detail(pid).get("ours"):
        return False
    if os.name == "nt":
        _run(["taskkill", "/PID", str(pid), "/F"])
    else:
        import signal
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            return True
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.25)
    return not process_alive(pid)


def should_stop_restarting(restart_times: list[float], now: float,
                           window: float = RESTART_WINDOW_S,
                           limit: int = RESTART_LIMIT) -> bool:
    """True when folding more restarts in would just be thrashing."""
    recent = [t for t in restart_times if now - t <= window]
    return len(recent) >= limit


def decide(http_ok: bool, holder_pid: int | None, child_pid: int | None,
           child_alive: bool, holder_is_ours: bool) -> str:
    """What the supervisor should do this cycle.

    The table, in the order it is applied:

      1. an answer on the port, from our live child   -> HEALTHY
      2. an answer on the port, but our child is gone  -> ADOPT (someone else
         serves it; do not start a second copy, which is how a restart silently
         keeps serving old code)
      3. an answer on the port, and we have no child   -> ADOPT
      4. no answer, our own child holds the port       -> RESTART (it hung)
      5. no answer, a foreign process holds the port   -> BLOCKED
      6. no answer, port free                          -> START
    """
    if http_ok:
        if child_pid and child_alive:
            return HEALTHY
        return ADOPT
    if holder_pid:
        if child_pid and holder_pid == child_pid:
            return RESTART
        if holder_is_ours:
            return RESTART
        return BLOCKED
    return START


def python_executable() -> str:
    """The interpreter that should run the dashboard, preferring the venv.

    python.exe, not pythonw.exe: the server's output has to reach the log file.
    Hiding the window is the launcher's job (the Startup entry runs it through a
    .vbs with window style 0) -- pythonw is what made every startup error
    invisible in the first place.
    """
    names = ("python.exe",) if os.name == "nt" else ("python3", "python")
    for name in names:
        cand = os.path.join(os.path.dirname(sys.executable or ""), name)
        if cand and os.path.exists(cand):
            return cand
    return sys.executable
