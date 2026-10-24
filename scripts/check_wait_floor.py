"""GATE ORACLE (scheduled waits): how much time does a module make the run
sleep for no reason, and did this change reduce it?

Measures the literal delays the code schedules — `page.wait_for_timeout(N)` and
`time.sleep(N)` / `asyncio.sleep(N)` with an integer/float literal — in the
working tree and in a committed version of the same file, and reports the drop.
An `N` that is a variable (a loop that already polls with a computed budget) is
NOT counted: the point is the number the source commits to sleeping.

It measures what the code *schedules*, which is what this change removes. It is
not a wall-clock measurement — check_projection.py turns it into a projection
against the real per-model time from a run log, and only a live run is truth.

  .venv/Scripts/python.exe -X utf8 scripts/check_wait_floor.py downloader.py \
      --against HEAD --reduce 60 --selftest

Exits 0 and prints the marker only when the reduction holds, no function was
deleted to fake it, and --selftest shows this same checker failing on a version
that sleeps MORE than the baseline.
"""
import argparse
import re
import subprocess
import sys

WAIT_RE = re.compile(r"wait_for_timeout\(\s*(\d+)")
SLEEP_RE = re.compile(r"(?:time\.|asyncio\.)sleep\(\s*(\d+(?:\.\d+)?)\s*\)")
DEF_RE = re.compile(r"^(?:async )?def (\w+)", re.M)


def scheduled_ms(src: str) -> int:
    """Every fixed delay the source commits to, in milliseconds."""
    total = sum(int(m) for m in WAIT_RE.findall(src))
    total += int(sum(float(m) for m in SLEEP_RE.findall(src)) * 1000)
    return total


def named_defs(src: str) -> set:
    return set(DEF_RE.findall(src))


def git_show(ref: str, path: str) -> str:
    out = subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if out.returncode:
        raise SystemExit("cannot read %s:%s: %s" % (ref, path,
                                                    out.stderr.decode("utf-8", "replace")))
    return out.stdout.decode("utf-8", "replace")


def compare(base: str, work: str, reduce_pct: float, label: str) -> bool:
    """True when `work` schedules at least `reduce_pct` less sleeping than
    `base`, without quietly deleting functions to do it."""
    b, w = scheduled_ms(base), scheduled_ms(work)
    lost = named_defs(base) - named_defs(work)
    if b <= 0:
        print("%s: baseline schedules no fixed waits — nothing to reduce" % label)
        return False
    drop = (b - w) * 100.0 / b
    print("%s: baseline %d ms scheduled -> now %d ms (%.1f%% less)" % (label, b, w, drop))
    if lost:
        print("  FAIL: %d function(s) disappeared, which can hide work rather "
              "than speed it up: %s" % (len(lost), ", ".join(sorted(lost))[:200]))
        return False
    if drop < reduce_pct:
        print("  FAIL: only %.1f%% less, gate demands %.1f%%" % (drop, reduce_pct))
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--against", default="HEAD")
    ap.add_argument("--reduce", type=float, default=0.0,
                    help="minimum percent reduction required")
    ap.add_argument("--selftest", action="store_true",
                    help="prove this checker can report a regression")
    a = ap.parse_args()

    work = open(a.module, encoding="utf-8").read()
    base = git_show(a.against, a.module)
    ok = compare(base, work, a.reduce, a.module)

    if a.selftest:
        # A known-positive control: hand the checker a version of the SAME file
        # that sleeps 90 s MORE and it must refuse it. A checker that cannot
        # fail proves nothing. The padded text is only ever compared as text —
        # it is never imported or run.
        padded = work + "\npage.wait_for_timeout(90000)\n"
        fooled = compare(work, padded, a.reduce, a.module + " [control: +90s]")
        if fooled:
            print("SELFTEST FAIL: the checker passed a version that sleeps more")
            return 1
        print("  selftest: a version sleeping 90 s more is correctly refused")
        ok = ok and not fooled

    if not ok:
        return 1
    print("wait floor verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
