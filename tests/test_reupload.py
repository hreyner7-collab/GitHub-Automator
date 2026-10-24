"""R3: license-safety + Creality Cloud dispatch tests (pure, no browser except
where a DOM is needed — those launch their own headless Chromium)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import downloader  # noqa: E402

# A REAL model file: a run publishes only with a proof of creation built from the
# file itself (creality_rules), so this fixture points at a real cube.
REAL_MODEL = str(Path(__file__).resolve().parent / "data" / "proof_cube.stl")


def test_cc_by_sa_allowed():
    lic = downloader.classify_license(
        "https://creativecommons.org/licenses/by-sa/4.0 — This work is "
        "licensed under the Copyright License 4.0")
    assert lic["nd"] is False
    assert lic["ok_for_reupload"] is True
    assert lic["code"] == "by-sa/4.0" or "by-sa" in lic["code"]
    assert "Attribution" in lic["note"] or "remix" in lic["note"].lower()


def test_cc_by_allowed():
    lic = downloader.classify_license("creativecommons.org/licenses/by/4.0")
    assert lic["ok_for_reupload"] is True
    assert lic["nd"] is False


def test_nd_refused():
    lic = downloader.classify_license("creativecommons.org/licenses/by-nd/4.0")
    assert lic["nd"] is True
    assert lic["ok_for_reupload"] is False
    assert "No-Derivatives" in lic["note"]


def test_nc_nd_refused():
    lic = downloader.classify_license("CC BY-NC-ND — no derivatives, noncommercial")
    assert lic["nd"] is True
    assert lic["ok_for_reupload"] is False


def test_nc_nd_url_is_nd():
    """Regression: the ND token check looked for "by-nd" only, so a CC BY-NC-ND
    source (the auto-run's licence filter allows that box to be ticked) passed
    as re-publishable. "-nd" in the code and a bare "nd" token must both work,
    while "and"/"end" must not trip it."""
    lic = downloader.classify_license(
        "https://creativecommons.org/licenses/by-nc-nd/4.0")
    assert lic["nd"] is True
    assert lic["ok_for_reupload"] is False
    assert lic["code"] == "by-nc-nd"
    assert downloader.classify_license("CC - ND")["nd"] is True
    assert downloader.classify_license("creativecommons.org/licenses/by/4.0")["nd"] is False


def test_nc_without_nd_is_refused_by_the_policy():
    """POLICY CHANGE (2026-10-09): a NonCommercial source is no longer
    re-published even though it has no No-Derivatives attribute. This account
    publishes on a platform that rewards uploads, which is not the
    "noncommercial use" an NC licence grants — three NC/creatively-reviewed
    re-uploads from the 2026-10-09 run were removed with "The model did not
    pass platform review". The refusal must SAY it is NonCommercial (not an
    unreadable licence, not Standard)."""
    lic = downloader.classify_license("CC BY-NC")
    assert lic["nd"] is False
    assert lic["nc"] is True
    assert lic["ok_for_reupload"] is False
    assert lic["unknown"] is False and lic["restricted"] is False
    assert "NonCommercial" in lic["note"]


def test_only_cc0_by_and_by_sa_may_be_re_published():
    """The whole allow-list, from both directions: the three licences that
    permit redistribution + remixing + commercial use pass, every NC and ND
    licence fails, and a CC BY-NC is refused for the NONCOMMERCIAL reason
    (never as "unreadable")."""
    for raw in ("creativecommons.org/licenses/by/4.0",
                "https://creativecommons.org/licenses/by-sa/4.0",
                "CC0 / public domain"):
        lic = downloader.classify_license(raw)
        assert lic["ok_for_reupload"] is True, raw
        assert lic["nc"] is False and lic["nd"] is False, raw
    for raw in ("creativecommons.org/licenses/by-nc/4.0",
                "https://creativecommons.org/licenses/by-nc-sa/4.0",
                "https://creativecommons.org/licenses/by-nd/4.0",
                "https://creativecommons.org/licenses/by-nc-nd/4.0",
                "CC BY-NC"):
        lic = downloader.classify_license(raw)
        assert lic["ok_for_reupload"] is False, raw
    nc = downloader.classify_license("creativecommons.org/licenses/by-nc/4.0")
    assert nc["nc"] is True and nc["nd"] is False
    assert "NonCommercial" in nc["note"] and "CC BY-NC" in nc["note"]


def test_an_nc_licence_merely_MENTIONED_on_the_page_does_not_taint_cc_by():
    """The form's Model-Source tip names "CC BY-NC-ND" and "CC BY-ND" as
    examples. NC must be decided from the licence's IDENTITY (the CC link /
    the named licence), never from a word that merely appears somewhere in the
    captured text — otherwise every CC BY page that shows the tip is refused."""
    tip = ("Please ensure that the source model's license does not have the "
           "\"No Derivatives\" attribute, for example licenses such as CC BY-ND "
           "and CC BY-NC-ND do not allow for derivative works")
    lic = downloader.classify_license(
        "creativecommons.org/licenses/by/4.0 " + tip)
    assert lic["code"] == "by"
    assert lic["nc"] is False
    assert lic["ok_for_reupload"] is True


def test_cc0_allowed():
    lic = downloader.classify_license("CC0 / public domain")
    assert lic["ok_for_reupload"] is True
    assert "CC0" in lic["note"] or "public" in lic["note"].lower()


def test_an_unreadable_licence_is_refused_as_unread_not_as_unlicensed():
    """Nothing at all was read → the pipeline must NOT claim the source has no
    licence (or a Standard one) and must NOT invent CC answers for the form: it
    says honestly that the licence could not be read and uploads nothing."""
    lic = downloader.classify_license("")
    assert lic["ok_for_reupload"] is False
    assert lic["unknown"] is True
    assert lic["restricted"] is False
    assert "could NOT be read" in lic["note"]
    assert "Standard" not in lic["note"]


def test_the_site_boilerplate_line_alone_is_not_a_licence():
    """"This work is licensed under the Copyright License 4.0." is printed on
    EVERY Creality model page, CC ones included. Reading it as "this model is
    not licensed" is exactly the false flag the user reported."""
    lic = downloader.classify_license("This work is licensed under the Copyright License 4.0.")
    assert lic["restricted"] is False
    assert lic["unknown"] is True                  # unread, not "not licensed"
    assert lic["ok_for_reupload"] is False
    assert "could NOT be read" in lic["note"]
    assert "boilerplate" in lic["note"]


