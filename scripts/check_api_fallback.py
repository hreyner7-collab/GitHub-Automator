"""Prove the API read falls back (or refuses) when the site will not answer.

Offline on purpose: no browser, no network, no account. Every scenario below
stubs the tab and drives the REAL `creality_api.Reader.read` plus the REAL
`downloader._fetch_creality_api` decision, so the gate is on the code that ships,
not on a copy of its rules.

Exit codes:
  0  every scenario landed where it must (fall back, or refuse)
  1  a gate failed — the API path invented, or a refusal was downgraded
  2  `--selftest` did not manage to make the gate fail (the gate is asleep)

`--selftest` swaps in a reader that lies (fabricates a completed record after a
403). The checker MUST then report failure; if it still says OK, the checker is
proving nothing and exits 2.
"""
from __future__ import annotations

import contextlib
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_opener                      # noqa: E402
import creality_api as api                 # noqa: E402
import downloader                          # noqa: E402
import uploader                            # noqa: E402

URL = "https://www.crealitycloud.com/model-detail/modular-mobile-organizer"

NODE = {
    "nodeId": "68b9fb618d1f79abe1da2e80",
    "groupName": "Modular Phone Organizer",
    "nickName": "Litolunar",
    "license": "CC BY",
    "desc": "A phone organizer that holds three handsets.",
    "covers": ["https://pic2-cdn.creality.com/pic/a.png",
               "https://pic2-cdn.creality.com/pic/b.png"],
}


def _payload(**overrides):
    node = dict(NODE)
    node.update(overrides.pop("node", {}) or {})
    out = {"missing": False, "nodes": 240, "h1": node.get("groupName", ""),
           "title": node.get("groupName", ""), "dead": False, "error": "",
           "model": node}
    out.update(overrides)
    return out


class StubReader(api.Reader):
    """A Reader whose tab is a dict. Only the two I/O seams are replaced."""

    def __init__(self, payload=None, mode="refuse", status=403):
        self._payload = payload
        self._mode = mode
        self._status = status
        self._hdr: dict = {}
        self._last = 0.0
        self.calls: list[str] = []

    def open_model(self, url: str) -> dict:
        if self._payload is not None and self._payload.get("_signin"):
            return {"dead": False, "signin": True, "unknown": ["signin"],
                    "load_ms": 1}
        rec = api.parse_payload(self._payload or _payload())
        rec["load_ms"] = 1
        return rec

    def post(self, path: str, body: dict, token: bool = False):
        self.calls.append(path)
        if self._mode == "empty":
            return {"ok": True, "status": 200, "json": {}, "text": ""}
        if self._mode == "refuse":
            return {"ok": False, "status": self._status, "text": "challenge"}
        raise AssertionError("scenario gave no answer for %s" % path)


class GuessingReader(StubReader):
    """DELIBERATELY WRONG: invents the fields a 403 did not provide."""

    def read(self, url: str, dest_dir: str | None = None) -> dict:
        rec = super().read(url, dest_dir=dest_dir)
        rec["stl_path"] = os.path.join(os.path.dirname(__file__), "guessed.3mf")
        rec["covers"] = ["https://pic2-cdn.creality.com/pic/fake.png"]
        rec["unknown"] = []
        return rec


@contextlib.contextmanager
def _stubbed_tab(reader):
    class FakePW:
        class chromium:
            @staticmethod
            def connect_over_cdp(_url):
                return types.SimpleNamespace(contexts=[object()])

    @contextlib.contextmanager
    def fake_pw():
        yield FakePW()

    saved = (api.get_reader, uploader.ensure_browser,
             browser_opener.sync_playwright_auto,
             downloader._download_images, uploader._verify_model_files)
    api.get_reader = lambda ctx=None: reader
    uploader.ensure_browser = lambda: True
    browser_opener.sync_playwright_auto = fake_pw
    downloader._download_images = lambda urls, slug, **k: list(urls)
    uploader._verify_model_files = lambda paths, url: True
    try:
        yield
    finally:
        (api.get_reader, uploader.ensure_browser,
         browser_opener.sync_playwright_auto,
         downloader._download_images, uploader._verify_model_files) = saved


def _run(reader) -> tuple[object, str]:
    """`_fetch_creality_api` against the stubbed tab: (result, outcome)."""
    try:
        with _stubbed_tab(reader):
            return downloader._fetch_creality_api(URL), "record"
    except RuntimeError as e:
        return str(e), "refused"


SCENARIOS = [
    # (name, reader, expected outcome)
    ("403 from every endpoint", StubReader(_payload(), "refuse", 403), "record:none"),
    ("429 from every endpoint", StubReader(_payload(), "refuse", 429), "record:none"),
    ("empty JSON body", StubReader(_payload(), "empty"), "record:none"),
    ("covers key renamed away",
     StubReader(_payload(node={"covers": [], "editCovers": []})), "record:none"),
    ("no licence on the node",
     StubReader(_payload(node={"license": ""})), "record:none"),
    ("licence by-nc-nd",
     StubReader(_payload(node={"license": "CC BY-NC-ND"})), "refused"),
    ("signed out", StubReader({"_signin": True}), "refused"),
    ("dead listing", StubReader(_payload(dead=True,
                                         h1="Page Not Found")), "refused"),
]


def check(scenarios) -> list[str]:
    failures = []
    for name, reader, want in scenarios:
        got, outcome = _run(reader)
        if want == "record:none":
            if outcome != "record" or got is not None:
                failures.append("%s: expected no record, got %r" % (name, got))
        elif outcome != "refused":
            failures.append("%s: expected a refusal, got %r" % (name, got))
    return failures


def check_wrapper() -> list[str]:
    """The API-first wrapper: None -> DOM, a refusal -> no fallback."""
    out = []
    saved_api, saved_dom = downloader._fetch_creality_api, downloader._fetch_creality_dom
    called = {"dom": 0}

    def dom(_url):
        called["dom"] += 1
        return {"read_via": "dom", "name": "DOM"}

    try:
        downloader._fetch_creality_api = lambda _url: None
        downloader._fetch_creality_dom = dom
        got = downloader._fetch_creality(URL)
        if called["dom"] != 1 or got.get("read_via") != "dom":
            out.append("an incomplete API answer did not fall back to the DOM pass")

        called["dom"] = 0

        def refuse(_url):
            raise RuntimeError("Licence refused")

        downloader._fetch_creality_api = refuse
        try:
            downloader._fetch_creality(URL)
            out.append("a licence refusal was swallowed into the DOM fallback")
        except RuntimeError:
            pass
        if called["dom"]:
            out.append("the DOM pass ran after a refusal")
    finally:
        downloader._fetch_creality_api = saved_api
        downloader._fetch_creality_dom = saved_dom
    return out


def main() -> int:
    selftest = "--selftest" in sys.argv
    if selftest:
        # the 403 now feeds a reader that fabricates the missing fields: the
        # checker has to notice that a record came back
        scenarios = [("403 with a lying reader",
                      GuessingReader(_payload(), "refuse", 403), "record:none")]
        failures = check(scenarios)
        if failures:
            print("SELFTEST OK — the gate can fail; it reported: %s"
                  % failures[0].split(":")[0])
            return 0
        print("SELFTEST FAILED — a fabricated record was accepted as a real read")
        return 2

    failures = check(SCENARIOS) + check_wrapper()
    for f in failures:
        print("FAIL  " + f)
    if failures:
        return 1
    print("API FALLBACK OK — %d scenarios fell back or refused, wrapper routes "
          "DOM only on an incomplete answer" % len(SCENARIOS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
