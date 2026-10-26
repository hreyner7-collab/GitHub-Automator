"""MakerWorld publish plan — pure logic, no browser, no Creality code.

Every literal in this file that names a page control was read off the live
MakerWorld wizard on 2026-10-25 (see MAKERWORLD-FLOW-CONFIG.md for the
measurements and MAKERWORLD-STUDY/ for the raw dumps). Anything derived rather
than measured is labelled UNMEASURED in a comment, and the driver re-reads
MakerWorld's own answer to it before publishing.

The Creality target lives in uploader.py / ledger.py and is deliberately not
imported here: the two publish targets stay separate.
"""
from __future__ import annotations

import hashlib
import os
import re

ORIGIN = "https://makerworld.com"

# ── entry points (measured: the nav "Upload" popover's own hrefs) ───────────
ENTRY_ORIGINAL = "/en/my/models/publish?type=original"
ENTRY_REMIX = "/en/my/models/publish?type=remix"
ENTRY_LASER_CUT = "/en/my/laser-and-cut-models/publish?type=original"
ENTRIES = {"original": ENTRY_ORIGINAL, "remix": ENTRY_REMIX, "laser-cut": ENTRY_LASER_CUT}


def entry_url(kind: str = "remix") -> str:
    return ORIGIN + ENTRIES.get((kind or "remix").lower(), ENTRY_REMIX)


def draft_edit_url(draft_id) -> str:
    return f"{ORIGIN}/en/my/models/drafts/{draft_id}/edit"


def draft_id_from_url(url: str) -> str:
    """The draft id a wizard URL carries ('' when it is not a draft edit page)."""
    m = re.search(r"/my/models/drafts/(\d+)", url or "")
    return m.group(1) if m else ""


def design_url(model_id, slug: str = "") -> str:
    """A published MakerWorld listing. The fetch side already builds the same
    shape from the numeric id it scrapes (discover.py link_re, downloader.py)."""
    slug = re.sub(r"[^A-Za-z0-9-]+", "-", (slug or "")).strip("-").lower()
    return f"{ORIGIN}/en/models/{model_id}" + (f"-{slug}" if slug else "")


def design_id_from_url(url: str) -> str:
    m = re.search(r"/models/(\d+)", url or "")
    return m.group(1) if m else ""


# ── step 1 (measured) ───────────────────────────────────────────────────────
# The wizard opens on a MakerLab/2D branch; the 3D branch is a text control.
STL_BRANCH_TEXT = "I have STL/CAD files or other types of 3MF files"
# accept="" of the model-file input, read from the live page
MODEL_ACCEPT_EXTS = (".3ds", ".amf", ".dwg", ".dxf", ".f3d", ".factory", ".fcstd", ".iges",
                     ".ipt", ".obj", ".ply", ".rsdoc", ".scad", ".shape", ".shapr", ".skp",
                     ".sldasm", ".sldprt", ".slvs", ".step", ".stl", ".stp", ".studio3",
                     ".zip", ".3mf", ".stpz")
NEXT_STEP_BUTTON = "Next Step"

# ── step 2 (measured) ───────────────────────────────────────────────────────
STEP2_URL_PREFIX = "/en/my/models/drafts/"
SAVE_DRAFT_BUTTON = "Save to draft"
PUBLISH_BUTTON = "Publish"
PREVIOUS_BUTTON = "Previous"

# file slots, by the text next to each hidden <input type=file> (measured)
SLOT_COVER_43 = "4:3 Cover"
SLOT_COVER_34 = "3:4 Cover is prioritized in App"
SLOT_VIDEO = "Add video"
SLOT_REAL_PHOTOS = "Real life photos"
SLOT_MODEL_PHOTOS = "Model Pictures"
SLOT_ORIGIN_ADD = "Add"

# Model Origin (remix source) — measured placeholder of the search box
SOURCE_INPUT_PLACEHOLDER = "Paste the URL or utilize the search function for models on MakerWorld"
# measured warning that sits under Model Origin; the licence rule MakerWorld states itself
ND_WARNING = ('Please make sure the source model\'s license does NOT contain "NoDerivatives" '
              "attribute")
# measured description hint — a remix that does not explain itself is removed
REMIX_DETAIL_REQUIRED = "Please detail how you remixed the original model."