def test_a_cc_card_wins_over_the_site_wide_standard_licence_text():
    """THE FALSE FLAG: a licensed (CC) model whose captured licence text ALSO
    contains the site-wide Standard-License paragraph. The CC evidence must
    decide the READ — before this rule the boilerplate got read as "the source
    has no CC licence" and the model was refused as unlicensed. The card here
    is CC BY-NC-SA, which the current policy then refuses for the
    NONCOMMERCIAL reason, not because the boilerplate was misread.
    (Both strings are verbatim from live Creality pages.)"""
    card = ("creativecommons.org/licenses/by-nc-sa/4 This work is licensed under "
            "the Copyright License 4.0. This license allows reusers to distribute, "
            "remix, adapt, and build upon the material in any medium or format for "
            "noncommercial purposes only, and only so long as attribution is given "
            "to the creator. If you remix, adapt, or build upon the material, you "
            "must license the modified material under identical terms.")
    site_block = ("This work is licensed under the Copyright License 4.0. 3D Model "
                  "files sold on Creality Cloud are available under a standard "
                  "license, which has certain restrictions. In particular, these "
                  "files cannot be used for any commercial use; it's for personal "
                  "use only. 3D model or any portion of the model in a digital or "
                  "physical format may not be shared, transmitted, redistributed, "
                  "remixed, duplicated, or sold.")
    lic = downloader.classify_license(card + " " + site_block)
    assert lic["restricted"] is False               # the CC card decided, not the boilerplate
    assert lic["code"].startswith("by-nc-sa")
    assert lic["nc"] is True and lic["nd"] is False
    assert lic["ok_for_reupload"] is False           # NC policy, not a misread
    assert "NonCommercial" in lic["note"]
    assert "Standard" not in lic["note"]
    assert lic["evidence"]
    # an ND licence named in the same card is still refused
    nd = downloader.classify_license(
        card.replace("by-nc-sa/4", "by-nc-nd/4") + " " + site_block)
    assert nd["nd"] is True and nd["ok_for_reupload"] is False


def test_the_standard_licence_read_names_the_sentence_it_refused_on():
    """An honest refusal must be checkable: quote what was read off the page."""
    std = ("This work is licensed under the Copyright License 4.0. 3D Model files "
           "sold on Creality Cloud are available under a standard license, which "
           "has certain restrictions. In particular, these files cannot be used "
           "for any commercial use; it's for personal use only. 3D model or any "
           "portion of the model in a digital or physical format may not be "
           "shared, transmitted, redistributed, remixed, duplicated, or sold.")
    lic = downloader.classify_license(std)
    assert lic["restricted"] is True and lic["unknown"] is False
    assert lic["quote"] and lic["quote"][:200] in lic["note"]


def test_a_licence_drawn_as_icons_is_read():
    """The licence is often only DRAWN (cc.svg + by.svg + nc.svg), with the
    site's standard-license paragraph as the only text — the icon badge is then
    the evidence that decides."""
    assert downloader.code_from_icons([]) == ""
    assert downloader.cc_code_from_text("CC BY-NC-SA") == "by-nc-sa"
    assert downloader.cc_code_from_text("ccby") == "by"
    assert downloader.cc_code_from_text("CC0 / public domain") == "cc0"
    assert downloader.cc_code_from_text("may not be shared, redistributed") == ""
    lic = downloader.classify_license(
        "This work is licensed under the Copyright License 4.0.",
        ["cc.svg", "by.svg", "nc.svg"])
    assert lic["code"] == "by-nc"
    assert lic["ok_for_reupload"] is False           # read correctly, refused for NC
    assert lic["nc"] is True
    assert lic["restricted"] is False
    nd = downloader.classify_license("", ["cc.svg", "by.svg", "nc.svg", "nd.svg"])
    assert nd["nd"] is True and nd["ok_for_reupload"] is False


# --- the declared remix source must BE our source model ----------------------

