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

# The supervisor's DECISIONS and the SERVER's output are two different logs, in
# two different files, and that separation is a bug fix rather than tidiness.
# They used to share artifacts/dashboard.log, and the server holds that file open
# as its stdout for as long as it runs: the supervisor's own appends were then
# denied (silently, on the `except` path) while a dashboard was up, so the very
# moment a human went looking for "why did it restart?" was the moment the
# decisions were missing from it.
LOG_PATH = BASE_DIR / "artifacts" / "dashboard_supervisor.log"
CHILD_LOG_PATH = BASE_DIR / "artifacts" / "dashboard.log"
REPORT_PATH = BASE_DIR / "artifacts" / "dashboard_supervisor_report.json"
LOCK_PATH = BASE_DIR / "artifacts" / "dashboard_supervisor.lock"

# How long a freshly started server may take to answer. Generous: the app
# imports playwright and rembg, which are slow on a cold disk.
START_TIMEOUT_S = 60.0
POLL_EVERY_S = 3.0
# Consecutive failed probes before a live-but-silent server is called hung.
HUNG_PROBES = 3
# While an auto run is going, silence is normal: the process is driving a
# browser, waiting on downloads and writing the ledger, and a probe that takes
# more than 3s is a busy machine, not a dead server. Restarting then does not
# just lose the run — it leaves the models half-published, which is the "I asked
# for 20 and it stopped working" the user reported (live 2026-10-24: a 20-model
# publish was killed 90 seconds in, on ONE probe that timed out).
RUN_HUNG_PROBES = 40
# The worker's heartbeat is only evidence of a live run this long after its last
# word; after that it is treated as gone, so a wedged run cannot hide forever.
HEARTBEAT_FRESH_S = 300.0
HEARTBEAT_PATH = BASE_DIR / "artifacts" / "auto_heartbeat.json"
# A stale build must stay stale this long, with the SAME code on disk the whole
# time, before it is reset. Editing the pipeline makes every few seconds a new
# "current" code, and a server that read the files a moment ago always looks
# stale: racing that restarted the dashboard five times in sixty seconds and
# tripped the crash breaker, which left no dashboard and no watcher at all.
STALE_RESET_GRACE_S = 30.0


def acquire_lock(force: bool = False) -> bool:
    """Only one supervisor may run, or two of them fight over the port.

    Two supervisors each see the other's server as "a dashboard I did not start"
    and one of them kills the other's child in a loop. The lock names the PID that
    holds it; a stale lock (its process is gone) is taken over silently, so a crash
    never needs a manual cleanup.
    """
    try:
        if LOCK_PATH.exists():
            try:
                holder = int((LOCK_PATH.read_text(encoding="utf-8") or "0").strip()
                             .split()[0])
            except Exception:
                holder = 0
            # A live PID is not enough: Windows recycles PIDs, so the holder must
            # still look like a supervisor (a python process) before its lock is
            # respected. A phantom-alive lock would silently stop the dashboard
            # from ever being started again.
            if (holder and holder != os.getpid() and guard.process_alive(holder)
                    and guard.process_detail(holder).get("ours") and not force):
                log("another supervisor is already running (PID %d) — leaving it alone"
                    % holder)
                return False
            if holder and force and holder != os.getpid():
                log("taking the lock by force from PID %d" % holder)
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCK_PATH.write_text("%d\n%s\n" % (os.getpid(),
                                            time.strftime("%Y-%m-%d %H:%M:%S")),
                             encoding="utf-8")
        return True
    except Exception as e:
        log("could not take the supervisor lock (%s); continuing anyway" % e)
        return True


def release_lock() -> None:
    try:
        if LOCK_PATH.exists():
            text = LOCK_PATH.read_text(encoding="utf-8") or ""
            if text.strip().split("\n")[0] == str(os.getpid()):
                LOCK_PATH.unlink()
    except Exception:
        pass