# radio groups, exactly as the live form labels them (measured)
MODEL_SOURCE_RADIOS = ("Original", "Remix", "Share")
LICENSE_Q_ADAPT = "Allow adaptations of your work to be shared?"
LICENSE_Q_COMMERCIAL = "Allow commercial uses of your work?"
LICENSE_Q_REDISTRIBUTE = "Allow sharing or redistributing of your work or its derivatives?"
LICENSE_Q_BAMBU_HANDY = "Is this model supported only for printing through Bambu Handy?"
ADAPT_OPTIONS = ("Yes", "Yes, as long as others share in the same way",
                 "Yes, but any derivative works must be published exclusively on MakerWorld",
                 "Yes, any derivative works must be published exclusively on MakerWorld and "
                 "comply with Standard Digital File License – Community Use (SDFL‑C)",
                 "No")
YES_NO = ("Yes", "No")
VISIBILITY_RADIOS = ("Public", "Private")

# limits measured on the live form (labels "0/50", "Tags (0 / 50)", "0/100")
MAX_NAME = 50
MAX_TAG_INPUT = 100
MAX_TAGS = 50
MIN_REAL_PHOTOS = 3
MAX_PHOTOS = 16
IMAGE_ACCEPT = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# Measured 2026-10-25 on the live draft: the form asks TWO licence questions —
# adaptations and commercial use. The redistribute question in the study dump
# is no longer rendered, so it must not be answered from a stale map.
# UNMEASURED: which answer combination renders which licence name is MakerWorld's
# own mapping. The table below is the ordinary CC reading; the driver never
# trusts it — it reads the licence name MakerWorld itself renders and refuses if
# that name is not the source licence (see license_name_matches). The cc0 row is
# the one mapping that IS measured, and it measures badly: Yes/Yes on a CC0
# source renders "CC BY", so license_plan refuses that code rather than answer it.
LICENSE_PLAN = {
    "cc0": {LICENSE_Q_ADAPT: "Yes", LICENSE_Q_COMMERCIAL: "Yes"},
    "zero": {LICENSE_Q_ADAPT: "Yes", LICENSE_Q_COMMERCIAL: "Yes"},
    "publicdomain": {LICENSE_Q_ADAPT: "Yes", LICENSE_Q_COMMERCIAL: "Yes"},
    "by": {LICENSE_Q_ADAPT: "Yes", LICENSE_Q_COMMERCIAL: "Yes"},
    "by-sa": {LICENSE_Q_ADAPT: ADAPT_OPTIONS[1], LICENSE_Q_COMMERCIAL: "Yes"},
}
# Refused outright, so they have no plan:
#  · ND — MakerWorld's remix form says a NoDerivatives source is not allowed;
#  · NC — this account publishes on a platform that pays per download, which an
#    NC source does not license (the same rule that got NC re-uploads taken
#    down on Creality on 2026-10-09);
#  · the site's own Standard Digital File License, which MakerWorld also names
#    in the warning under Model Origin.
REFUSED_CODES = ("standard", "sdl", "sdlf")
# Codes whose licence the live form cannot render at all, measured rather than
# assumed — see license_plan, which refuses these before a browser opens.
FORM_UNREPRESENTABLE = ("cc0", "zero", "publicdomain")

_CC_TOKEN = re.compile(r"(?i)\bCC[\s-]?0\b|public[\s-]?domain|"
                       r"CC[\s-]?BY(?:[\s-]?(?:NC|SA|ND)){0,2}")


def cc_code(license_raw: str) -> str:
    """The CC code of the SOURCE licence ('' when the text names none)."""
    raw = (license_raw or "").strip()
    # The public design API reports the licence as a bare token ("BY-SA"), not
    # as a URL or a "CC BY-SA" sentence.
    if re.fullmatch(r"(?i)by(?:-(?:nc|sa|nd)){0,2}", raw):
        return raw.lower()
    if re.fullmatch(r"(?i)(?:cc[\s-]?0|0|zero|public[\s-]?domain)", raw):
        return "cc0"
    t = re.sub(r"\s+", "-", (license_raw or "").lower().replace("_", "-"))
    m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z0-9\-]+)", t)
    if m:
        code = m.group(1).split("/")[0]
    else:
        m2 = re.search(r"\bcc-?(by(?:-(?:nc|sa|nd)){0,2}|0|zero)\b", t)
        code = m2.group(1) if m2 else ""
    code = code.strip("-")
    if code in ("0", "zero", "public-domain", "publicdomain"):
        code = "cc0"
    if not code and re.search(r"(?i)public[\s-]?domain", t):
        # "Public Domain Mark 1.0" is how MakerWorld words CC0 on some listings;
        # it is the same licence and the form renders it the same wrong way.
        code = "cc0"
    return code