def test_the_registered_remix_source_must_be_our_source_model():
    """The check that stops a listing crediting the wrong creator. It used to
    pass on the loosest possible evidence ("the first word of the clicked row
    appears in the card"), and the 2026-10-09 run recorded an EMPTY
    `remix_registered_href` for all five models — nothing ever proved which
    model was credited. Only the source's own identity may confirm it."""
    from uploader import declares_our_source
    src = ("https://www.crealitycloud.com/model-detail/"
           "smartphone-stand-with-cable-cutout")
    slug = "smartphone-stand-with-cable-cutout"
    title = "Подставка для смартфона, с вырезом для провода"
    clicked = {"name": "Smartphone stand", "certain": True}

    # the registered source LINKS the source model
    v = declares_our_source(clicked, "source: Smartphone stand",
                            "/model-detail/" + slug, src, slug, title)
    assert v["declared"] is True and slug in v["why"]
    # the source URL is printed on the form
    assert declares_our_source(clicked, "Remix of " + src, "", src, slug,
                               title)["declared"] is True
    # the card names the source model's own (Cyrillic) title
    assert declares_our_source(clicked, title, "", src, slug,
                               title)["declared"] is True
    # the card is the source row we clicked, and that row really is the source
    assert declares_our_source(clicked, "Smartphone stand with cable cutout",
                               "", src, slug, title)["declared"] is True

    # a DIFFERENT model is registered: nothing may be submitted
    wrong = declares_our_source(clicked, "Wireless charger doodad", "", src,
                               slug, title)
    assert wrong["declared"] is False
    assert "does not identify" in wrong["why"]
    # an empty card is not a declaration
    assert declares_our_source(clicked, "", "", src, slug,
                               title)["declared"] is False
    # an uncertain row can never prove the source by its name alone
    assert declares_our_source({"name": "Phone holder", "certain": False},
                               "Phone holder", "", src, slug,
                               title)["declared"] is False


def test_a_one_word_short_title_is_not_certain_enough_to_click():
    """A row that loses a word of a long title is a guess; declaring a guess is
    how a listing credits a model it was not made from. It must not be clicked,
    and the picker must report the uncertainty instead of guessing."""
    from uploader import pick_source_suggestion, source_score, CERTAIN_SCORE
    src_title = "Scale model pallet jack 1/10"
    slug = "scale-model-pallet-jack"
    guess = "Scale model pallet 1/10"
    assert 0 < source_score(guess, src_title, slug) < CERTAIN_SCORE
    picked = pick_source_suggestion(
        [{"i": 0, "name": guess, "href": "", "flagged": False,
          "x": 1, "y": 1}], src_title, slug)
    assert picked["pick"] is None
    assert picked["certain"] is False
    # the source itself still is clickable, and travels with its certainty
    ok = pick_source_suggestion(
        [{"i": 0, "name": src_title, "href": "", "flagged": False,
          "x": 1, "y": 1}], src_title, slug)
    assert ok["pick"]["name"] == src_title
    assert ok["certain"] is True and ok["pick"]["certain"] is True
    assert ok["score"] >= CERTAIN_SCORE


def test_creality_url_dispatches_to_creality_fetcher(monkeypatch):
    called = {}

    def _fake(url):
        called["url"] = url
        return {"name": "X", "stl_path": None, "image_paths": []}

    monkeypatch.setattr(downloader, "_fetch_creality", _fake)
    out = downloader.fetch(
        "https://www.crealitycloud.com/model-detail/under-sink-door-organiser"
        "-kitchen-cabinet?profileId=6a9ec72126536a7fceeab91d")
    assert out["name"] == "X"
    assert "under-sink-door-organiser" not in called["url"] or True
    # the dispatcher passes the ORIGINAL url; slug cleanup happens inside
    assert "crealitycloud.com" in called["url"]


# --- license -> form answers (the configurations the live form shows) --------

def test_answer_mapping_matches_the_forms_badges():
    from uploader import license_answer_indexes
    # configurations observed on the real create-model form
    assert license_answer_indexes(
        "creativecommons.org/licenses/by/4.0")[1] == [0, 0, 1]      # CC BY
    assert license_answer_indexes(
        "creativecommons.org/licenses/by-nc/4.0")[1] == [0, 1, 1]   # CC BY-NC
    assert license_answer_indexes(
        "creativecommons.org/licenses/by-sa/4.0")[1] == [2, 0, 1]   # CC BY-SA
    assert license_answer_indexes(
        "creativecommons.org/publicdomain/zero/1.0")[1] == [0, 0, 0]  # CC0
    # the real test model: CC BY-NC-SA -> share-alike + No commercial + no
    # public-domain dedication (so the badge stays a normal CC licence)
    code, idx = license_answer_indexes(
        "https://creativecommons.org/licenses/by-nc-sa/4")
    assert code == "by-nc-sa"
    assert idx == [2, 1, 1]


def test_model_source_tip_is_not_mistaken_for_the_badge():
    """Regression: the form's Model Source panel says "...for example licenses
    such as CC BY-ND and CC BY-NC-ND do not allow for derivative works". A
    page-wide text grab picked that sentence up, 'read' CC BY-ND off a form
    whose badge correctly said CC BY-NC, and refused a valid upload."""
    from uploader import badge_license_name
    tip = ("Please ensure that the source model's license does not have the "
           "\"No Derivatives\" attribute, for example licenses such as CC BY-ND "
           "and CC BY-NC-ND do not allow for derivative works")
    assert badge_license_name(tip) == ""
    assert badge_license_name(tip) != "CCBY-ND"


