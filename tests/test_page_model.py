"""The whole-page read, tested offline from a recorded snapshot.

The claim this module makes is specific: ONE call returns the entire page,
including form controls that exist in the DOM but are hidden or unlaid-out --
the multi-step upload form's step 2. These tests build a small snapshot in the
protocol's own columnar shape (strings table + parallel node arrays + a separate
layout tree) and pin that behaviour. No browser, no account.
"""
import asyncio

import page_model


class Snap:
    """Builds a DOMSnapshot result the way Chrome sends one."""

    def __init__(self):
        self.strings = []
        self.names, self.attrs, self.values = [], [], []
        self.input_value, self.input_checked = [], []
        self.layout_index, self.layout_bounds = [], []

    def _s(self, text):
        if text not in self.strings:
            self.strings.append(text)
        return self.strings.index(text)

    def node(self, tag, attrs=None, text=None, value=None, checked=None,
             box=None):
        self.names.append(self._s(tag))
        flat = []
        for k, v in (attrs or {}).items():
            flat += [self._s(k), self._s(v)]
        self.attrs.append(flat)
        self.values.append(self._s(text) if text is not None else 0)
        self.input_value.append(self._s(value) if value is not None else 0)
        self.input_checked.append(bool(checked) if checked is not None else False)
        if box is not None:
            self.layout_index.append(len(self.names) - 1)
            self.layout_bounds.append(list(box))
        return self

    def build(self):
        return {
            "strings": self.strings,
            "documents": [{
                "documentURL": {"index": 0},
                "nodes": {
                    "nodeName": self.names,
                    "attributes": self.attrs,
                    "nodeValue": self.values,
                    "inputValue": self.input_value,
                    "inputChecked": self.input_checked,
                },
                "layout": {"nodeIndex": self.layout_index,
                           "bounds": self.layout_bounds},
            }],
        }


def _step_two_snapshot():
    """A step-2 upload form: the licence checkbox and the textarea are in the
    DOM but NOT laid out (that is what a hidden step looks like), the submit
    button and one cover image are laid out, and a second cover image has not
    been lazy-loaded yet."""
    s = Snap()
    s.node("DIV")
    s.node("INPUT", {"type": "checkbox", "name": "lic_cc_by", "id": "lic-1",
                     "class": "lic-row", "aria-label": "CC BY"})          # hidden
    s.node("TEXTAREA", {"name": "description"}, value="hand written note")  # hidden
    s.node("BUTTON", {"class": "btn-submit"}, text="Submit", box=(100, 200, 80, 30))
    s.node("IMG", {"src": "/a.png", "alt": "cover 1"}, box=(0, 0, 300, 300))
    s.node("IMG", {"src": "/b.png", "alt": "cover 2"})                      # not loaded
    s.node("H2", text="Step 2 · Model information")
    return s.build()


def test_a_hidden_form_control_is_in_the_model_even_though_it_has_no_box():
    model = page_model.parse_snapshot(_step_two_snapshot())
    box = [c for c in model["controls"] if c["name"] == "lic_cc_by"]
    assert box, "the hidden licence checkbox must still be in the model"
    assert box[0]["visible"] is False
    assert box[0]["box"] is None


def test_a_laid_out_control_is_visible_with_its_rectangle():
    model = page_model.parse_snapshot(_step_two_snapshot())
    submit = [c for c in model["controls"] if c["tag"] == "button"][0]
    assert submit["visible"] is True
    assert submit["box"] == [100.0, 200.0, 80.0, 30.0]


def test_live_values_and_checked_state_come_from_the_snapshot():
    """`inputValue` is the control's CURRENT value, which is the whole reason to
    read a snapshot instead of the markup."""
    s = Snap()
    s.node("INPUT", {"type": "checkbox", "name": "ok"}, checked=True)
    s.node("TEXTAREA", {"name": "description"}, value="hand written note")
    model = page_model.parse_snapshot(s.build())
    by_name = {c["name"]: c for c in model["controls"]}
    assert by_name["ok"]["checked"] is True
    assert by_name["description"]["value"] == "hand written note"


def test_images_carry_whether_they_actually_loaded():
    model = page_model.parse_snapshot(_step_two_snapshot())
    srcs = {i["src"] for i in model["images"]}
    assert srcs == {"/a.png", "/b.png"}
    loaded = [i for i in model["images"] if i["src"] == "/a.png"][0]
    lazy = [i for i in model["images"] if i["src"] == "/b.png"][0]
    assert loaded["visible"] is True and lazy["visible"] is False


def test_counts_separate_the_hidden_things_from_the_visible_ones():
    model = page_model.parse_snapshot(_step_two_snapshot())
    assert model["counts"]["controls"] == 3
    assert model["counts"]["hidden_controls"] == 2
    assert model["counts"]["images"] == 2
    assert model["counts"]["hidden_images"] == 1


