"""Titles and listing copy for original work.

Nothing here names another designer, another platform, or a trademark. The
copy states plainly that the model is original and that the photos are renders
of the same STL.
"""
from __future__ import annotations

import re

ADJECTIVES = {
    "cable_clip": ["Arc", "Loop", "Grip", "Ridge", "Cove", "Bend", "Tuck", "Snap"],
    "phone_stand": ["Lean", "Wedge", "Perch", "Rest", "Alder", "Easel", "Slope"],
    "wall_hook": ["Juno", "Nook", "Bracken", "Cove", "Perch", "Haven", "Cleat"],
    "desk_organiser": ["Studio", "Grid", "Bureau", "Dock", "Quarter", "Atlas"],
    "parts_tray": ["Sort", "Cell", "Tinker", "Nest", "Bin", "Ledger", "Batch"],
    "cup_holder": ["Halo", "Cozy", "Rim", "Hollow", "Girth", "Collar"],
    "clothes_peg": ["Pivot", "Spring", "Fold", "Clasp", "Tender", "Pinch"],
    "swab_holder": ["Quill", "Reed", "Linen", "Bath", "Stem", "Pebble"],
}

BANNED_WORDS = ("creality", "iphone", "apple", "disney", "lego", "nintendo",
                "star wars", "marvel", "pokemon", "makerworld", "thingiverse",
                "printables", "cults", "patreon", "bambu")


def _g(value: float) -> str:
    return ("%.1f" % value).rstrip("0").rstrip(".")


def title_for(family: str, params: dict, rng) -> str:
    adj = rng.choice(ADJECTIVES[family])
    p = params
    if family == "cable_clip":
        title = "%s Cable Clip - %s mm" % (adj, _g(p["cable_d"]))
    elif family == "phone_stand":
        title = "%s Phone Stand - %s°" % (adj, _g(p["angle"]))
    elif family == "wall_hook":
        title = "%s Wall Hook - %s mm Reach" % (adj, _g(p["reach"]))
    elif family == "desk_organiser":
        title = "%s Desk Organiser - %d Bays" % (adj, p["bays"])
    elif family == "parts_tray":
        title = "%s Parts Tray - %d Cells" % (adj, p["cols"] * p["rows"])
    elif family == "cup_holder":
        title = "%s Cup Holder - %s mm" % (adj, _g(p["cup_d"]))
    elif family == "clothes_peg":
        title = "%s Clothes Peg - %s mm" % (adj, _g(p["length"]))
    elif family == "swab_holder":
        title = "%s Cotton Bud Holder - %s mm" % (adj, _g(p["height"]))
    else:
        raise KeyError(family)
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0]
    return title


def clean_title(title: str) -> str:
    for bad in BANNED_WORDS:
        if bad in title.lower():
            raise ValueError("title names a trademark or marketplace: %r" % title)
    if not title.strip():
        raise ValueError("empty title")
    return title.strip()


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60].strip("-")


def feature_for(family: str, params: dict) -> str:
    p = params
    if family == "cable_clip":
        return ("The C mouth snaps over a %s mm cable; the flat foot takes an "
                "adhesive pad or a small screw." % _g(p["cable_d"]))
    if family == "phone_stand":
        return ("A %s° lean with a front lip and a charging-cable gap; the wide "
                "foot keeps it steady." % _g(p["angle"]))
    if family == "wall_hook":
        return ("A %s mm reach J-hook on a screw-fixed plate, with a gusset "
                "under the arm." % _g(p["reach"]))
    if family == "desk_organiser":
        return "%d open bays with a finger scoop at the front." % p["bays"]
    if family == "parts_tray":
        return "%d shallow cells in a low tray with a raised rim." % (
            p["cols"] * p["rows"])
    if family == "cup_holder":
        return ("A %s mm cup sleeve with a handle slot and drainage holes."
                % _g(p["cup_d"]))
    if family == "clothes_peg":
        return ("A one-piece flexure peg: squeeze the back and the jaws open. "
                "PETG gives the spring a longer life than PLA.")
    if family == "swab_holder":
        return ("A %s mm tub with an open side window, so the next bud is "
                "reachable without tipping the whole lot out." % _g(p["height"]))
    raise KeyError(family)


def context_note(family: str, params: dict) -> str:
    p = params
    if family == "cable_clip":
        return ("The first photo shows it on a desk holding a %s mm cable; the "
                "cable is a prop for scale, not included." % _g(p["cable_d"]))
    if family == "phone_stand":
        return ("The first photo shows a phone resting in the stand; the phone "
                "is a prop for scale, not included.")
    if family == "wall_hook":
        return ("The first photo shows it mounted on a wall; the wall and "
                "screws are props, not included.")
    return ("The first photo shows it on a desk; anything around it is a prop "
            "for scale, not included.")


def description_for(spec, dims: tuple[float, float, float],
                    print_note: str = "") -> str:
    L, W, H = dims
    clean_title(spec.title)
    note = print_note.strip() or ("Print settings are a starting point; the "
                                  "preview shows the modelled orientation.")
    return (
        "An original %s, designed for this listing - not copied, re-uploaded "
        "or re-photographed from another designer's model.\n\n"
        "%s\n\n"
        "Size: %s x %s x %s mm (measured from the STL).\n"
        "Print: 0.4 mm nozzle, 0.2 mm layers, 3 perimeters, 15%% infill, "
        "PLA or PETG.\n%s\n\n"
        "%s\n"
        "The photos are renders of the exact STL in this download, printed "
        "colour %s.\n"
        "Licence: CC BY 4.0 - share and adapt with credit."
        % (spec.label.lower(), feature_for(spec.family, spec.params),
           _g(L), _g(W), _g(H), note,
           context_note(spec.family, spec.params), spec.colour_name)
    )


def print_note_for(family: str, supports: bool) -> str:
    if family == "wall_hook":
        return ("Print lying on its back, flat side on the bed, so the plate "
                "and hook need no supports.")
    if family == "clothes_peg":
        return ("Print flat. Flex the loop gently a few times before the "
                "first use.")
    if supports:
        return ("Printed as modelled this part has some overhangs; a slicer's "
                "supports may be needed under the listed areas.")
    return "Prints at the modelled orientation without supports."