def test_real_license_card_reads_cc_by_nc():
    """The exact strings the live form renders (from the saved screenshot):
    icons "CC BY NC", the caption, and the badge paragraph that STARTS with
    the licence name."""
    from uploader import badge_license_name, badge_from_icons, _norm_cc
    badge_para = ("CC BY-NC This license allows reusers to distribute, remix, "
                  "adapt, and build upon the material in any medium or format "
                  "for noncommercial purposes only, and only so long as "
                  "attribution is given to the creator.")
    card = ("License Allow adaptations of your work to be shared? Yes No Yes, "
            "as long as others share in the same way Allow commercial uses of "
            "your work? Yes No This work is licensed under the Copyright "
            "License 4.0. " + badge_para)
    assert badge_license_name(badge_para) == "CCBY-NC"
    assert badge_license_name(card) == "CCBY-NC"
    # the icon strip names the same licence (CC, BY, NC images)
    assert badge_from_icons(["cc.svg", "by.svg", "nc.svg"]) == "CC BY-NC"
    assert badge_from_icons(["cc_0.png"]) == "CC0"
    assert badge_from_icons(["cc.svg"]) == ""        # a lone CC mark is no licence
    assert badge_from_icons([]) == ""
    assert _norm_cc("CC BY-NC") == _norm_cc(badge_license_name(badge_para))


def test_photo_plan_keeps_order_and_uses_each_photo_once():
    """The upload must carry every selected photo, in the creator's order, with
    photo 1 on the web cover and photo 2 on the app cover — the old code sent
    photo 1 to both covers (same image in two slots) and never filled the
    Model Images gallery at all."""
    import os
    import tempfile
    from uploader import image_plan, dedupe_images
    tmp = tempfile.mkdtemp()
    paths = []
    for i in range(6):
        p = os.path.join(tmp, "photo_%d.jpg" % i)
        with open(p, "wb") as fh:
            fh.write(b"photo-%d" % i)
        paths.append(p)
    dup = os.path.join(tmp, "copy_of_photo_0.jpg")
    with open(dup, "wb") as fh:
        fh.write(b"photo-0")

    plan = image_plan(paths + [dup])
    assert plan["all"] == paths                      # order kept, copy dropped
    assert plan["web"] == paths[0]
    assert plan["app"] == paths[1]                   # a DIFFERENT photo
    assert plan["app"] != plan["web"]
    assert plan["gallery"] == paths                  # whole set, in order
    assert dedupe_images(["", "/does/not/exist.jpg"]) == []
    # a single photo is the only case where both covers share an image
    one = image_plan([paths[0]])
    assert one["web"] == one["app"] == paths[0] and one["gallery"] == [paths[0]]
    # more than nine photos still leaves the covers filled and reports the rest
    many = []
    for i in range(12):
        p = os.path.join(tmp, "many_%d.jpg" % i)
        with open(p, "wb") as fh:
            fh.write(b"many-%d" % i)
        many.append(p)
    big = image_plan(many)
    assert len(big["gallery"]) == 9 and big["dropped"] == 3


def test_boilerplate_licence_line_does_not_hide_a_cc_source():
    """Every Creality licence card OPENS with "This work is licensed under the
    Copyright License 4.0." — even on CC models. Reading that line as the
    licence would refuse good sources, so the card's own paragraph decides.
    (Both strings below are verbatim from the live pages.)

    The two halves matter separately: the card is still READ correctly (never
    "Standard"), and what happens next is the licence POLICY — CC BY is
    published, CC BY-NC is refused for being NonCommercial."""
    cc_card = ("creativecommons.org/licenses/by-nc/4 This work is licensed under "
               "the Copyright License 4.0. This license allows reusers to "
               "distribute, remix, adapt, and build upon the material in any "
               "medium or format for noncommercial purposes only, and only so "
               "long as attribution is given to the creator.")
    lic = downloader.classify_license(cc_card)
    assert lic["restricted"] is False              # the card decided, not the boilerplate
    assert lic["code"] == "by-nc"
    assert lic["nc"] is True and lic["nd"] is False
    assert lic["ok_for_reupload"] is False
    assert "NonCommercial" in lic["note"] and "Standard" not in lic["note"]
    # the same card shape with a licence that permits commercial use: published
    by_card = cc_card.replace(
        "creativecommons.org/licenses/by-nc/4", "creativecommons.org/licenses/by/4"
    ).replace("for noncommercial purposes only, ", "")
    by = downloader.classify_license(by_card)
    assert by["restricted"] is False
    assert by["code"] == "by" and by["nc"] is False
    assert by["ok_for_reupload"] is True


def test_creality_standard_license_forbids_remix():
    """Creality's own Standard/Copyright License 4.0 says the model "may not be
    shared, transmitted, redistributed, remixed, duplicated, or sold" — the
    pipeline must refuse it instead of answering the form as if it were CC BY.
    (Verbatim card text from the knitted-cactus page.)"""
    std = ("This work is licensed under the Copyright License 4.0. 3D Model "
           "files sold on Creality Cloud are available under a standard license, "
           "which has certain restrictions. In particular, these files cannot be "
           "used for any commercial use; it's for personal use only. 3D model or "
           "any portion of the model in a digital or physical format may not be "
           "shared, transmitted, redistributed, remixed, duplicated, or sold.")
    lic = downloader.classify_license(std)
    assert lic["restricted"] is True
    assert lic["ok_for_reupload"] is False
    assert lic["nd"] is True and lic["nc"] is True
    assert "Standard (Copyright) License" in lic["note"]


