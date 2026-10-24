"""Two failure classes that ended a publish before it began.

Attach: Playwright streams a set_input_files payload and refuses anything over
50 MB to a CDP-connected browser, so a 73.55 MB part was rejected by the
transport ("Cannot transfer files larger than 50Mb to a browser not co-located
with the server", live 2026-10-24 on /model-detail/cable-rapper-organizer) even
though Creality takes that file by hand. The path-based CDP attach has no cap,
and the DOM walk that finds the input is what this covers.

Covers: a model skipped for "0 photo(s), 0 usable" said nothing about whether
the listing has no pictures or the reader could not see them — which is why
three such skips in one run could not be diagnosed from the log at all.
"""
import os

import pytest

import main
import uploader


def _input(node_id, accept, extra=None):
    attrs = ["type", "file", "accept", accept]
    if extra:
        attrs += extra
    n = {"nodeId": node_id, "nodeName": "INPUT", "attributes": attrs}
    return n


CDP_TREE = {
    "nodeId": 1, "nodeName": "#document", "children": [
        {"nodeId": 2, "nodeName": "DIV", "children": [
            # the images zone of the form — must never be the model input
            _input(3, ".jpg,.png,.webp"),
            {"nodeId": 4, "nodeName": "DIV", "children": [
                _input(5, ".step,.stp,.stl,.3mf,.obj")]},
        ]},
        # a form inside the legacy same-origin iframe arrives under
        # pierceChildren, not children
        {"nodeId": 6, "nodeName": "IFRAME", "pierceChildren": [
            {"nodeId": 7, "nodeName": "#document", "children": [
                _input(9, ".3mf,.stl")]},
        ]},
    ],
}


class TestAttachRoute:
    def test_under_the_cap_playwright_keeps_the_familiar_path(self, tmp_path):
        p = tmp_path / "m.3mf"
        p.write_bytes(b"x" * 1024)
        assert uploader.attach_needs_cdp([str(p)]) is False

    def test_over_the_cap_the_payload_goes_by_path(self, tmp_path):
        p = tmp_path / "big.3mf"
        p.write_bytes(b"")
        with open(p, "wb") as fh:                # sparse: a real 60 MB without
            fh.truncate(60 * 1024 * 1024)        # filling the temp directory
        assert uploader.attach_needs_cdp([str(p)]) is True

    def test_the_cap_counts_the_whole_set_not_just_the_largest_part(self, tmp_path):
        # two 30 MB parts are one 60 MB payload: neither is over the cap alone
        a, b = tmp_path / "a.3mf", tmp_path / "b.3mf"
        for f in (a, b):
            with open(f, "wb") as fh:
                fh.truncate(30 * 1024 * 1024)
        assert uploader.attach_needs_cdp([str(a), str(b)]) is True

    def test_a_size_that_cannot_be_read_takes_the_safe_route(self, tmp_path):
        assert uploader.attach_needs_cdp([str(tmp_path / "gone.3mf")]) is True


class TestFindModelInputThroughCdp:
    def test_only_the_model_file_input_is_offered_not_the_images_one(self):
        # CDP answers DOM.getDocument with {"root": …}; the walker takes the root
        assert uploader.file_input_nodes(CDP_TREE) == [5, 9]

    def test_a_tree_without_a_model_input_finds_nothing(self):
        imgs = {"nodeId": 1, "nodeName": "DIV", "children": [_input(2, ".jpg,.png")]}
        assert uploader.file_input_nodes(imgs) == []

    def test_a_node_without_an_id_cannot_be_addressed(self):
        tree = {"nodeId": 1, "nodeName": "DIV",
                "children": [{"nodeName": "INPUT",
                              "attributes": ["type", "file", "accept", ".stl"]}]}
        assert uploader.file_input_nodes(tree) == []

    def test_accept_is_matched_case_insensitively(self):
        tree = {"nodeId": 1, "nodeName": "DIV",
                "children": [_input(4, ".STL,.3MF")]}
        assert uploader.file_input_nodes(tree) == [4]


class TestCoverFailureReason:
    def test_a_listing_with_no_picture_at_all_says_so(self):
        g = {"images": 0, "downloaded": 0, "page_images": 14, "creator_images": 0,
             "found_by": []}
        why = main.cover_failure_reason(g)
        assert "no picture of the model at all" in why
        assert "14 image(s) on the page" in why

    def test_photos_the_rules_refused_are_named_instead_of_hidden(self):
        g = {"images": 0, "downloaded": 0, "page_images": 22, "creator_images": 3,
             "found_by": []}
        why = main.cover_failure_reason(g)
        assert "3 creator photo(s)" in why
        assert "did not accept" in why

    def test_photos_dropped_after_download_report_their_own_reason(self):
        g = {"images": 2, "downloaded": 0,
             "dropped": [{"url": "/upload/a.jpg", "reason": "under 200 px, or not a readable image"}]}
        assert "under 200 px" in main.cover_failure_reason(g)

    def test_an_empty_gallery_block_still_reads_as_a_skip(self):
        assert main.cover_failure_reason({}).endswith("the run skipped it and moved on")


def _repo_file(*parts) -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(here, *parts), encoding="utf-8").read()


def _gallery_js() -> str:
    """The gallery reader exactly as downloader.py holds it now (no copy to drift)."""
    src = _repo_file("downloader.py")
    i = src.index("gallery_js = ")
    open_q = src.index('"""', i)
    return src[i:src.index('"""', open_q + 3)]


class TestGalleryReaderSeesLazyPhotos:
    """The reader must consider EVERY url an image element carries.

    Reading only the first attribute present looked at a lazy placeholder and
    concluded the listing had no photos: three models in job 20261024-131810
    were skipped as "0 photo(s), 0 usable" while their files downloaded fine.
    bun scripts/check_gallery_js.js proves the behaviour against a DOM; these
    assert the rules stay in the string so an edit cannot quietly drop them."""

    JS = _gallery_js()

    @pytest.mark.parametrize("needle", [
        "data-src", "data-original", "srcset", "backgroundImage",
        'og:image', "creator_imgs", "how.push",
    ])
    def test_the_reader_carries_the_rule_that_found_it(self, needle):
        assert needle in self.JS

    def test_the_size_floor_follows_the_url_it_chose_not_the_element(self):
        # naturalWidth describes the displayed bitmap (a 1x1 placeholder), so
        # it may only reject a photo once that placeholder IS the chosen url
        assert "displayedSmall(im, s)" in self.JS


class TestDashboardShowsTheEvidence:
    """The dashboard's publish path must show the originals shelf and say, in
    the page itself, that no designer's model or photo is reused."""

    HTML = _repo_file("creality", "index.html")

    @pytest.mark.parametrize("needle", [
        "originals-grid", "Approve &amp; publish", "contact_url",
        "no designer's photos are edited or re-uploaded",
        "/originals/status", "/originals/publish",
    ])
    def test_the_originals_path_is_on_the_page(self, needle):
        assert needle in self.HTML
