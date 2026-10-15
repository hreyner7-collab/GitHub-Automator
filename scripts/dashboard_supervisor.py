#!/usr/bin/env python
"""Keep the dashboard up, and say honestly when it cannot be.

THE BUG THIS FIXES
------------------
You kept getting:

    This site can't be reached
    127.0.0.1 refused to connect.

Nothing was broken in the app. The server simply was not running -- never
started, exited, or cleared by a reboot -- and because it was launched as a
hidden `pythonw main.py` with every error discarded, there was nothing to tell
you which. So: this owns the process. It checks the port before starting (so
"address already in use" can never be silent), verifies the server actually
ANSWERS (an open port is not a working server), restarts it when it hangs or
dies, and writes every decision to a log you can read.

Usage
-----
    python scripts/dashboard_supervisor.py             # start if down, then supervise
    python scripts/dashboard_supervisor.py --status    # report only; exit 1 if not serving
    python scripts/dashboard_supervisor.py --restart   # replace a stale dashboard, then supervise
    python scripts/dashboard_supervisor.py --once      # start if down and verify, then exit
    python scripts/dashboard_supervisor.py --no-supervise

It never opens a browser and never reads the account: main.py's own startup only
seeds the ledger from files on disk.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import dashboard_guard as guard  # noqa: E402  (needs the path above)

LOG_PATH = BASE_DIR / "artifacts" / "dashboard.log"
REPORT_PATH = BASE_DIR / "artifacts" / "dashboard_supervisor_report.json"

# How long a freshly started server may take to answer. Generous: the app
# imports playwright and rembg, which are slow on a cold disk.
START_TIMEOUT_S = 60.0
POLL_EVERY_S = 3.0
# Consecutive failed probes before a live-but-silent server is called hung.
HUNG_PROBES = 3


def log(message: str) -> None:
    line = "[%s] supervisor: %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


class Supervisor:
    """Holds the state the policy needs: our child, and our restart history."""

    def __init__(self, port: int = guard.PORT, replace: bool = False,
                 supervise: bool = True) -> None:
        self.port = port
        self.replace = replace
        self.supervise = supervise
        self.url = guard.dashboard_url(port=port)
        self.child = None
        self.child_pid: int | None = None
        self.restart_times: list[float] = []
        self.silent_probes = 0
        self.gave_up_reason: str | None = None

    # ── probes ───────────────────────────────────────────────────────────────
    def probe(self) -> str:
        status = guard.http_status(self.url)
        holder = guard.port_holder(self.port)
        alive = guard.process_alive(self.child_pid)
        detail = guard.port_holder_detail(holder)
        action = guard.decide(http_ok=status is not None, holder_pid=holder,
                              child_pid=self.child_pid, child_alive=alive,
                              holder_is_ours=bool(detail.get("ours")))
        self.last = {"http": status, "holder": holder, "detail": detail,
                     "child_alive": alive, "action": action}
        return action

    # ── actions ──────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start the dashboard and wait until it really answers."""
        holder = guard.port_holder(self.port)
        if holder:
            detail = guard.port_holder_detail(holder)
            log("refusing to start: port %d is held by PID %s (%s)"
                % (self.port, holder, detail.get("image") or "unknown"))
            return False
        python = guard.python_executable()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            logfh = LOG_PATH.open("a", encoding="utf-8", buffering=1)
        except Exception as e:
            log("cannot open the log file (%s); starting anyway" % e)
            logfh = subprocess.DEVNULL
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        log("starting: %s main.py (port %d)" % (python, self.port))
        try:
            self.child = subprocess.Popen(
                [python, "main.py"], cwd=str(BASE_DIR),
                stdout=logfh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=flags)
        except Exception as e:
            log("could not start the dashboard: %s" % e)
            return False
        self.child_pid = self.child.pid
        log("child PID %d" % self.child_pid)
        deadline = time.time() + START_TIMEOUT_S
        while time.time() < deadline:
            status = guard.http_status(self.url)
            if status is not None:
                health = guard.health_json(guard.health_url(port=self.port))
                log("answering on %s (HTTP %s) build=%s pid=%s uptime=%ss"
                    % (self.url, status, health.get("build", "?"),
                       health.get("pid", "?"), health.get("uptime_s", "?")))
                return True
            if not guard.process_alive(self.child_pid):
                log("the child exited before it answered (see the log above this line)")
                return False
            time.sleep(1.0)
        log("no answer within %.0fs — treating the start as failed" % START_TIMEOUT_S)
        self.stop_child()
        return False

    def stop_child(self) -> None:
        """Stop ONLY the child this supervisor started."""
        if not self.child_pid or not guard.process_alive(self.child_pid):
            self.child_pid = None
            return
        log("stopping child PID %d" % self.child_pid)
        try:
            if os.name == "nt":
                guard._run(["taskkill", "/PID", str(self.child_pid), "/F"])
            else:
                self.child.terminate()
        except Exception as e:
            log("could not stop child PID %d: %s" % (self.child_pid, e))
        for _ in range(20):
            if not guard.process_alive(self.child_pid):
                break
            time.sleep(0.5)
        self.child_pid = None

    def clear_port_holder(self) -> bool:
        """Free the port when the holder is a stale process of ours.

        A foreign server is never killed: that could be somebody else's work.
        """
        holder = guard.port_holder(self.port)
        if not holder:
            return True
        detail = guard.port_holder_detail(holder)
        if not detail.get("ours"):
            log("port %d is held by PID %s (%s) — not a Python process, leaving it alone"
                % (self.port, holder, detail.get("image") or "unknown"))
            return False
        log("port %d is held by a stale dashboard (PID %s %s) — stopping it"
            % (self.port, holder, detail.get("image")))
        guard._run(["taskkill", "/PID", str(holder), "/F"])
        for _ in range(20):
            if guard.port_holder(self.port) is None:
                return True
            time.sleep(0.5)
        log("port %d is still held after the kill" % self.port)
        return False

    def note_restart(self) -> bool:
        """Record a restart; False when the policy says to stop trying."""
        now = time.time()
        self.restart_times.append(now)
        self.restart_times = [t for t in self.restart_times
                              if now - t <= guard.RESTART_WINDOW_S]
        if guard.should_stop_restarting(self.restart_times, now):
            self.gave_up_reason = (
                "%d restarts in %.0fs — the dashboard is failing to start, not unlucky"
                % (len(self.restart_times), guard.RESTART_WINDOW_S))
            return False
        return True

    def write_report(self, reason: str) -> None:
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tail = ""
            try:
                lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = "\n".join(lines[-40:])
            except Exception:
                pass
            REPORT_PATH.write_text(json.dumps({
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "port": self.port,
                "restarts_in_window": len(self.restart_times),
                "port_holder": guard.port_holder_detail(guard.port_holder(self.port)),
                "last_log_lines": tail,
            }, indent=1), encoding="utf-8")
            log("wrote %s" % REPORT_PATH.name)
        except Exception as e:
            log("could not write the report: %s" % e)

    # ── the loop ─────────────────────────────────────────────────────────────
    def cycle(self) -> bool:
        """One pass. Returns False when the supervisor should stop."""
        action = self.probe()
        status = self.last["http"]
        if action == guard.HEALTHY:
            self.silent_probes = 0
            return True
        if action == guard.ADOPT:
            # Something answers but it is not our child. Starting a second copy
            # would die on the port and leave you serving old code.
            if status is not None and self.child_pid is None:
                health = guard.health_json(guard.health_url(port=self.port))
                log("a dashboard is already serving %s (build=%s pid=%s) — adopting it"
                    % (self.url, health.get("build", "?"), health.get("pid", "?")))
                return False
            self.silent_probes += 1
            if self.silent_probes < HUNG_PROBES:
                return True
            self.silent_probes = 0
            log("our child is gone but the port answers — restarting it")
            if not self.note_restart():
                return False
            return self.start()
        if action == guard.BLOCKED:
            detail = self.last["detail"]
            log("port %d is held by PID %s (%s) and is NOT answering"
                % (self.port, self.last["holder"], detail.get("image") or "unknown"))
            if not self.replace:
                log("not touching it (run with --restart to replace a stale dashboard)")
                self.write_report("port held by a non-answering process")
                return False
            if not self.clear_port_holder():
                self.write_report("could not free port %d" % self.port)
                return False
            if not self.note_restart():
                return False
            return self.start()
        if action == guard.RESTART:
            log("the dashboard is holding port %d but not answering — restarting" % self.port)
            self.stop_child()
            if not self.clear_port_holder():
                self.write_report("could not free port %d" % self.port)
                return False
            if not self.note_restart():
                return False
            return self.start()
        # START
        if not self.note_restart():
            return False
        return self.start()

    def run(self) -> int:
        if self.replace:
            self.clear_port_holder()
        started = self.cycle()
        if not started and self.gave_up_reason:
            log(self.gave_up_reason)
            self.write_report(self.gave_up_reason)
            return 3
        if not self.supervise:
            return 0 if guard.http_status(self.url) is not None else 1
        while True:
            time.sleep(POLL_EVERY_S)
            if not self.cycle():
                if self.gave_up_reason:
                    log(self.gave_up_reason)
                    self.write_report(self.gave_up_reason)
                    return 3
                return 0