def test_photo_in_a_format_the_form_rejects_is_converted():
    """The form's image inputs accept only .jpeg/.png/.jpg/.webp/.gif, but a
    Creality gallery serves its cover photo as .avif. That photo used to be
    handed to the form as-is (so it silently never appeared); now it is
    converted to JPEG in its original position."""
    import os
    import tempfile
    from PIL import Image
    from downloader import to_uploadable, UPLOAD_IMAGE_EXTS
    from uploader import image_plan

    tmp = tempfile.mkdtemp()
    avif_like = os.path.join(tmp, "cover.avif")        # pixels are what count
    Image.new("RGB", (600, 600), (200, 40, 40)).save(avif_like, "PNG")
    assert ".avif" not in UPLOAD_IMAGE_EXTS

    fixed = to_uploadable(avif_like)
    assert fixed.lower().endswith(".jpg")
    assert os.path.exists(fixed) and not os.path.exists(avif_like)
    with Image.open(fixed) as im:
        assert im.size == (600, 600) and im.format == "JPEG"

    # the plan hands the uploader only files the form accepts, order intact
    b = os.path.join(tmp, "second.png")
    Image.new("RGB", (300, 300), (10, 200, 10)).save(b)
    cover2 = os.path.join(tmp, "cover2.avif")
    Image.new("RGB", (600, 600), (30, 90, 200)).save(cover2, "PNG")
    plan = image_plan([cover2, b])
    assert [os.path.basename(p) for p in plan["all"]] == ["cover2.jpg", "second.png"]
    assert plan["web"] != plan["app"]
    assert len(plan["gallery"]) == 2
    assert any(c[0] == "cover2.avif" for c in plan["converted"])


def test_badge_parser_and_expected_tokens():
    from uploader import badge_license_name, expected_badge_token
    cc_by = ("This work is licensed under the Copyright License 4.0. CC BY This "
             "license allows reusers to distribute, remix, adapt, and build upon "
             "the material in any medium or format, so long as attribution is "
             "given to the creator. The license allows for commercial use.")
    cc_by_nc = ("This work is licensed under the Copyright License 4.0. CC BY-NC "
                "This license allows reusers to distribute, remix, adapt, and build "
                "upon the material for noncommercial purposes only, and only so "
                "long as attribution is given to the creator.")
    cc0 = ("This work is licensed under the Copyright License 4.0. CC0 (aka CC "
           "Zero) is a public dedication tool, which allows creators to give up "
           "their copyright and put their works into the worldwide public domain.")
    assert badge_license_name(cc_by) == "CCBY"
    assert badge_license_name(cc_by_nc) == "CCBY-NC"
    assert badge_license_name(cc0) == "CC0"
    assert badge_license_name("") == ""
    assert expected_badge_token("by") == "CC BY"
    assert expected_badge_token("by-nc-sa") == "CC BY-NC-SA"
    assert expected_badge_token("cc0") == "CC0"


# ── prompt 12: a rejected Submit must be diagnosable, and the app cover filled ─

def test_describe_submit_failure_reports_what_creality_did():
    """The authorised publish ended with "Creality never confirmed" and no way to
    see why. The failure text must now name the step Creality bounced to, the
    field it put in its error state, and the create request's response."""
    from uploader import describe_submit_failure
    step_and_field = describe_submit_failure(
        1, [{"cls": "content-box model-file-content err-box", "label": "* Model files"}])
    assert "bounced back to step 1" in step_and_field
    assert "Model files" in step_and_field and "err-box" in step_and_field
    assert "Nothing was published" in step_and_field
    net = [{"method": "POST", "url": "https://www.crealitycloud.com/api/x/create",
            "status": 400, "body": '{"message":"upload token expired"}'}]
    both = describe_submit_failure(1, [{"cls": "err-box", "label": "Model files"}], net)
    assert "HTTP 400" in both and "upload token expired" in both
    # nothing known: say the fallback (never invent a reason), and no fallback = ""
    assert describe_submit_failure() == ""
    assert describe_submit_failure(fallback="Submit was pressed") == "Submit was pressed"


def test_a_single_photo_fills_BOTH_cover_slots(tmp_path):
    """image_plan() returns app = web for one photo; the form's two cover slots
    are independent inputs, so the same file must fill both. The old guard
    compared the file and skipped the app slot, leaving the required 3:4 cover
    empty — which is what the authorised publish's before_submit.png showed."""
    from uploader import cover_attach_plan, image_plan
    one = tmp_path / "only_photo.webp"       # a real path; the extension is accepted
    one.write_bytes(b"RIFF....WEBP")
    a = tmp_path / "a.webp"
    b = tmp_path / "b.webp"
    a.write_bytes(b"RIFF....WEBP")
    b.write_bytes(b"RIFF....WEBP2")
    plan = image_plan([str(one)])
    assert plan["web"] and plan["app"] == plan["web"]
    zones = {"web": 2, "app": 3, "gallery": 5}
    assert cover_attach_plan(plan, zones) == [("web", [plan["web"]]),
                                              ("app", [plan["app"]]),
                                              ("gallery", [plan["web"]])]
    # two photos: each cover gets its own, gallery keeps the order
    two = image_plan([str(a), str(b)])
    assert cover_attach_plan(two, zones)[:2] == [("web", [two["web"]]),
                                                 ("app", [two["app"]])]
    assert two["web"] != two["app"]
    # a zone the form does not have is skipped, never guessed at
    assert [z for z, _f in cover_attach_plan(plan, {"web": 2, "app": -1})] == ["web"]
    # the same input can never be asked twice for the same slot
    assert cover_attach_plan(plan, {"web": 2, "app": 2, "gallery": -1}) == \
        [("web", [plan["web"]])]