def run_heartbeat(now: float | None = None) -> dict | None:
    """The auto run's last word about itself, read from disk, or None.

    A silent dashboard cannot be asked whether it is busy — asking is what just
    timed out — so this is the one source that works while the server is not
    answering. `_auto_worker` rewrites it as it works and names its finished
    state at the end; a heartbeat that is not fresh is worth nothing, so a
    genuinely wedged run is still restarted.
    """
    try:
        hb = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(hb, dict) or hb.get("state") not in ("discovering", "running"):
        return None
    try:
        age = (now if now is not None else time.time()) - float(hb.get("at") or 0)
    except (TypeError, ValueError):
        return None
    return hb if 0 <= age <= HEARTBEAT_FRESH_S else None


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
        # The PID that /health reports, i.e. the process ACTUALLY serving. On a
        # venv where python.exe re-execs, this is the real server while
        # child_pid is only its wrapper -- so both are tracked and both are
        # stopped. Trusting child_pid alone orphaned a live server on the port.
        self.server_pid: int | None = None
        self.restart_times: list[float] = []
        self.silent_probes = 0
        self.gave_up_reason: str | None = None
        # (when we first saw it, the code on disk we saw it against): a stale
        # build only earns a reset once it has stayed stale, unchanged, for
        # STALE_RESET_GRACE_S. See STALE_RESET_GRACE_S for why.
        self.stale_since: float | None = None
        self.stale_seen: str | None = None

    # ── probes ───────────────────────────────────────────────────────────────
    def probe(self) -> str:
        status = guard.http_status(self.url)
        holder = guard.port_holder(self.port)
        alive = (guard.process_alive(self.child_pid)
                 or guard.process_alive(self.server_pid))
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
        CHILD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            logfh = CHILD_LOG_PATH.open("a", encoding="utf-8", buffering=1)
        except Exception as e:
            log("cannot open the server log (%s); starting anyway" % e)
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
                # Adopt the serving PID: that, not the wrapper, is what has to be
                # stopped when this child is replaced.
                self.server_pid = health.get("pid") or None
                log("answering on %s (HTTP %s) build=%s pid=%s uptime=%ss"
                    % (self.url, status, health.get("build", "?"),
                       self.server_pid or "?", health.get("uptime_s", "?")))
                return True
            if not guard.process_alive(self.child_pid):
                log("the child exited before it answered (see the log above this line)")
                return False
            time.sleep(1.0)
        log("no answer within %.0fs — treating the start as failed" % START_TIMEOUT_S)
        self.stop_child()
        return False

    def stop_child(self) -> None:
        """Stop the dashboard this supervisor started -- both processes of it.

        The wrapper PID from Popen AND the PID that /health reported are stopped:
        on this venv they are different processes, and stopping only the wrapper
        leaves the real server alive on the port.
        """
        pids = [p for p in (self.server_pid, self.child_pid) if p]
        for pid in pids:
            if guard.stop_process(pid):
                log("stopped PID %d" % pid)
            elif guard.process_alive(pid):
                log("could NOT stop PID %d (not ours, or still running)" % pid)
        self.child_pid = None
        self.server_pid = None

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
        guard.stop_process(holder)
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
    def auto_reset(self) -> bool:
        """Restart a stale dashboard on its own, unless a run is in flight.

        The user's standing request: an update on disk is served without being
        asked for, so "code changed on disk — RESTART NEEDED" should not sit
        there waiting for a human. The one time a restart must NOT happen is
        mid-run: killing the dashboard leaves a half-published model behind, so
        when /auto/status reads busy, the restart is deferred to a later cycle
        and the stale warning stays up until the run settles.

        Staleness is also given STALE_RESET_GRACE_S to settle: a dashboard whose
        files are being edited is *always* stale against the process that read
        them seconds ago, and resetting on every one of those reads restarts the
        dashboard as fast as the edits land.
        """
        health = guard.health_json(guard.health_url(port=self.port))
        if not guard.is_stale_build(health):
            self.stale_since = self.stale_seen = None
            return True
        # what the running server says the disk holds. An older dashboard does
        # not report a fingerprint, so its build stamp is the best key there is.
        disk = str(health.get("disk_fingerprint") or health.get("build") or "")
        now = time.time()
        if self.stale_seen != disk or self.stale_since is None:
            self.stale_seen = disk
            self.stale_since = now
            log("build %s is behind the code on disk (%s) — watching it for %.0fs "
                "before resetting, in case the files are still being edited"
                % (health.get("build", "?"), disk or "?", STALE_RESET_GRACE_S))
            return True
        if now - self.stale_since < STALE_RESET_GRACE_S:
            return True
        auto = guard.health_json(guard.auto_status_url(port=self.port))
        if guard.run_in_progress(auto):
            log("build %s is stale but an auto run is in progress — "
                "the reset waits until it finishes" % health.get("build", "?"))
            return True
        log("code changed on disk since build %s (pid %s) — auto-resetting"
            % (health.get("build", "?"), health.get("pid", "?")))
        self.stale_since = self.stale_seen = None
        self.stop_child()
        if not self.clear_port_holder():
            self.write_report("auto-reset could not free port %d" % self.port)
            return False
        if not self.note_restart():
            return False
        return self.start()

    def cycle(self) -> bool:
        """One pass. Returns False when the supervisor should stop."""
        action = self.probe()
        if action == guard.HEALTHY:
            self.silent_probes = 0
            return self.auto_reset()
        if action == guard.ADOPT:
            # Something answers but it is not our child: a dashboard that was
            # already running (at logon, or started by hand). Starting a second
            # copy would die on the port and leave you serving old code, and
            # simply EXITING would leave a live server with nobody watching it.
            # So adopt it: watch the process that /health names, and heal it like
            # any other if it dies or hangs.
            health = guard.health_json(guard.health_url(port=self.port))
            pid = health.get("pid") or self.last.get("holder")
            if pid and pid != self.server_pid:
                self.server_pid = pid
                log("adopting the dashboard already serving %s "
                    "(build=%s pid=%s) — watching it, not starting a second copy"
                    % (self.url, health.get("build", "?"), pid))
            self.silent_probes = 0
            # A fresh supervisor has no child of its own, so this branch is its
            # steady state: the stale check must live here too, not only under
            # HEALTHY, or an adopted dashboard never auto-resets.
            return self.auto_reset()
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
            self.silent_probes += 1
            hb = run_heartbeat()
            need = RUN_HUNG_PROBES if hb else HUNG_PROBES
            if self.silent_probes < need:
                why = (" — an auto run is in progress (job %s, %s published) and "
                       "restarting now would leave its models half-published"
                       % (hb.get("job_id"), hb.get("published"))) if hb else ""
                log("the dashboard did not answer (%d of %d probes)%s — waiting: "
                    "a timed-out probe is a busy machine, not a dead server"
                    % (self.silent_probes, need, why))
                return True
            log("the dashboard is holding port %d and has not answered %d probes "
                "in a row — restarting" % (self.port, self.silent_probes))
            self.silent_probes = 0
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
        try:
            return self._run()
        finally:
            release_lock()

    def _run(self) -> int:
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
    ap.add_argument("--force", action="store_true",
                    help="take the supervisor lock from a running supervisor")
    args = ap.parse_args(argv)
    if args.status:
        return status(args.port)
    if not acquire_lock(force=args.force):
        return 4
    sup = Supervisor(port=args.port, replace=args.restart,
                     supervise=not (args.once or args.no_supervise))
    return sup.run()


if __name__ == "__main__":
    raise SystemExit(main())