def status(port: int) -> int:
    """Report the truth about the dashboard: serving, who owns the port, build."""
    url = guard.dashboard_url(port=port)
    holder = guard.port_holder(port)
    detail = guard.port_holder_detail(holder)
    code = guard.http_status(url)
    print("port %d: %s" % (port, "held by PID %s (%s)" % (holder, detail.get("image"))
                           if holder else "free"))
    if code is None:
        print("dashboard: NOT answering on %s" % url)
        if holder and not detail.get("ours"):
            print("  ...and the port belongs to %s, which this app did not start." % detail.get("image"))
        return 1
    health = guard.health_json(guard.health_url(port=port))
    print("dashboard: serving on %s (HTTP %s)" % (url, code))
    print("  build:     %s" % health.get("build", "unknown (old code without a build stamp)"))
    print("  pid:       %s" % health.get("pid", "?"))
    print("  started:   %s" % health.get("started_at", "?"))
    print("  uptime:    %ss" % health.get("uptime_s", "?"))
    running = health.get("running_build")
    if running:
        print("  git rev:   %s" % running)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Keep the Creality dashboard running.")
    ap.add_argument("--port", type=int, default=guard.PORT)
    ap.add_argument("--status", action="store_true", help="report only, change nothing")
    ap.add_argument("--restart", action="store_true",
                    help="replace a stale dashboard holding the port")
    ap.add_argument("--once", action="store_true",
                    help="start if down and verify, then exit")
    ap.add_argument("--no-supervise", action="store_true",
                    help="start if down and leave it running, do not watch it")
    args = ap.parse_args(argv)
    if args.status:
        return status(args.port)
    sup = Supervisor(port=args.port, replace=args.restart,
                     supervise=not (args.once or args.no_supervise))
    return sup.run()


if __name__ == "__main__":
    raise SystemExit(main())