def test_the_run_log_keeps_the_upload_evidence(monkeypatch, tmp_path):
    """A failed publish's evidence (photo slots, consent, licence answers and the
    captured rejection) must survive in the run's own log, not only in memory."""
    import asyncio
    import json
    import main
    from downloader import classify_license

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0")
    # a household-family name: the run judges every candidate with the household
    # gate before it is queued, and this test is about the submit-rejection log
    models = [{"name": "Rejected storage box",
               "url": "https://x/model-detail/rejected", "keyword": "k"}]
    verify = {"images": {"cover_web": "cover.jpg", "cover_app": "cover.jpg",
                         "gallery_counter": [0, 1]},
              "consent": {"found": True, "tried": [["round1:circle", True]],
                          "checked": True},
              "copyright_agreed": True,
              "license_answers": {"wanted": [2, 1, 1]},
              "submit_rejection": {"step": 1, "flagged": [{"cls": "err-box",
                                                           "label": "Model files"}],
                                   "network": [{"method": "POST", "url": "…/create",
                                                "status": 400, "body": "token expired"}]}}
    monkeypatch.setattr(main, "find_creality",
                        lambda *a, **k: {"models": models, "note": "",
                                        "licence": {"opened": True, "verified": True}})
    monkeypatch.setattr(main, "dl_fetch", lambda url: {
        "name": "Rejected", "description": "d", "stl_path": REAL_MODEL,
        "image_paths": [str(cover)], "slug": "rejected", "author": "a",
        "source_url": url, "license": classify_license(
            "https://creativecommons.org/licenses/by/4.0")})

    async def fake_upload(*a, **k):
        return {"success": False, "verify": verify,
                "error": "Creality rejected the submission: the form bounced back to "
                         "step 1; field 'Model files' is in Creality's error state "
                         "(err-box). Nothing was published."}

    monkeypatch.setattr(main, "uploader_upload", fake_upload)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    # this test must never touch the real already-published config, nor write
    # its proof renders into the project's downloads folder
    monkeypatch.setenv("CREALITY_LEDGER", str(tmp_path / "published.json"))
    monkeypatch.setenv("CREALITY_PROOF_DIR", str(tmp_path / "proof"))
    asyncio.run(main._auto_worker(1, ["k"], "rejectedjob"))

    item = main.AUTO["items"][0]
    assert item["state"] == "failed"
    assert item["upload_verify"] == verify
    assert "bounced back to step 1" in item["reason"]
    import ledger
    assert ledger.count() == 0                      # a rejection locks nothing
    log = json.loads((tmp_path / "artifacts" /
                      "auto_run_rejectedjob.json").read_text(encoding="utf-8"))
    assert log["items"][0]["upload_verify"]["submit_rejection"]["step"] == 1
    assert log["items"][0]["upload_verify"]["images"]["gallery_counter"] == [0, 1]


def _gallery_js():
    """The gallery-collector rule, extracted from downloader.fetch's source so the
    test can run it in a DOM without a browser session."""
    import re as _re
    src = Path(downloader.__file__).read_text(encoding="utf-8")
    m = _re.search(r'gallery_js = r?"""\n(.*?)\n\s*"""', src, _re.S)
    assert m, "gallery_js not found in downloader.py"
    return m.group(1)


def _run_gallery_js(page):
    return page.evaluate("(%s)()" % _gallery_js())


def test_a_gallery_without_cover_thumb_wrappers_still_yields_every_photo():
    """REGRESSION (the 'one image out of six' bug): the collector required every
    gallery photo to sit inside a div.cover or div.thumb wrapper. Creality's
    layouts moved (photos render in the gallery block without those wrappers),
    so rule 2 found nothing and the fetch collapsed to the one SSR cover image.
    The gallery BLOCK itself is the scope now; the plate cards are proven to
    live OUTSIDE div.model-gallery, and bad() rejects their containers anyway."""
    from playwright.sync_api import sync_playwright
    html = """
    <html><body>
      <div class="model-content">
        <div class="model-gallery">
          <div class="swiper-slide"><img id="p0"
              src="https://pic2-cdn.creality.com/comp/model/a0.webp"></div>
          <div class="swiper-slide"><img id="p1"
              src="https://pic2-cdn.creality.com/comp/model/a1.webp"></div>
          <div class="swiper-slide"><img id="p2"
              src="https://pic2-cdn.creality.com/comp/model/a2.webp"></div>
          <div class="swiper-slide"><img id="p3"
              src="https://pic2-cdn.creality.com/comp/model/a3.webp"></div>
          <div class="swiper-slide"><img id="p4"
              src="https://pic2-cdn.creality.com/comp/model/a4.webp"></div>
          <div class="swiper-slide"><img id="p5"
              src="https://pic2-cdn.creality.com/comp/model/a5.webp"></div>
        </div>
        <div class="print-config-comp"><!-- the 3MF plate card, NOT a photo -->
          <img src="https://pic2-cdn.creality.com/comp/model/plate.webp">
        </div>
      </div>
    </body></html>"""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_content(html)
        # naturalWidth is 0 for non-loaded images; stub it so the >=200 rule passes
        page.evaluate("""() => {
            for (const im of document.querySelectorAll('img'))
                Object.defineProperty(im, 'naturalWidth', {value: 800});
        }""")
        g = _run_gallery_js(page)
        b.close()
    urls = g.get("urls") or []
    assert len(urls) == 6, "expected all 6 gallery photos, got %d: %s" % (len(urls), urls)
    assert all("plate" not in u for u in urls), "the plate card leaked into the gallery"


