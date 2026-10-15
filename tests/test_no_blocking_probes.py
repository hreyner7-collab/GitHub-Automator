"""A static guard: no BLOCKING browser probe may be called from async code.

The defect this pins, found in the audit and fixed in eight places: the port
probe (2s timeout) and the Chrome launcher (up to 20s of `time.sleep` while it
waits for the debug port) are SYNCHRONOUS, and they were being awaited directly
inside `async def` functions. Every one of those calls froze the dashboard -- all
requests, not just the one being served -- for as long as it took.

The failure is invisible in review and invisible in a passing test run, so it is
caught by reading the code's own structure instead: if a call to one of these
helpers appears inside an async function, the test fails and names the line.
"""
import ast
import pathlib

import pytest

# Helpers that block their caller. Everything here must run in a worker thread
# (asyncio.to_thread) when it is reached from async code.
BLOCKING_HELPERS = {"_debug_port_up", "ensure_browser", "ensure_edge", "time.sleep"}

FILES = ["main.py", "uploader.py", "analytics.py", "downloader.py", "discover.py",
         "editor.py", "ledger.py"]


def _called_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # time.sleep -> "time.sleep"; keep the attribute for the sweep below
        base = func.value.id if isinstance(func.value, ast.Name) else ""
        return ("%s.%s" % (base, func.attr)) if base else func.attr
    return ""


def _blocking_calls_inside_async(tree: ast.AST) -> list[tuple[str, int]]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and _called_name(sub) in BLOCKING_HELPERS:
                # passing the helper BY NAME to asyncio.to_thread is the correct
                # form and is not a Call, so it is never flagged here
                found.append((node.name, sub.lineno))
    return found


@pytest.mark.parametrize("path", FILES)
def test_no_blocking_probe_is_called_from_async_code(path):
    src = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = _blocking_calls_inside_async(tree)
    # sleep() inside async is handled separately below: it is legal only when
    # reached through asyncio.sleep, i.e. it never reads as `time.sleep`.
    offenders = [(fn, ln) for fn, ln in offenders
                 if not (fn and _line_is_asyncio_sleep(src, ln))]
    assert not offenders, (
        "%s calls a blocking helper inside an async function: %s\n"
        "Wrap it: await asyncio.to_thread(helper). Otherwise the whole dashboard "
        "freezes for the duration." % (path, offenders))


def _line_is_asyncio_sleep(src: str, lineno: int) -> bool:
    line = src.splitlines()[lineno - 1]
    return "asyncio.sleep" in line


def test_the_guard_actually_catches_a_blocking_call():
    """A guard that cannot fail proves nothing -- feed it the defect itself."""
    broken = ast.parse(
        "import asyncio\n"
        "async def f():\n"
        "    if not _debug_port_up():\n"
        "        ensure_browser()\n")
    offenders = _blocking_calls_inside_async(broken)
    assert ("f", 3) in offenders and ("f", 4) in offenders


def test_the_correct_form_is_accepted():
    good = ast.parse(
        "import asyncio\n"
        "async def f():\n"
        "    if not await asyncio.to_thread(_debug_port_up):\n"
        "        await asyncio.to_thread(ensure_browser)\n")
    assert _blocking_calls_inside_async(good) == []
