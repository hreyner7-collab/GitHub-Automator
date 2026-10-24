"""The model-file step works out of Creality's file dialog.

The dialog is the step that decided whether a model was published at all, and it
broke silently: its per-file download controls are icons with NO text
(<i class="iconfont icon-download">), while every finder the step had matches on
an element's TEXT — so it reported "no usable model file came back from the
page" for models whose files were on the page the whole time (live 2026-10-24 on
/model-detail/ender-3-cable-chain, and the same shape as the 2026-10-23 run that
published 0 of 20).

These tests cover both halves: MODEL_FILES_JS against a DOM that mirrors the
real dialog (headless, a local string, no page and no account — the same
fixture style test_reupload.py already uses for FIND_JS), and the Python that
decides WHICH row to click and whether to keep what came back.
"""
import os

import pytest

import downloader


MODEL_URL = "https://www.crealitycloud.com/model-detail/ender-3-cable-chain"

# Mirrors what the live dialog actually holds (probe 2026-10-24): a folder header
# with a download icon, one row per file whose only control is a text-less icon,
# and a footer button that downloads everything.
DIALOG_HTML = """<html><body>
<div class="el-dialog" style="position:absolute;top:20px;left:40px;width:600px;height:500px">
  <div class="model-download-wrap">
    <div class="folder-box default-file-folder">
      <div class="folder-header" style="height:40px">
        <div class="folder-operate"><div class="iconfont icon-download"
             style="display:inline-block;width:18px;height:18px"></div></div>
      </div>
      <div class="folder-files" style="height:300px">
        <div class="model-item" style="height:60px">
          <div class="model-info"><div class="model-name">
            <span class="name">cableChain_linkCover_v2.stl</span>
            <span class="size">5.94 KB</span></div></div>
          <div class="model-time"><span class="time">2026-01-04</span>
            <span><i class="iconfont icon-slice" style="display:inline-block;width:16px;height:16px"></i>
            <i class="iconfont icon-download" style="display:inline-block;width:16px;height:16px"></i></span>
          </div>
        </div>
        <div class="model-item" style="height:60px">
          <div class="model-info"><div class="model-name">
            <span class="name">cableChain_linkCover_v2.3mf</span>
            <span class="size">2.4 MB</span></div></div>
          <div class="model-time"><span class="time">2026-01-04</span>
            <span><i class="iconfont icon-download" style="display:inline-block;width:16px;height:16px"></i></span>
          </div>
        </div>
        <div class="model-item" style="height:60px;visibility:hidden">
          <div class="model-info"><div class="model-name">
            <span class="name">template that was never mounted</span>
            <span class="size">1 KB</span></div></div>
        </div>
      </div>
    </div>
    <div class="dialog-footer" style="height:50px">
      <button class="el-button el-button--primary" style="width:120px;height:34px">Download All</button>
    </div>
  </div>
</div></body></html>"""