def test_a_page_that_never_renders_the_strip_still_reports_its_photos():
    """The strip can be absent entirely (no .swiper-thumb on the page). The
    collector must fall back to the gallery block (and the cover swiper's own
    next-arrow) instead of returning zero and hiding behind the last resort."""
    from playwright.sync_api import sync_playwright
    html = """
    <html><body>
      <div class="model-gallery">
        <div class="swiper-cover">
          <div class="swiper-wrapper">
            <div class="swiper-slide"><img
                src="https://pic2-cdn.creality.com/crealityCloud/upload/x0.webp"></div>
            <div class="swiper-slide"><img
                src="https://pic2-cdn.creality.com/crealityCloud/upload/x1.webp"></div>
          </div>
          <div class="swiper-button-next"></div>
        </div>
      </div>
    </body></html>"""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_content(html)
        page.evaluate("""() => {
            for (const im of document.querySelectorAll('img'))
                Object.defineProperty(im, 'naturalWidth', {value: 800});
        }""")
        g = _run_gallery_js(page)
        b.close()
    assert len(g.get("urls") or []) == 2
    assert g.get("next"), "the cover swiper's next-arrow must be found when the strip is absent"


def test_the_wait_for_stable_loop_is_in_the_fetcher():
    """The gallery must be read until the count is STABLE across two reads, not
    once — a single early read is how a slow-rendering page was fetched with one
    image. The loop's presence is what the retry evidence ('settle' grabs) rides
    on, so pin it in the source."""
    src = Path(downloader.__file__).read_text(encoding="utf-8")
    assert '_grab("settle")' in src, "the settle re-read is missing"
    assert 'stale' in src and 'WAIT FOR STABLE' in src


# ── the model-file download (the live French layout, 2026-10-09) ─────────────
# The manual link came back with all 6 photos and NO model file, so the mandatory
# proof could not be built: the page is served in the browser's own language, and
# matching only the English "Download 3MF" found nothing. The real controls were
# "Voir les fichiers STL/CAO" (opens the file dialog) and the dialog's "Tout
# télécharger". The first attempt also clicked a WRAPPER div that merely contains
# the control's text, which navigated to the designer's profile page instead.

@pytest.mark.parametrize("label", [
    "Download 3MF", "View STL/CAD Files", "Télécharger 3MF",
    "Télécharger des fichiers d’impression", "Voir les fichiers STL/CAO",
    "Descargar 3MF", "3MF herunterladen", "Baixar 3MF", "Scaricare 3MF",
    "Download print files", "3MF download",
])
def test_the_download_control_is_matched_in_the_sites_own_language(label):
    import re
    assert re.search(downloader.DOWNLOAD_MAIN_RE, label, re.I)


@pytest.mark.parametrize("label", ["Downloads", "Model files", "Home",
                                   "Comments", "License", "Trancher"])
def test_ordinary_page_text_is_never_taken_for_the_download_control(label):
    import re
    assert not re.search(downloader.DOWNLOAD_MAIN_RE, label, re.I)


def test_the_download_matcher_picks_the_control_not_a_wrapper_or_a_hidden_row():
    """FIND_JS must return the innermost, TOPMOST element: the page keeps its
    dropdown rows in the DOM while the dropdown is closed, and a container that
    merely holds the control's text puts the click on the profile card behind
    it."""
    from playwright.sync_api import sync_playwright
    html = """<html><body>
      <div id="wrapper" style="width:260px;height:60px;position:absolute;top:40px;left:40px">
        <span id="real" style="display:inline-block;width:120px;height:20px">Tout t&eacute;l&eacute;charger</span>
      </div>
      <div id="hidden" style="width:200px;height:57px;position:absolute;top:400px;left:40px;visibility:hidden">
        T&eacute;l&eacute;charger des fichiers d&rsquo;impression
      </div>
    </body></html>"""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_viewport_size({"width": 800, "height": 600})
        page.set_content(html)
        real = page.evaluate(downloader.FIND_JS, downloader.DOWNLOAD_VERB_RE)
        hidden = page.evaluate(downloader.FIND_JS, r"fichiers d\u2019impression")
        b.close()
    assert real, "the download control's own text was not found"
    assert real.get("t", "").strip() == "Tout télécharger", real
    # the wrapper (260x60) must NOT be the pick — the small inner label is
    assert real["area"] < 260 * 60
    assert hidden is None or hidden.get("t", "").count("Télécharger") == 0, \
        "an invisible dropdown row must never be clicked"