def license_plan(license_raw: str) -> dict:
    """{code, refused, answers: {question label: option label}} for the form.

    A licence this pipeline may not republish comes back refused, with the
    sentence that says why, and no answers — the driver stops there rather than
    picking an option that would mis-state the source's licence.
    """
    if re.search(r"(?i)standard\s+digital\s+file\s+license|\bsdlf?\b", license_raw or ""):
        return {"code": "standard", "refused": True, "answers": {},
                "why": "the source is published under MakerWorld's Standard Digital File "
                       "License, which does not license a re-upload"}
    code = cc_code(license_raw)
    if code in FORM_UNREPRESENTABLE:
        # Measured 2026-10-26 (artifacts/makerworld_auto_20261026-161524.json):
        # answering the form's two questions the way CC0 reads renders the badge
        # "CC BY". There is no answer that gets CC0 back, and publishing a public
        # domain source under an attribution licence is a statement about the
        # source that is not true — so this stops before the browser opens
        # instead of after a three-minute run.
        return {"code": code, "refused": True, "answers": {},
                "why": f"the source licence is {code.upper()}, and MakerWorld's remix form has no "
                       "answer that renders it: its two licence questions came back as CC BY. "
                       "Refusing to declare a licence the source does not name"}
    if code in REFUSED_CODES or code.endswith("-nd"):
        return {"code": code, "refused": True, "answers": {},
                "why": f"the source licence is {code.upper() or 'unreadable'} and MakerWorld's "
                       f"remix form itself warns that a NoDerivatives licence is not allowed "
                       f"({ND_WARNING})"}
    if "-nc" in code:
        return {"code": code, "refused": True, "answers": {},
                "why": f"the source licence is {code.upper()}: noncommercial, and this account "
                       "publishes on a platform that pays creators per download"}
    answers = LICENSE_PLAN.get(code)
    if answers is None:
        # No licence the page names at all. Silence is not a grant: the safe
        # answer is CC BY's own terms, and the driver still verifies the badge.
        answers = dict(LICENSE_PLAN["by"])
    return {"code": code, "refused": False, "answers": dict(answers), "why": ""}


_FULL_MOD = {"SA": "SHARE ALIKE", "NC": "NONCOMMERCIAL", "ND": "NODERIVATIVES"}


def expected_license_names(code: str) -> list[str]:
    """The licence NAMES MakerWorld may legitimately render for this source CC
    code — the short badge ("CC BY-SA") and the long link text the live form
    shows ("Creative Commons Attribution-Share Alike"). Empty list = cannot be
    checked."""
    c = (code or "").lower()
    if c in ("cc0", "zero", "publicdomain"):
        return ["CC0", "Public Domain"]
    if not c:
        return []
    mods = [p for p in c.split("-")[1:] if p in ("nc", "sa", "nd")]
    short = "CC BY" + "".join("-" + m.upper() for m in mods)
    long = "Creative Commons Attribution" + "".join(
        "-" + _FULL_MOD[m.upper()] for m in mods if m.upper() in _FULL_MOD)
    return [short, long]


def license_name_matches(read: str, code: str) -> bool:
    """Whether the licence MakerWorld rendered (short badge or long CC name)
    is the one the source grants. Anything stricter, looser or unreadable is
    a mismatch."""
    want = expected_license_names(code)
    if not want:
        return False
    text = re.sub(r"[^A-Z0-9]+", "", (read or "").upper())
    if not text:
        return False
    c = (code or "").lower()
    if c in ("cc0", "zero", "publicdomain"):
        return "CC0" in text or "PUBLICDOMAIN" in text
    long_forms = [re.sub(r"[^A-Z0-9]+", "", n.upper())
                  for n in want if n.upper().startswith("CREATIVE")]
    if any(n and n in text for n in long_forms):
        return True
    # short form: CCBY + the modifier letters, with any version digits ignored
    m = re.match(r"^CCBY([A-Z]*)(?:[0-9].*)?$", text)
    if not m:
        return False
    mods = [p for p in c.split("-")[1:] if p in ("nc", "sa", "nd")]
    suffix = m.group(1)
    got = re.findall(r"NC|SA|ND", suffix)
    return (sorted(got) == sorted(p.upper() for p in mods)
            and len(suffix) == sum(len(x) for x in got))


def clamp_name(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "")).strip()
    return n[:MAX_NAME]


