"""The post-conditions, including the faults they exist to catch.

Each test injects the specific wrongness that used to slip through as a silently
successful step, and asserts the check names it. The last group pins the honesty
rule: no page model means "not verified", never "verified good", and never a
failed run.
"""
import asyncio

import page_model
import verify


def model(controls=(), images=()):
    return page_model.normalize_report({"url": "u", "title": "", "ready": "complete",
                                        "controls": list(controls),
                                        "images": list(images), "texts": []})


def field(**kw):
    base = {"tag": "input", "type": "text", "id": "", "name": "", "cls": "",
            "label": "", "value": "", "checked": None, "disabled": False,
            "required": False, "text": "", "box": [1, 1, 10, 10], "visible": True}
    base.update(kw)
    return base


def step_two_form(name="A cable organizer", licences=("CC BY", "CC BY-SA", "CC0"),
                  covers=2, consent=True):
    controls = [field(label="model name", value=name),
                field(tag="textarea", label="", value="description", visible=True)]
    for label in licences:
        controls.append(field(type="checkbox", label=label, checked=True,
                              name="lic-" + label.replace(" ", "-")))
    controls.append(field(type="checkbox", label="I have read and agree",
                          checked=consent, name="consent"))
    images = [{"src": "/cover-%d.png" % i, "alt": "", "cls": "", "complete": True,
               "box": [0, 0, 300, 300], "visible": True} for i in range(covers)]
    return model(controls=controls, images=images)


# ── the faults these checks exist to catch ──────────────────────────────────
def test_the_form_stuck_on_step_1_is_named_for_what_it_is():
    """The real failure from the run log: step 2's fields exist but are not laid
    out, so the run later refused with a reason that hid the real cause."""
    stuck = model(controls=[field(label="model name", visible=False, box=None),
                            field(tag="textarea", visible=False, box=None)])
    check = verify.step_two_open(stuck)
    assert not check.ok
    assert "not laid out" in check.reason and "step 1" in check.reason


def test_a_step_2_that_opened_is_accepted():
    assert verify.step_two_open(step_two_form()).ok


def test_a_name_that_was_not_applied_is_caught():
    check = verify.name_applied(step_two_form(name=""), "Cable organizer")
    assert not check.ok and "does not hold the name" in check.reason


def test_a_silently_unticked_licence_row_is_caught_and_named():
    """Publishing under a licence row nobody ticked is the expensive one."""
    form = step_two_form(licences=("CC BY", "CC BY-SA"))
    for c in form["controls"]:
        if c.get("label") == "CC BY-SA":
            c["checked"] = False
    check = verify.licence_rows_ticked(form, ("CC BY", "CC BY-SA", "CC0"))
    assert not check.ok
    # only the row that is actually unticked may be named
    assert check.reason == "these licence rows are NOT ticked: CC BY-SA"


def test_a_cover_slot_that_stayed_empty_is_caught():
    check = verify.covers_present(step_two_form(covers=1), expected=3)
    assert not check.ok and "1 of 3" in check.reason


def test_an_unticked_consent_box_is_caught_even_though_the_licence_boxes_are_ticked():
    """The licence rows are checkboxes too, so 'some checkbox is ticked' would
    report success for the one box whose absence makes Creality refuse."""
    check = verify.consent_ticked(step_two_form(consent=False))
    assert not check.ok and "consent checkbox is NOT ticked" in check.reason


def test_a_ticked_consent_box_passes():
    assert verify.consent_ticked(step_two_form(consent=True)).ok


# ── the honesty rule ────────────────────────────────────────────────────────
def test_without_a_page_model_nothing_is_claimed_and_nothing_fails():
    for check in (verify.step_two_open(None), verify.name_applied(None, "x"),
                  verify.licence_rows_ticked(None), verify.covers_present(None, 3),
                  verify.consent_ticked(None)):
        assert check.ok is True, "an unreadable page must not fail a run"
        assert check.checked is False, "and it must not claim to be verified"
    assert "not verified" in verify.step_two_open(None).reason


def test_the_summary_says_which_is_which():
    failed = {"step2": verify.Check(False, "still on step 1", True),
              "name": verify.Check(True, "fine", True)}
    assert "FAILED" in verify.summarise(failed)
    unverified_only = {"step2": verify.unverified()}
    assert "unverified" in verify.summarise(unverified_only)
    assert "not readable" in verify.summarise(unverified_only)


# ── the retry: the action is real, the check is a read ──────────────────────
class FakePage:
    def __init__(self, models):
        self._models = list(models)
        self.reads = 0

    async def evaluate(self, js):
        # read_page falls back to this route when there is no CDP session
        if js == page_model.PAGE_MODEL_JS:
            self.reads += 1
            if len(self._models) > 1:
                return self._models.pop(0)
            return self._models[0]
        return None

    class context:
        async def new_cdp_session(self, page):
            raise RuntimeError("no cdp in this test")


def test_a_step_that_fails_its_check_is_retried_and_then_accepted():
    """The fix for 'it works sometimes': do it again, READ IT BACK, and only
    carry on when the page agrees."""
    stuck_then_ok = [model(controls=[field(label="model name", visible=False,
                                           box=None)]),
                     step_two_form()]
    page = FakePage(stuck_then_ok)
    acted = []

    async def act():
        acted.append(1)

    check, tries = asyncio.run(verify.retry("open step 2", act,
                                            verify.step_two_open, page,
                                            attempts=3, delay_s=0))
    assert check.ok and tries == 2
    assert len(acted) == 2, "the action must actually be redone, not just re-read"
    assert page.reads == 2


def test_a_step_that_never_passes_ends_as_its_own_named_reason():
    doomed = model(controls=[field(label="model name", visible=False, box=None)])
    page = FakePage([doomed])

    async def act():
        return None

    check, tries = asyncio.run(verify.retry("open step 2", act,
                                            verify.step_two_open, page,
                                            attempts=3, delay_s=0))
    assert not check.ok and tries == 3
    assert "step 1" in check.reason


def test_an_action_that_raises_is_reported_as_that_action_failing():
    page = FakePage([step_two_form()])

    async def act():
        raise RuntimeError("the Next button was not there")

    check, tries = asyncio.run(verify.retry("open step 2", act,
                                            verify.step_two_open, page,
                                            attempts=2, delay_s=0))
    assert not check.ok and "Next button" in check.reason and tries == 2