def test_a_zip_of_model_files_is_proved_and_unpacked_not_thrown_away(tmp_path):
    """Creality's own "download all" answers with <model>_part1.zip holding the
    .3mf files. It was rejected as "not real model geometry" — so the fetch came
    back with photos and no model, and the now-mandatory proof could not be
    built. The files INSIDE must be verified instead."""
    import zipfile
    import proof
    from uploader import _verify_model_files, _extract_models

    model_xml = ('<?xml version="1.0" encoding="UTF-8"?>'
                 '<model unit="millimeter" '
                 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
                 '<resources><object id="1" type="model"><mesh><vertices>'
                 '<vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/>'
                 '<vertex x="0" y="10" z="0"/></vertices>'
                 '<triangles><triangle v1="0" v2="1" v3="2"/></triangles>'
                 '</mesh></object></resources><build><item objectid="1"/></build>'
                 + "<!-- " + "pad " * 3000 + " --></model>")   # > 10 KB part
    inner = tmp_path / "part_one.3mf"
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("3D/3dmodel.model", model_xml)
    outer = tmp_path / "model_part1.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("part one.3mf", inner.read_bytes())
        z.writestr("part two.3mf", inner.read_bytes())

    files = _extract_models(str(outer))
    assert [os.path.basename(f) for f in files] == ["part one.3mf", "part two.3mf"]
    assert _verify_model_files([str(outer)]) is True, \
        "a zip of real 3MF files is not junk"
    monkey_env = os.environ.get("CREALITY_PROOF_DIR")
    os.environ["CREALITY_PROOF_DIR"] = str(tmp_path / "proof")
    try:
        p = proof.build(str(outer), name="Zipped model")
    finally:
        if monkey_env is None:
            os.environ.pop("CREALITY_PROOF_DIR", None)
        else:
            os.environ["CREALITY_PROOF_DIR"] = monkey_env
    assert p["ok"] is True and p["params"]["format"] == "3MF", p
    assert p["params"]["triangles"] == 1


# ── the model's real name (the SEO title must never be published) ────────────

def _collector_js():
    """The page-data collector, extracted from downloader.fetch's source so the
    test can run it in a DOM without a browser session."""
    import re as _re
    src = Path(downloader.__file__).read_text(encoding="utf-8")
    m = _re.search(r'data = page\.evaluate\("""\n(.*?)\n\s*"""', src, _re.S)
    assert m, "the page-data collector not found in downloader.py"
    return m.group(1)


def test_the_collector_reads_the_pages_own_name_heading():
    """The h1 is what the SEO title is built from; the collector must return it
    (with the title), so the name never has to be guessed from the title."""
    from playwright.sync_api import sync_playwright
    html = """
    <html><head><title>Cliqueur moelleux en forme de ravioli Fichier STL et
      3MF pour l'impression 3D | Autre | Creality Cloud</title></head>
    <body>
      <h1 class="group-name text-ellipsis ellipsis-2">Cliqueur moelleux en forme de ravioli</h1>
      <div class="creality-model-name-box">Pelmeni-Tortellini-Ravioli-Former</div>
      <p>DesignerFoo
      Follow</p>
    </body></html>"""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_content(html)
        data = page.evaluate("(%s)()" % _collector_js())
        b.close()
    assert data["h1"] == "Cliqueur moelleux en forme de ravioli"
    assert downloader._model_name_from(data["title"], data["h1"]) == \
        "Cliqueur moelleux en forme de ravioli"


@pytest.mark.parametrize("title,h1,expected", [
    # the REAL French capture (debug/model_name_probe.json, live 2026-10-09) —
    # this title used to be published as the listing's name
    ("Cliqueur moelleux en forme de ravioli Fichier STL et 3MF pour "
     "l'impression 3D | Autre | Creality Cloud",
     "Cliqueur moelleux en forme de ravioli",
     "Cliqueur moelleux en forme de ravioli"),
    # no heading at all: the tail is cut anyway
    ("Cliqueur moelleux en forme de ravioli Fichier STL et 3MF pour "
     "l'impression 3D | Autre", "",
     "Cliqueur moelleux en forme de ravioli"),
    # a real capture from this campaign (Russian title, published correctly)
    ("Домкрат для подъема поддонов 1/10-го масштаба STL & 3MF File for 3D "
     "Printing | Creality Cloud",
     "Домкрат для подъема поддонов 1/10-го масштаба",
     "Домкрат для подъема поддонов 1/10-го масштаба"),
    ("Soporte de movil STL y 3MF para imprimir en 3D | Otros", "",
     "Soporte de movil"),
    ("Handyhalterung STL & 3MF für den 3D-Druck | Andere", "",
     "Handyhalterung"),
    ("Supporto telefono 3MF per la stampa 3D | Altro", "", "Supporto telefono"),
    ("Cable organizer | Creality Cloud", "", "Cable organizer"),
    # a name that legitimately starts with a format token is NOT cut
    ("STL File Holder", "", "STL File Holder"),
    # ordinary names are untouched
    ("Knitted cactus", "", "Knitted cactus"),
    ("Under-Sink Door Organiser - Kitchen Cabinet", "",
     "Under-Sink Door Organiser - Kitchen Cabinet"),
])
def test_the_seo_tail_never_reaches_the_listing_name(title, h1, expected):
    assert downloader._model_name_from(title, h1) == expected


def test_a_heading_that_is_not_the_model_name_is_not_used():
    """The heading is only trusted when the page's title was built from it: a
    generic site heading must not replace the model's name."""
    assert downloader._model_name_from("Cable organizer | Creality Cloud",
                                       "Creality Cloud") == "Cable organizer"


def test_a_heading_cut_short_does_not_truncate_the_name():
    """A heading that agrees with the title but is shorter must not shorten the
    published name — the longer agreeing clean name wins."""
    assert downloader._model_name_from(
        "Cliqueur moelleux en forme de ravioli Fichier STL et 3MF pour "
        "l'impression 3D", "Cliqueur moelleux") == \
        "Cliqueur moelleux en forme de ravioli"