def plan_tags(tags, source_name: str = "") -> list[str]:
    """Tags the form will accept: ≤50 of them, ≤100 chars in the box, and the
    source model's own name first so the listing stays findable by it."""
    out: list[str] = []
    seen = set()
    for t in ([source_name] if source_name else []) + [x for x in (tags or []) if x]:
        clean = re.sub(r"\s+", " ", str(t)).strip()[:MAX_TAG_INPUT].strip()
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        out.append(clean)
        if len(out) >= MAX_TAGS:
            break
    return out


def plan_description(description: str, source_url: str = "", source_name: str = "") -> str:
    """The source listing's own description, copied as-is.

    It is not decorated with a generated remix preamble or a credit line: the
    source is already declared in Model Origin, where MakerWorld reads it. The
    user asked for the uploaded listing to carry the original model's own text.
    """
    return re.sub(r"\s*\n{3,}", "\n\n", (description or "").strip())


def _norm_words(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def pick_source_row(rows, source_name: str = "", design_id: str = "") -> int:
    """The suggestion row that IS the source model, or -1.

    Same lesson the Creality form taught (UPLOAD-FLOW-CONFIG.md): rows found by
    a prefix or by position pick a look-alike ("Dumpling Clicker" instead of
    "dumpling squishy clicker v2") and register the WRONG source. So: exact
    name, then name as a whole leading phrase, then the numeric design id.
    Anything else is -1 and the publish is refused rather than guessed.
    """
    if not rows:
        return -1
    want = _norm_words(source_name)
    if want:
        for i, r in enumerate(rows):
            if _norm_words(r) == want:
                return i
        for i, r in enumerate(rows):
            t = _norm_words(r)
            if t.startswith(want) and (len(t) == len(want) or not t[len(want)].isalnum()):
                return i
    if design_id:
        for i, r in enumerate(rows):
            if str(design_id) in re.sub(r"[^0-9]+", " ", r).split():
                return i
    return -1


def file_fingerprint(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def is_model_file(path: str) -> bool:
    return (os.path.splitext(path)[1].lower() in MODEL_ACCEPT_EXTS
            if path else False)


def usable_images(paths, must_exist: bool = True) -> list[str]:
    """Only files MakerWorld's image slots take (measured accept= lists)."""
    out = []
    for p in paths or []:
        if not p:
            continue
        if must_exist and not os.path.exists(p):
            continue
        if os.path.splitext(str(p))[1].lower() in IMAGE_ACCEPT:
            out.append(str(p))
    return out


def split_photo_sets(paths, cover_want: int = 2) -> tuple[list[str], list[str]]:
    """Split a drawn set into (covers, print photos) for the form's two places.

    The 4:3 and 3:4 cover slots, the gallery and the "real photos of your print"
    slot are separate controls, and the same file in two of them is what came
    back as "Please set the model cover". So the sets stay apart, and a set that
    is too small to cover both is reported by leaving one side short rather than
    by duplicating a picture across slots.
    """
    files = list(paths or [])
    if len(files) <= cover_want + MIN_REAL_PHOTOS - 1:
        cover_want = max(1, len(files) - MIN_REAL_PHOTOS) if len(files) > MIN_REAL_PHOTOS else 1
    return files[:cover_want], files[cover_want:]


def prepare_print_photos(photo_paths, background: str = "",
                         recolour: str = "") -> dict:
    """Tidy the photographs of your own print before they go on the listing.

    Both options are opt-in and act only on photos that already exist:
    `background` is the backdrop the editor composites onto ("keep" leaves the
    workbench in the shot exactly as it was), `recolour` shifts the printed
    plastic to another filament colour. Neither one creates a picture — the
    object, its geometry and every detail in it are the ones you photographed.

    With no option set the list is returned untouched. An image the editor's QA
    refuses keeps its ORIGINAL file and is named in `notes`, so a restyle can
    never quietly drop the listing below MakerWorld's 3-photo minimum.
    """
    photos = usable_images(photo_paths)
    if not (background or "").strip() and not (recolour or "").strip():
        return {"photos": photos, "notes": [], "styled": False}

    import editor                      # heavy (cv2/numpy) and only needed here

    style, colour = editor.check_options((background or "keep").strip(), recolour)
    notes = []
    out = []
    for path in photos:
        try:
            edited = editor.edit_image(path, background=style, recolour=colour)
        except Exception as e:          # noqa: BLE001 — refuse one, keep the rest
            notes.append(f"{os.path.basename(path)}: left as shot (the finisher "
                         f"refused it: {e})")
            out.append(path)
            continue
        notes.append(f"{os.path.basename(path)} -> {os.path.basename(edited)}")
        out.append(edited)
    return {"photos": out, "notes": notes, "styled": True,
            "background": style, "recolour": colour}


def validate_plan(name: str = "", description: str = "", source_url: str = "",
                  model_file: str = "", cover_images=(), print_photos=(),
                  remix_source_url: str = "", license_raw: str = "",
                  kind: str = "remix", dry_run: bool = False,
                  photos_drawn: int = 0) -> dict:
    """Everything that would make MakerWorld's own form refuse the publish,
    found before a browser opens. {ok, blockers:[…], warnings:[…], plan:{…}}.

    Print photos are checked separately from the covers because MakerWorld asks
    for two different things and only one of them is what this pipeline's editor
    produces: the covers are renders, the "Real life photos" slot wants ≥3
    photographs of the printed object (measured: 'Upload ≥3 photos of the
    printed model, covering 3 views').

    `photos_drawn` says the pictures do not exist yet — they are drawn when the
    publish runs — so the plan checks the set that will really be split between
    the two slots instead of the empty file lists the page sends in that mode.
    """
    blockers: list[str] = []
    warnings: list[str] = []

    lp = license_plan(license_raw)
    if lp["refused"]:
        blockers.append("Refusing to publish: " + lp["why"])

    if not model_file or not os.path.exists(model_file):
        blockers.append("no model file to attach (step 1 will not advance without one)")
    elif not is_model_file(model_file):
        blockers.append(f"{os.path.basename(model_file)} is not a format MakerWorld's step 1 "
                        "accepts (measured accept= list)")

    title = clamp_name(name)
    if not title:
        blockers.append("the model has no name; the form's Model Name is required")

    covers = usable_images(cover_images)
    photos = usable_images(print_photos)
    if photos_drawn:
        covers, photos = split_photo_sets([f"drawn{i}.jpg" for i in range(int(photos_drawn))])
    if len(covers) < 1:
        blockers.append("no cover image to upload (the form shows two required cover slots, "
                        f"4:3 and 3:4; accepts {', '.join(IMAGE_ACCEPT)})")

    gap = (f"only {len(photos)} real print photo(s); MakerWorld requires at least "
           f"{MIN_REAL_PHOTOS} photographs of the printed model. The pipeline's own "
           "edited studio renders are NOT print photos — supply photos of a print "
           "you actually made, or do not publish this model.")
    if photos_drawn:
        gap = (f"only {len(photos)} of the {MIN_REAL_PHOTOS} pictures the photo slot "
               "needs would come back from the drawer")
    if len(photos) < MIN_REAL_PHOTOS:
        # A dry run is allowed to walk the wizard without them; a publish is not.
        (warnings if dry_run else blockers).append(gap)
    if len(covers) and len(set(covers) & set(photos)) == len(covers):
        warnings.append("the cover images are also being sent as the real-life photos; "
                        "MakerWorld asks for photos of the print in the second slot")

    if not source_url:
        warnings.append("no source URL — the listing will carry nothing to credit")
    if not remix_source_url and (kind or "remix").lower() == "remix":
        blockers.append("this is a ?type=remix publish but no MakerWorld design was named as "
                        "the source (the Model Origin 'Add' control) — MakerWorld only accepts "
                        "a remix whose base is a design already on MakerWorld")
    elif remix_source_url and (kind or "remix").lower() == "remix" \
            and "makerworld.com" not in remix_source_url.lower():
        # measured wording of the source box: '… for models on MakerWorld'
        blockers.append(f"{remix_source_url} is not a MakerWorld design, and MakerWorld's remix "
                        "source box only takes models already on MakerWorld — a Thingiverse or "
                        "Printables original must be published with the original-model path or "
                        "imported through MakerWorld's own third-party import")

    desc = plan_description(description)
    if not desc:
        blockers.append("the source listing has no description and MakerWorld's Description "
                        "field is required — there is nothing original to copy")
    elif len(desc) < 40:
        warnings.append("the source description is short; MakerWorld's remix form asks the "
                        "listing to explain how it was remixed, so review may be stricter")

    return {"ok": not blockers, "blockers": blockers, "warnings": warnings,
            "plan": {"name": title, "description": desc, "license": lp,
                     "covers": covers, "print_photos": photos,
                     "model_file": model_file, "remix_source_url": remix_source_url,
                     "source_url": source_url}}
