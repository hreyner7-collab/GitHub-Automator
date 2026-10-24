"""The run must wait for a CONDITION, never for a NUMBER, wherever the page can
be asked whether it is ready yet.

The delay census on job 20261024-131810 found 18.6 s of the measured 25.3 s
fetch and 15.8 s of the measured 40.1 s publish spent sitting in fixed
`wait_for_timeout` calls that nothing was actually waiting for: 8000 ms for a
page to render, 3 x 1200 ms for lazy sections, 3500 ms per source search, 500 ms
before even looking for a crop modal. Those are gone; each kept its old figure
as a CAP and now returns as soon as its own evidence appears.

These tests pin the two primitives that replaced them, and the specific sleeps
that must not come back. An unbounded poll would hang a run forever, so the cap
is asserted too — being fast is worthless if a stuck page stops the queue.
"""
import asyncio
import os
import re
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class FakePage:
    """Records the waits it is asked for; a real clock still runs the poll."""

    def __init__(self):
        self.waited = []

    def wait_for_timeout(self, ms):
        self.waited.append(ms)
        time.sleep(ms / 1000.0)


class FakeAsyncPage(FakePage):
    async def wait_for_timeout(self, ms):
        FakePage.wait_for_timeout(self, ms)


def _src(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()


class TestPollPrimitives:
    def test_poll_returns_the_moment_the_condition_is_true(self):
        import downloader
        page = FakePage()
        calls = {"n": 0}

        def cond():
            calls["n"] += 1
            return calls["n"] >= 3

        t0 = time.time()
        assert downloader._poll(page, 5000, 50, cond) is True
        assert calls["n"] == 3                 # it stopped asking once it was true
        assert time.time() - t0 < 1.0          # and did not spend the 5 s budget
        assert page.waited == [50, 50]         # one wait per unanswered check

    def test_poll_gives_up_at_its_budget_and_returns_the_last_answer(self):
        """A page that never answers must not hang the run."""
        import downloader
        page = FakePage()
        seen = {"n": 0}

        def cond():
            seen["n"] += 1
            return False

        t0 = time.time()
        got = downloader._poll(page, 300, 50, cond)
        assert not got
        assert 0.25 < time.time() - t0 < 1.5   # honoured the cap, roughly
        assert seen["n"] >= 4                  # and really did keep asking

    def test_settle_is_the_same_contract_in_the_async_path(self):
        import uploader
        page = FakeAsyncPage()
        calls = {"n": 0}

        async def cond():
            calls["n"] += 1
            return calls["n"] >= 2 or None

        got = asyncio.run(uploader._settle(page, 5000, 40, cond))
        assert got is True and calls["n"] == 2
        assert page.waited == [40]

        calls["n"] = 0
        async def never():
            return False

        page2 = FakeAsyncPage()
        assert not asyncio.run(uploader._settle(page2, 200, 40, never))
        assert len(page2.waited) >= 3          # the budget was spent, not skipped


class TestTheClockIsGone:
    """The specific fixed sleeps the census named must stay gone."""

    @pytest.mark.parametrize("needle", [
        "page.wait_for_timeout(8000)",                 # render the listing
        "await page.wait_for_timeout(3500)",           # source search per query
        "await page.wait_for_timeout(2500)\n                        last",
        "await page.wait_for_timeout(2000)\n                    after",
        "await page.wait_for_timeout(3000)",           # URL paste
        "await page.wait_for_timeout(5000)\n                g, _ = _grab",
    ])
    def test_the_old_sleep_is_not_back(self, needle):
        assert needle not in (_src("downloader.py") + _src("uploader.py"))

    def test_the_fetch_still_waits_for_the_page_to_render(self):
        """Not deleted — replaced by evidence: the title plus real images, and
        the 8 s grace is kept as the timeout."""
        d = _src("downloader.py")
        assert "document.images.length >= 3" in d
        assert "timeout=8000" in d

    def test_lazy_sections_are_detected_by_their_own_growth(self):
        d = _src("downloader.py")
        assert "(n) => document.images.length > n" in d
        assert re.search(r"for _ in range\(6\):\s*\n\s*page\.evaluate\("
                         r"\"window.scrollTo", d)

    def test_the_publish_polls_its_own_search(self):
        u = _src("uploader.py")
        assert "_settle(page, 3500, 300, _rows_now)" in u
        assert "_settle(page, 2500, 250, _rows_now)" in u
        assert "_settle(page, 2000, 200, _chosen_or_marked)" in u
        assert "_settle(page, 1200, 150, _radio_checked)" in u

    def test_a_crop_modal_is_watched_for_rather_than_slept_through(self):
        """The modal button's own visibility is the evidence; the same 500/700/400
        ms figures survive as timeouts, so a slow modal is still given its grace."""
        u = _src("uploader.py")
        assert 'await _wait_for(btn, "visible", 500)' in u
        assert 'await _wait_for(btn, "hidden", 700)' in u
        assert 'await _wait_for(btn, "visible", 400)' in u

    def test_submit_answer_is_now_noticed_within_400ms_not_1s(self):
        """The ~40 s of grace a slow submit gets is unchanged: 100 x 400 ms."""
        u = _src("uploader.py")
        assert "for _ in range(100):" in u and "await page.wait_for_timeout(400)" in u
        assert "for _ in range(40):\n                        await page.wait_for_timeout(1000)" not in u

    def test_the_module_still_schedules_finite_waiting(self):
        """A guard against the cheap way to pass the gate: deleting the waits
        that are genuinely load-bearing (the settle that proves a gallery is
        stable). Both modules must still contain some polling."""
        for name in ("downloader.py", "uploader.py"):
            assert _src(name).count("wait_for_timeout(") >= 5


class TestPhotosAreFetchedConcurrently:
    def test_the_pool_keeps_the_serial_result_exactly(self, tmp_path, monkeypatch):
        """Same files, same order, same drops — the speed may not cost evidence.
        Run against a server that answers instantly, so this tests equivalence
        and not latency (scripts/check_photo_parallel.py measures the latency)."""
        import http.server
        import threading
        from uploader import __name__ as _u  # noqa: F401  (import graph smoke)
        import downloader

        body = b"\xff\xd8\xff\xe0" + b"0" * 4000

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
        urls = ["http://127.0.0.1:%d/p_%d.jpg" % (port, i) for i in range(5)]
        try:
            monkeypatch.setattr(downloader, "IMAGE_FETCH_WORKERS", 6)
            rep_p = []
            kept_p = downloader._download_images(urls, "s", limit=5,
                                                 strict=False, report=rep_p)
            monkeypatch.setattr(downloader, "IMAGE_FETCH_WORKERS", 1)
            rep_s = []
            kept_s = downloader._download_images(urls, "s", limit=5,
                                                 strict=False, report=rep_s)
        finally:
            srv.shutdown()
        assert kept_p == kept_s
        assert [d["reason"] for d in rep_p] == [d["reason"] for d in rep_s]
        assert len(rep_p) + len(kept_p) == 5      # every photo accounted for once

    def test_index_names_survive_the_pool(self, tmp_path, monkeypatch):
        """Files are named by their position in the listing, so a pool that
        finishes out of order must not rename them."""
        import downloader
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
        monkeypatch.setattr(downloader, "IMAGE_FETCH_WORKERS", 4)
        paths = []

        def fake_save(url, path):
            paths.append(path)
            open(path, "wb").write(b"x" * 2000)
            return path

        monkeypatch.setattr(downloader, "_save", fake_save)
        monkeypatch.setattr(downloader, "_ai_label_reason", lambda p: "")
        monkeypatch.setattr(downloader, "_image_quality_ok",
                            lambda p, **k: True)
        monkeypatch.setattr(downloader, "_flat_render_reason", lambda p: "")
        out = downloader._download_images(["http://x/%d.png" % i for i in range(4)],
                                          "m", limit=4, strict=False, report=[])
        assert [os.path.basename(p) for p in out] == [
            "m_img_0.png", "m_img_1.png", "m_img_2.png", "m_img_3.png"]


def _body(name, header):
    """The source of one module-level function, so a mark cannot be counted from
    somewhere else in the file."""
    src = _src(name)
    rest = src[src.index(header) + len(header):]
    m = re.search(r"\n(?:async )?def \w", rest)
    return rest[:m.start()] if m else rest


class TestTheStepsAreInstrumented:
    """Fast is only a claim if the next run can say where the time went.

    main.py timed the whole fetch and the whole publish; that is enough to
    compare two runs but not to point at a step. Both paths now mark every
    step boundary through `timing.Steps`, so a live run's log carries
    `publish.covers` and `fetch.gallery` next to the totals the census used.
    """

    PUBLISH = ["browser", "files", "form_open", "radio", "attach", "next",
               "name", "covers", "ai_fill", "description", "source",
               "consent", "submit"]
    FETCH = ["connect", "page", "licence", "gallery", "licence", "photos", "file"]

    def _marks(self, src):
        return re.findall(r'_mark\("([a-z_]+)"\)', src)

    def test_every_publish_step_marks_itself_in_order(self):
        body = _body("uploader.py", "async def upload(")
        assert "timing.Steps(\"publish\")" in body
        assert self._marks(body) == self.PUBLISH

    def test_every_fetch_step_marks_itself_in_order(self):
        body = _body("downloader.py", "def _fetch_creality(")
        assert "timing.Steps(\"fetch\")" in body
        assert self._marks(body) == self.FETCH

    def test_both_paths_write_the_one_ledger_the_dashboard_reads(self):
        """Separate copies of timing.py would mean marks that never reach the
        run log — the failure mode worth guarding on a module both import."""
        import downloader
        import timing
        import uploader
        assert uploader.timing is downloader.timing is timing
