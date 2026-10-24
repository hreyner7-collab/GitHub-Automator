"""Seeded parameters for the eight original household families.

Every number here is a design input, chosen inside a printable band. The seed
is derived from the family plus a variant number, so a spec can always be
rebuilt byte-for-byte; "give me another one" changes the variant, not the rules.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, asdict

from . import names

FAMILIES = {
    "cable_clip": "Cable Clip",
    "phone_stand": "Phone Stand",
    "wall_hook": "Wall Hook",
    "desk_organiser": "Desk Organiser",
    "parts_tray": "Parts Tray",
    "cup_holder": "Cup Holder",
    "clothes_peg": "Clothes Peg",
    "swab_holder": "Cotton Bud Holder",
}

PALETTE = [
    ("Terracotta", "#BF604E"),
    ("Sage", "#8FA27E"),
    ("Slate Blue", "#5C7295"),
    ("Mustard", "#C99A4E"),
    ("Warm White", "#DED6C8"),
    ("Teal", "#4E8C8C"),
    ("Charcoal", "#4A4D51"),
    ("Forest", "#4C6B57"),
]


@dataclass(frozen=True)
class ModelSpec:
    family: str
    variant: int
    seed: int
    params: dict
    title: str
    colour_name: str
    colour_hex: str

    @property
    def label(self) -> str:
        return FAMILIES[self.family]

    @property
    def key(self) -> str:
        return "%s-%02d-%08x" % (self.family, self.variant, self.seed)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        d["label"] = self.label
        return d


def seed_for(family: str, variant: int) -> int:
    if family not in FAMILIES:
        raise KeyError("unknown family %r" % family)
    h = hashlib.sha256(("%s:%d" % (family, variant)).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _cable_clip(rng: random.Random) -> dict:
    return {
        "cable_d": rng.choice([4.0, 5.0, 6.0, 8.0, 10.0]),
        "wall": round(rng.uniform(2.6, 3.4), 2),
        "width": round(rng.uniform(13.0, 20.0), 1),
        "slot_ratio": round(rng.uniform(0.45, 0.60), 2),
        "base_margin": round(rng.uniform(2.0, 4.0), 1),
        "base_t": round(rng.uniform(2.4, 3.2), 2),
    }


def _phone_stand(rng: random.Random) -> dict:
    return {
        "width": round(rng.uniform(74.0, 98.0), 1),
        "depth": round(rng.uniform(82.0, 98.0), 1),
        "angle": round(rng.uniform(58.0, 69.0), 1),
        "base_t": round(rng.uniform(4.0, 5.5), 2),
        "back_t": round(rng.uniform(5.5, 7.0), 1),
        "lip_t": round(rng.uniform(5.0, 7.5), 1),
        "lip_h": round(rng.uniform(11.0, 16.0), 1),
        "groove_w": round(rng.uniform(16.0, 24.0), 1),
        "cable_slot": rng.random() < 0.75,
        "cable_w": round(rng.uniform(10.0, 14.0), 1),
    }


def _wall_hook(rng: random.Random) -> dict:
    plate_h = round(rng.uniform(68.0, 92.0), 1)
    return {
        "width": round(rng.uniform(22.0, 34.0), 1),
        "plate_h": plate_h,
        "plate_t": round(rng.uniform(4.5, 6.0), 2),
        "arm_z": round(plate_h * rng.uniform(0.55, 0.66), 1),
        "reach": round(rng.uniform(52.0, 76.0), 1),
        "hook_r": round(rng.uniform(12.0, 18.0), 1),
        "arm_t": round(rng.uniform(7.0, 9.5), 1),
        "gusset_h": round(rng.uniform(12.0, 18.0), 1),
        "screw_d": 4.6,
    }


def _desk_organiser(rng: random.Random) -> dict:
    return {
        "width": round(rng.uniform(104.0, 140.0), 1),
        "depth": round(rng.uniform(72.0, 98.0), 1),
        "height": round(rng.uniform(46.0, 72.0), 1),
        "wall": round(rng.uniform(2.6, 3.2), 2),
        "base_t": round(rng.uniform(2.6, 3.4), 2),
        "bays": rng.choice([3, 4]),
        "divider": round(rng.uniform(2.0, 2.8), 2),
        "corner_r": round(rng.uniform(9.0, 15.0), 1),
        "notch": rng.random() < 0.8,
    }


def _parts_tray(rng: random.Random) -> dict:
    return {
        "width": round(rng.uniform(92.0, 128.0), 1),
        "depth": round(rng.uniform(70.0, 96.0), 1),
        "height": round(rng.uniform(28.0, 40.0), 1),
        "wall": round(rng.uniform(2.4, 2.8), 2),
        "base_t": round(rng.uniform(2.4, 3.0), 2),
        "cols": rng.choice([2, 3]),
        "rows": 2,
        "corner_r": round(rng.uniform(11.0, 17.0), 1),
        "rim_w": round(rng.uniform(2.0, 3.0), 1),
    }


def _cup_holder(rng: random.Random) -> dict:
    return {
        "cup_d": round(rng.uniform(68.0, 86.0), 1),
        "height": round(rng.uniform(70.0, 94.0), 1),
        "wall": round(rng.uniform(2.8, 3.4), 2),
        "base_t": round(rng.uniform(3.0, 4.0), 2),
        "slot_w": round(rng.uniform(24.0, 32.0), 1),
        "slot_h": round(rng.uniform(44.0, 58.0), 1),
        "flutes": rng.choice([6, 8, 10]),
        "drains": rng.choice([0, 3, 4, 6]),
    }


def _clothes_peg(rng: random.Random) -> dict:
    return {
        "length": round(rng.uniform(62.0, 76.0), 1),
        "width": round(rng.uniform(8.0, 12.0), 1),
        "arm_t": round(rng.uniform(2.4, 3.0), 2),
        "gap": round(rng.uniform(2.2, 2.8), 2),
        "loop_r": round(rng.uniform(13.0, 18.0), 1),
        "teeth": rng.choice([2, 2, 3]),
    }


def _swab_holder(rng: random.Random) -> dict:
    return {
        "diameter": round(rng.uniform(56.0, 74.0), 1),
        "height": round(rng.uniform(66.0, 88.0), 1),
        "wall": round(rng.uniform(2.8, 3.4), 2),
        "base_t": round(rng.uniform(3.0, 3.8), 2),
        "window_w": round(rng.uniform(26.0, 34.0), 1),
        "window_h": round(rng.uniform(30.0, 42.0), 1),
        "window_z": round(rng.uniform(12.0, 18.0), 1),
        "flutes": rng.choice([6, 8, 10]),
        "notch": rng.random() < 0.7,
    }


_SAMPLERS = {
    "cable_clip": _cable_clip,
    "phone_stand": _phone_stand,
    "wall_hook": _wall_hook,
    "desk_organiser": _desk_organiser,
    "parts_tray": _parts_tray,
    "cup_holder": _cup_holder,
    "clothes_peg": _clothes_peg,
    "swab_holder": _swab_holder,
}


def nominal_height(family: str, params: dict) -> float:
    """The design's main dimension, used in the title and contact sheet."""
    p = params
    if family == "cable_clip":
        return p["cable_d"]
    if family == "phone_stand":
        return p["angle"]
    if family == "wall_hook":
        return p["reach"]
    if family == "desk_organiser":
        return p["bays"]
    if family == "parts_tray":
        return p["cols"] * p["rows"]
    if family == "cup_holder":
        return p["cup_d"]
    if family == "clothes_peg":
        return p["length"]
    if family == "swab_holder":
        return p["height"]
    raise KeyError(family)


def make_spec(family: str, variant: int) -> ModelSpec:
    if family not in FAMILIES:
        raise KeyError("unknown family %r" % family)
    seed = seed_for(family, variant)
    rng = random.Random(seed)
    params = _SAMPLERS[family](rng)
    # One colour per family at variant 1, then rotate: two neighbouring models
    # never share a filament colour, which is what makes a shelf of listings
    # read as a range rather than eight copies.
    order = list(FAMILIES)
    colour_name, colour_hex = PALETTE[(order.index(family) + variant - 1)
                                     % len(PALETTE)]
    title = names.title_for(family, params, rng)
    return ModelSpec(family=family, variant=variant, seed=seed, params=params,
                     title=title, colour_name=colour_name, colour_hex=colour_hex)


def catalogue(per_family: int = 1) -> list[ModelSpec]:
    return [make_spec(f, v) for f in FAMILIES for v in range(1, per_family + 1)]
