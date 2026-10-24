"""Per-phase timings, so "it is faster now" is a number and not a claim.

WHY: until this existed the pipeline recorded no timings at all, which meant a
speedup could not be measured, argued about, or regressed. Every phase now goes
through `phase()`, and each run writes its own table into the run log.

Two ledgers are kept:

  * the RUN ledger, cleared at the start of every run (timing.reset()), so a run
    log says where THAT run's time went;
  * the PROCESS ledger, which accumulates for the life of the server, so the
    dashboard can answer "what is slow in general" without starting a run.

Phases nest, so the totals are "time spent inside this phase", not a partition of
wall-clock. `run` is the one phase that does cover the whole run; the dashboard
compares the others against it rather than summing them.

Callers here can be on the event loop or on a worker thread (the fetch and the
image edit run in threads), so every write takes a lock.
"""
from __future__ import annotations

import contextlib
import threading
import time
from collections import OrderedDict
from typing import Iterator

_LOCK = threading.RLock()
_RUN: "OrderedDict[str, dict]" = OrderedDict()
_PROCESS: "OrderedDict[str, dict]" = OrderedDict()
_run_started: float | None = None
_process_started = time.time()


def _bump(store: "OrderedDict[str, dict]", name: str, seconds: float) -> None:
    row = store.setdefault(name, {"count": 0, "total_s": 0.0, "last_s": 0.0, "max_s": 0.0})
    row["count"] += 1
    row["total_s"] = round(row["total_s"] + seconds, 3)
    row["last_s"] = round(seconds, 3)
    row["max_s"] = round(max(row["max_s"], seconds), 3)


def record(name: str, seconds: float) -> None:
    """Record one completed phase, in both ledgers."""
    seconds = max(0.0, float(seconds))
    with _LOCK:
        _bump(_RUN, name, seconds)
        _bump(_PROCESS, name, seconds)


class Steps:
    """Sequential marks inside one long function: `m = timing.Steps("publish")`,
    then `m("covers")` at each step boundary.

    Each mark times the stretch since the previous one, so the marks add up to
    the function instead of nesting inside each other — which is what makes a
    40 s publish answerable with "the covers took 22 s of it". Names come out
    prefixed and dotted, so the dashboard shows them under the phase that
    already wraps the call.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._t = time.perf_counter()

    def __call__(self, label: str) -> None:
        now = time.perf_counter()
        record("%s.%s" % (self.prefix, label), now - self._t)
        self._t = now


@contextlib.contextmanager
def phase(name: str) -> Iterator[None]:
    """`with timing.phase("fetch"): ...` — times the block even if it raises.

    The timing is recorded in a `finally`, so a phase that fails still shows up:
    a phase that fails slowly is exactly what needs to be visible.
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - t0)


def reset() -> None:
    """Start a fresh run's accounting. Process totals keep accumulating."""
    global _run_started
    with _LOCK:
        _RUN.clear()
        _run_started = time.time()


def _rows(store: "OrderedDict[str, dict]") -> list[dict]:
    rows = [{"phase": name, "count": r["count"],
             "total_s": r["total_s"], "last_s": r["last_s"], "max_s": r["max_s"],
             "avg_s": round(r["total_s"] / r["count"], 3) if r["count"] else 0.0}
            for name, r in store.items()]
    # slowest first: the point of the table is to show where the time went
    return sorted(rows, key=lambda r: (-r["total_s"], r["phase"]))


def snapshot() -> dict:
    """This run's table. Empty (but well-formed) before a run starts."""
    with _LOCK:
        rows = _rows(_RUN)
        started = _run_started
    run_row = next((r for r in rows if r["phase"] == "run"), None)
    return {
        "phases": rows,
        "run_total_s": run_row["total_s"] if run_row else None,
        "started": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))
                    if started else None),
    }


def process_snapshot() -> dict:
    """Everything timed since the server started."""
    with _LOCK:
        rows = _rows(_PROCESS)
    return {"phases": rows,
            "uptime_s": round(time.time() - _process_started, 1)}


def summary_line(limit: int = 4) -> str:
    """One line for a log or a status panel: the slowest phases first."""
    with _LOCK:
        rows = _rows(_RUN)[:limit]
    if not rows:
        return "nothing timed yet"
    # two decimals: a phase that costs 20ms must not be printed as "0.0s", which
    # reads like it did nothing
    return " · ".join("%s %.2fs" % (r["phase"], r["total_s"]) for r in rows)