def test_text_around_the_form_is_carried_so_a_step_can_be_recognised():
    model = page_model.parse_snapshot(_step_two_snapshot())
    assert any("Step 2" in t for t in model["texts"])


def test_attribute_pairs_are_read_as_name_value_not_as_a_flat_list():
    s = Snap()
    s.node("INPUT", {"type": "radio", "name": "licence", "value": "CC BY"})
    model = page_model.parse_snapshot(s.build())
    c = model["controls"][0]
    assert (c["type"], c["name"]) == ("radio", "licence")


def test_a_string_index_that_is_out_of_range_does_not_raise():
    """A malformed or truncated snapshot must degrade, not explode mid-run."""
    model = page_model.parse_snapshot({
        "strings": ["INPUT"],
        "documents": [{"nodes": {"nodeName": [0], "attributes": [[99, 99]]},
                       "layout": {"nodeIndex": [7], "bounds": [[0, 0, 1, 1]]}}],
    })
    assert model["controls"] and model["controls"][0]["visible"] is False


def test_an_empty_or_absent_snapshot_parses_to_an_empty_model():
    for bad in ({}, {"documents": []}, {"documents": [{}]}, None):
        model = page_model.parse_snapshot(bad)
        assert model["controls"] == [] and model["images"] == []


def test_the_evaluate_report_is_normalised_to_the_same_shape():
    model = page_model.normalize_report({
        "url": "https://example.test/form", "title": "Create", "ready": "complete",
        "controls": [{"tag": "input", "type": "radio", "name": "licence",
                      "value": "CC BY", "checked": True, "box": [1, 2, 10, 10]}],
        "images": [{"src": "/x.png", "box": [0, 0, 0, 0]}],
        "texts": ["Step 2"],
    })
    assert model["source"] == "evaluate"
    assert model["counts"] == {"controls": 1, "hidden_controls": 0, "images": 1,
                               "hidden_images": 1, "texts": 1}


class _FakeContext:
    def __init__(self, snapshot=None, fail=False):
        self.snapshot, self.fail = snapshot, fail
        self.sent = []

    async def new_cdp_session(self, page):
        return _FakeSession(self)


class _FakeSession:
    def __init__(self, ctx):
        self.ctx = ctx

    async def send(self, method, params=None):
        self.ctx.sent.append(method)
        self.ctx.params = params or {}
        if self.ctx.fail:
            raise RuntimeError("snapshot unavailable")
        return self.ctx.snapshot

    async def detach(self):
        pass


class _FakePage:
    def __init__(self, snap=None, report=None, fail_snapshot=False, fail_eval=False):
        self.context = _FakeContext(snap, fail_snapshot)
        self._report = report
        self.fail_eval = fail_eval
        self.evaluated = 0

    async def evaluate(self, js):
        self.evaluated += 1
        if self.fail_eval:
            raise RuntimeError("no evaluate")
        return self._report


def test_one_call_reads_the_whole_page():
    page = _FakePage(snap=_step_two_snapshot())
    model = asyncio.run(page_model.read_page(page))
    assert model["source"] == "snapshot"
    assert page.context.sent == ["DOMSnapshot.captureSnapshot"]
    assert page.evaluated == 0, "the fallback must not run when the snapshot works"


def test_the_snapshot_is_asked_for_only_what_the_model_uses():
    from page_model import _try_snapshot
    page = _FakePage(snap=_step_two_snapshot())
    asyncio.run(_try_snapshot(page, 5.0))
    asked = page.context.params
    # a long computed-styles whitelist and rectangle/paint data cost time on
    # every node of the page and buy the model nothing
    assert asked["computedStyles"] == ["display", "visibility"]
    assert asked["includeDOMRects"] is False
    assert asked["includePaintOrder"] is False


def test_it_falls_back_to_one_evaluate_when_the_snapshot_fails():
    page = _FakePage(report={"url": "u", "controls": [{"tag": "input"}],
                             "images": [], "texts": []},
                     fail_snapshot=True)
    model = asyncio.run(page_model.read_page(page))
    assert model["source"] == "evaluate" and page.evaluated == 1


def test_a_page_it_cannot_read_at_all_returns_none_so_the_old_path_still_runs():
    page = _FakePage(fail_snapshot=True, fail_eval=True)
    assert asyncio.run(page_model.read_page(page)) is None


def test_summary_says_how_much_of_the_page_is_hidden():
    line = page_model.summary(page_model.parse_snapshot(_step_two_snapshot()))
    assert "3 controls" in line and "2 hidden" in line
    assert "no page model" in page_model.summary(None)