@pytest.fixture(scope="module")
def dialog_page():
    """The fixture DOM in a throwaway headless browser: never the user's Chrome,
    never a page, never the account."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_viewport_size({"width": 800, "height": 600})
        page.set_content(DIALOG_HTML)
        yield page
        b.close()


def test_the_dialog_is_read_by_structure_even_though_its_icons_have_no_text(
        dialog_page):
    got = dialog_page.evaluate(downloader.MODEL_FILES_JS, {})
    rows = got["rows"]
    assert len(rows) == 2, rows            # the unmounted template row is not a file
    names = [r["name"] for r in rows]
    assert names == ["cableChain_linkCover_v2.stl", "cableChain_linkCover_v2.3mf"]
    assert rows[0]["bytes"] == 6083, rows          # 5.94 KB, in bytes
    assert rows[1]["bytes"] == 2516582, rows       # 2.4 MB, and the bigger file
    assert all(r["icon"] for r in rows), "each row's own download icon was lost"
    assert got["all"], "the dialog's download-everything control was not found"


def test_the_word_finders_cannot_see_a_rows_own_download_control(dialog_page):
    """The negative control: the step this replaces matched on text, and the
    icon has none. 'Download 3MF' never appears, and the only text the old finder
    could lock onto is a FILE NAME — clicking it starts nothing."""
    by_verb = dialog_page.evaluate(downloader.FIND_DIALOG_JS,
                                   downloader.DOWNLOAD_3MF_RE)
    assert by_verb is None, by_verb
    by_thing = dialog_page.evaluate(downloader.FIND_DIALOG_JS, r"\b3MF\b")
    assert not by_thing or "3mf" in by_thing["t"], by_thing


def test_locating_a_row_gives_a_point_the_mouse_can_actually_reach(dialog_page):
    hit = dialog_page.evaluate(downloader.MODEL_FILES_JS, {"index": 1})
    assert hit.get("hit") is True, hit
    assert hit["inView"] is True, hit
    gone = dialog_page.evaluate(downloader.MODEL_FILES_JS, {"index": 9})
    assert "gone" in gone.get("error", ""), gone


def test_force_clicks_the_folder_control_through_the_dom(dialog_page):
    """The row list scrolls inside the dialog, so a row below the fold has no
    point to aim at; clicking the element itself is still a real click."""
    out = dialog_page.evaluate(downloader.MODEL_FILES_JS,
                               {"all": True, "force": True})
    assert out.get("clicked") is True, out


class FakeDownload:
    def __init__(self, name, blob=b"solid"):
        self.suggested_filename = name
        self._blob = blob

    def save_as(self, path):
        with open(path, "wb") as f:
            f.write(self._blob)


class FakeDialog:
    """Answers MODEL_FILES_JS the way the page does. `yields` maps a clicked
    control to the files it answers with; a control that is absent from it
    downloads nothing. A download starts on the CLICK, never on the read, so no
    test can pass by only looking at the page."""

    def __init__(self, rows, all_ctl=None, yields=None, no_dialog=False):
        self.rows = rows
        self.all_ctl = all_ctl
        self.yields = yields or {}
        self.no_dialog = no_dialog
        self.downloads = []
        self.clicks = []
        self.waits = 0
        self.pending = None
        self.mouse = self

    def click(self, x, y):
        self.clicks.append(("mouse", x, y))
        if self.pending is not None:
            self._start(self.pending)
            self.pending = None

    def _dom_click(self, key):
        self.clicks.append(("dom", key))
        self._start(key)

    def evaluate(self, js, arg=None):
        assert js is downloader.MODEL_FILES_JS, "the step must read the dialog"
        if self.no_dialog:
            return {"error": "no file dialog is open"}
        q = arg or {}
        if "index" in q:
            row = self.rows[q["index"]]
            key = str(q["index"])
            if not row.get("icon"):
                return {"error": "no control to click: " + row["name"]}
            if q.get("force"):
                self._dom_click(key)
                return {"clicked": True, "name": row["name"]}
            self.clicks.append(("probe", key))
            if not row.get("reachable", True):
                return {"x": 0, "y": 9999, "name": row["name"], "hit": False,
                        "inView": True}
            self.pending = key
            return {"x": row["icon"]["x"], "y": row["icon"]["y"], "name": row["name"],
                    "hit": True, "inView": True}
        if q.get("all"):
            if not self.all_ctl:
                return {"error": "no control to click: download all"}
            if q.get("force"):
                self._dom_click("all")
                return {"clicked": True, "name": "Download All"}
            self.pending = "all"
            return dict(self.all_ctl, name="Download All", hit=True, inView=True)
        return {"rows": [dict(r, i=n) for n, r in enumerate(self.rows)],
                "all": self.all_ctl}

    def _start(self, key):
        for name in self.yields.get(key, []):
            self.downloads.append(FakeDownload(name))

    def wait_for_timeout(self, ms):
        self.waits += 1


def _rows(*specs):
    return [{"name": n, "size": s, "bytes": b, "icon": {"x": 100 + i, "y": 200}}
            for i, (n, s, b) in enumerate(specs)]


def test_the_biggest_file_is_clicked_first_not_the_first_row(tmp_path, monkeypatch):
    """The listing keeps a 5 KB preview stub beside the 2 MB print file, and a
    DOM-order click fetches the stub."""
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
    rows = _rows(("stub.stl", "5.94 KB", 6083), ("part.3mf", "2.4 MB", 2516582))
    page = FakeDialog(rows, yields={"0": ["stub.stl"], "1": ["part.3mf"]})
    seen = []

    def verify(files, url):
        seen.append(os.path.basename(files[0]))
        return os.path.basename(files[0]) == "part.3mf"

    got = downloader._files_from_dialog(page, page.downloads, "part", MODEL_URL,
                                        {}, verify, patience=2)
    assert got == str(tmp_path / "part.3mf"), got
    assert seen == ["part.3mf"], seen
    assert ("mouse", 101, 200) in page.clicks, page.clicks   # row 1's own icon
    assert ("mouse", 100, 200) not in page.clicks, page.clicks  # never the stub


def test_a_stub_that_is_not_geometry_is_never_handed_to_the_uploader(
        tmp_path, monkeypatch):
    """Every saved file has to verify before it is returned; what was refused is
    named in the report, so the run says WHICH file was junk."""
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
    rows = _rows(("stub.stl", "5.94 KB", 6083), ("other.stl", "4 KB", 4096))
    page = FakeDialog(rows, yields={"0": ["stub.stl"], "1": ["other.stl"]})
    report: dict = {}
    got = downloader._files_from_dialog(page, page.downloads, "m", MODEL_URL,
                                        report, lambda f, u: False, patience=2)
    assert got is None
    assert report["rejected"] == ["stub.stl", "other.stl"], report
    assert [f["name"] for f in report["files"]] == ["stub.stl", "other.stl"]


def test_a_row_below_the_fold_is_clicked_through_the_dom(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
    rows = _rows(("far.3mf", "1 MB", 1048576),)
    rows[0]["reachable"] = False
    page = FakeDialog(rows, yields={"0": ["far.3mf"]})
    got = downloader._files_from_dialog(page, page.downloads, "m", MODEL_URL, {},
                                        lambda f, u: True, patience=2)
    assert got == str(tmp_path / "far.3mf"), got
    assert ("probe", "0") in page.clicks and ("dom", "0") in page.clicks


def test_the_dialogs_own_download_all_is_the_back_stop(tmp_path, monkeypatch):
    """A row whose icon the page cannot reach downloads nothing; the folder
    control still gets its turn, and 'download all' answers with several files —
    the verified one is kept, the rest are not."""
    monkeypatch.setattr(downloader, "DOWNLOADS_DIR", str(tmp_path))
    rows = _rows(("readme.txt", "1 KB", 1024))
    page = FakeDialog(rows, all_ctl={"x": 50, "y": 55},
                      yields={"0": [], "all": ["readme.txt", "real.3mf"]})
    report: dict = {}
    good = []

    def verify(files, url):
        (good.append(os.path.basename(files[0]))
         if os.path.basename(files[0]) == "real.3mf" else None)
        return os.path.basename(files[0]) == "real.3mf"

    got = downloader._files_from_dialog(page, page.downloads, "m", MODEL_URL,
                                        report, verify, patience=2)
    assert got == str(tmp_path / "real.3mf"), got
    assert report["tried_files"][-1]["name"] == "Download All", report
    assert good == ["real.3mf"]


def test_no_dialog_is_reported_and_the_word_path_keeps_its_turn():
    """The other layout answers with a menu ITEM that does carry text; the
    structural read must give up at once — no rows, no waiting — so the caller
    sees no "files" and falls through to the wording finder."""
    page = FakeDialog([], no_dialog=True)
    report: dict = {}
    got = downloader._files_from_dialog(page, page.downloads, "m", MODEL_URL,
                                        report, lambda f, u: True, patience=2)
    assert got is None
    assert "files" not in report, report
    assert "no file dialog" in report["dialog"], report
    assert page.waits == 0, "it waited for a dialog that is not there"


def test_a_dialog_that_downloads_nothing_leaves_evidence_behind():
    page = FakeDialog(_rows(("a.3mf", "1 MB", 1048576)), yields={})
    report: dict = {}
    got = downloader._files_from_dialog(page, page.downloads, "m", MODEL_URL,
                                        report, lambda f, u: True, patience=3)
    assert got is None
    assert [f["name"] for f in report["files"]] == ["a.3mf"]
    assert report["tried_files"][0]["how"] == "mouse", report


@pytest.mark.parametrize("fa, want", [
    ({"control": "View STL/CAD Files", "files": [{"name": "a.3mf", "size": "2 MB"}]},
     "listed 'a.3mf' (2 MB)"),
    ({"control": "View STL/CAD Files", "rejected": ["stub.stl"]},
     "none of it is real model geometry"),
])
def test_a_skipped_model_names_the_files_the_dialog_offered(fa, want):
    import main
    assert want in main._file_attempt_note(fa), main._file_attempt_note(fa)
