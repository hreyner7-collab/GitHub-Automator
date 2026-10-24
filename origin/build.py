"""Geometry for the eight original families.

Everything is built from primitives and 2D profiles here; no downloaded mesh
is read, imported, resampled or otherwise used as an input. Manifold's boolean
engine is the only CSG backend, so a result that is not a valid solid fails
loudly instead of being sanded down into something printable.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from trimesh.transformations import rotation_matrix


class BuildError(RuntimeError):
    pass


# ── primitives ───────────────────────────────────────────────────────────────

def _valid(profile):
    p = profile.buffer(0)
    if p.is_empty or p.area <= 1e-6:
        raise BuildError("empty 2D profile")
    if p.geom_type != "Polygon":
        raise BuildError("profile is %s, not one polygon" % p.geom_type)
    return p


def rounded_rect(w: float, d: float, r: float, cx: float = 0.0,
                 cy: float = 0.0) -> Polygon:
    r = max(0.2, min(r, min(w, d) / 2 - 0.05))
    return _valid(box(cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2)
                  .buffer(-r, quad_segs=16).buffer(r, quad_segs=16))


def circle(cx: float, cy: float, r: float):
    return Point(cx, cy).buffer(r, quad_segs=64)


def extrude(profile, z0: float, z1: float) -> trimesh.Trimesh:
    p = _valid(profile)
    m = trimesh.creation.extrude_polygon(p, height=float(z1 - z0))
    m.apply_translation((0.0, 0.0, float(z0)))
    return m


def box3(cx: float, cy: float, cz: float, sx: float, sy: float,
         sz: float) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=(sx, sy, sz))
    m.apply_translation((cx, cy, cz))
    return m


def cyl(r: float, h: float, cx: float = 0, cy: float = 0, cz: float = 0,
        sections: int = 64) -> trimesh.Trimesh:
    m = trimesh.creation.cylinder(radius=r, height=h, sections=sections)
    m.apply_translation((cx, cy, cz))
    return m


def _clean(m: trimesh.Trimesh) -> trimesh.Trimesh:
    m.merge_vertices()
    m.remove_unreferenced_vertices()
    try:
        trimesh.repair.fix_normals(m)
    except Exception:
        pass
    return m


def union(*meshes) -> trimesh.Trimesh:
    meshes = [m for m in meshes if m is not None and len(m.faces)]
    if not meshes:
        raise BuildError("nothing to union")
    if len(meshes) == 1:
        return _clean(meshes[0])
    try:
        out = trimesh.boolean.union(meshes, engine="manifold")
    except Exception as e:
        raise BuildError("union failed: %s" % e) from e
    return _clean(out)


def difference(mesh: trimesh.Trimesh, *cutters) -> trimesh.Trimesh:
    cutters = [c for c in cutters if c is not None and len(c.faces)]
    if not cutters:
        return _clean(mesh)
    try:
        out = trimesh.boolean.difference([mesh] + cutters, engine="manifold")
    except Exception as e:
        raise BuildError("difference failed: %s" % e) from e
    return _clean(out)


def intersect(a: trimesh.Trimesh, b: trimesh.Trimesh) -> trimesh.Trimesh:
    try:
        out = trimesh.boolean.intersection([a, b], engine="manifold")
    except Exception as e:
        raise BuildError("intersection failed: %s" % e) from e
    return _clean(out)


def finish(mesh: trimesh.Trimesh, family: str) -> trimesh.Trimesh:
    mesh = _clean(mesh)
    lo, hi = mesh.bounds
    mesh.apply_translation((-(lo[0] + hi[0]) / 2,
                            -(lo[1] + hi[1]) / 2,
                            -lo[2]))
    mesh = _clean(mesh)
    if not mesh.is_watertight:
        raise BuildError("%s result is not watertight" % family)
    if not mesh.is_winding_consistent:
        raise BuildError("%s has inconsistent winding" % family)
    if mesh.volume <= 1.0:
        raise BuildError("%s volume is %.3f mm3" % (family, mesh.volume))
    return mesh


def _stand_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Profile built in XY and extruded along Z becomes display orientation:
    profile-Y up, extrusion across the product."""
    mesh.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
    return mesh


# ── 1. cable clip ────────────────────────────────────────────────────────────

def build_cable_clip(p: dict) -> trimesh.Trimesh:
    r_cable = p["cable_d"] / 2
    wall = p["wall"]
    base_t = p["base_t"]
    R = r_cable + wall
    overlap = 1.0                       # bury the ring in the foot, not tangent
    cy = base_t + R - overlap
    base_w = 2 * R + 2 * p["base_margin"]

    foot = rounded_rect(base_w, base_t, base_t * 0.48, cy=base_t / 2)
    ring = circle(0, cy, R).difference(circle(0, cy, r_cable))
    slot_w = p["cable_d"] * p["slot_ratio"]
    slot = box(-slot_w / 2, cy, slot_w / 2, cy + R + wall + 2)
    ring = ring.difference(slot)
    prof = _valid(unary_union([foot, ring]))
    m = extrude(prof, 0, p["width"])
    return finish(_stand_up(m), "cable_clip")


# ── 2. phone stand ───────────────────────────────────────────────────────────

def build_phone_stand(p: dict) -> trimesh.Trimesh:
    D, t = p["depth"], p["back_t"]
    base_t, lip_t, lip_h = p["base_t"], p["lip_t"], p["lip_h"]
    groove_back = lip_t + p["groove_w"]
    H = base_t + (D - t - groove_back) * math.tan(math.radians(p["angle"]))
    if H > 170:
        raise BuildError("phone stand would be %.0f mm tall" % H)

    base = Polygon([(0, 0), (D, 0), (D, base_t), (0, base_t)])
    lip = Polygon([(0, base_t), (lip_t, base_t),
                   (lip_t, base_t + lip_h - 1.2),
                   (lip_t - 1.2, base_t + lip_h), (0, base_t + lip_h)])
    # The back is a plate, not a solid wedge: the phone leans on its upper
    # face and the load runs straight down into the base. A solid wedge was
    # 394 cm3 (490 g) for the same phone; this is ~110 g and looks like a
    # folded sheet rather than a doorstop.
    A = np.array([groove_back - 2.0, base_t])
    B = np.array([D - t, H])
    u = (B - A) / float(np.linalg.norm(B - A))
    n = np.array([u[1], -u[0]])          # into the stand: down and back
    ramp = Polygon([tuple(A), tuple(B), tuple(B + n * t), tuple(A + n * t)])
    prof = unary_union([base, ramp, lip]).intersection(
        box(-50, 0, D + 50, H + 50))
    prof = _valid(prof)
    m = extrude(prof, 0, p["width"])

    if p["cable_slot"]:
        cw = p["cable_w"]
        cut = box3(0, 0, 0, lip_t + 4, lip_h + 4, cw)
        cut.apply_translation(((lip_t + 3) / 2 - 1.5,
                               base_t + lip_h / 2 - 0.5, p["width"] / 2))
        m = difference(m, cut)
    return finish(_stand_up(m), "phone_stand")


# ── 3. wall hook ─────────────────────────────────────────────────────────────

def build_wall_hook(p: dict) -> trimesh.Trimesh:
    plate_t, plate_h = p["plate_t"], p["plate_h"]
    reach, hook_r, arm_t = p["reach"], p["hook_r"], p["arm_t"]
    arm_z, gusset_h = p["arm_z"], p["gusset_h"]

    plate = Polygon([(0, 0), (plate_t, 0), (plate_t, plate_h - 4),
                     (plate_t - 4, plate_h), (0, plate_h)])
    pts = [(plate_t * 0.5, arm_z), (reach - hook_r, arm_z)]
    cx, cy = reach - hook_r, arm_z + hook_r
    for ang in np.linspace(-90.0, 55.0, 22):
        a = math.radians(float(ang))
        pts.append((cx + hook_r * math.cos(a), cy + hook_r * math.sin(a)))
    arm = LineString(pts).buffer(arm_t / 2, cap_style=1, join_style=1)
    gusset = Polygon([(plate_t - 1.0, arm_z - gusset_h),
                      (plate_t + reach * 0.45, arm_z - arm_t * 0.3),
                      (plate_t - 1.0, arm_z - arm_t * 0.3)])
    prof = _valid(unary_union([plate, arm, gusset]))
    m = extrude(prof, 0, p["width"])

    holes = []
    for y in (plate_h * 0.20, plate_h * 0.78):
        if abs(y - (arm_z - gusset_h)) < 6:
            y += 8
        through = cyl(p["screw_d"] / 2, plate_t + 10, sections=40)
        through.apply_transform(rotation_matrix(math.pi / 2, [0, 1, 0]))
        through.apply_translation((plate_t / 2, y, p["width"] / 2))
        recess = cyl(4.0, 2.2, sections=40)
        recess.apply_transform(rotation_matrix(math.pi / 2, [0, 1, 0]))
        recess.apply_translation((plate_t - 0.9, y, p["width"] / 2))
        holes += [through, recess]
    m = difference(m, *holes)
    return finish(_stand_up(m), "wall_hook")


# ── 4. desk organiser ────────────────────────────────────────────────────────

def _tray_shell(p: dict, height: float, cell_height: float,
                cols: int, rows: int):
    W, D = p["width"], p["depth"]
    wall, base_t = p["wall"], p["base_t"]
    outer = rounded_rect(W, D, p["corner_r"])
    inner = _valid(outer.buffer(-wall, quad_segs=16))
    shell = difference(extrude(outer, 0, height),
                       extrude(inner, base_t, height + 2))

    divs = []
    if cols > 1:
        x0, x1 = -W / 2 + wall, W / 2 - wall
        for i in range(1, cols):
            x = x0 + (x1 - x0) * i / cols
            divs.append(extrude(box(x - p.get("divider", 2.4) / 2,
                                    -D / 2 + wall - 0.5,
                                    x + p.get("divider", 2.4) / 2,
                                    D / 2 - wall + 0.5),
                                base_t, cell_height))
    if rows > 1:
        y0, y1 = -D / 2 + wall, D / 2 - wall
        for i in range(1, rows):
            y = y0 + (y1 - y0) * i / rows
            divs.append(extrude(box(-W / 2 + wall - 0.5,
                                    y - p.get("divider", 2.4) / 2,
                                    W / 2 - wall + 0.5,
                                    y + p.get("divider", 2.4) / 2),
                                base_t, cell_height))
    return union(shell, *divs) if divs else shell


def build_desk_organiser(p: dict) -> trimesh.Trimesh:
    H = p["height"]
    m = _tray_shell(p, H, H - 8, p["bays"], 1)
    if p["notch"]:
        scoop = cyl(10.0, p["wall"] + 8, sections=48)
        scoop.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
        scoop.apply_translation((0.0, -p["depth"] / 2 + p["wall"] / 2 - 0.5, H))
        m = difference(m, scoop)
    return finish(m, "desk_organiser")


# ── 5. parts tray ───────────────────────────────────────────────────────────

def build_parts_tray(p: dict) -> trimesh.Trimesh:
    H = p["height"]
    m = _tray_shell(p, H, H - 3, p["cols"], p["rows"])
    outer = rounded_rect(p["width"], p["depth"], p["corner_r"])
    rim = outer.buffer(p["rim_w"], quad_segs=16).difference(
        outer.buffer(-0.6, quad_segs=16))
    m = union(m, extrude(rim, H - 2.5, H))
    return finish(m, "parts_tray")


# ── 6. cup holder ───────────────────────────────────────────────────────────

def build_cup_holder(p: dict) -> trimesh.Trimesh:
    R = p["cup_d"] / 2
    H, wall, base_t = p["height"], p["wall"], p["base_t"]
    outer = cyl(R, H, cz=H / 2, sections=96)
    inner = cyl(R - wall, H - base_t + 2, cz=base_t + (H - base_t + 2) / 2,
                sections=96)
    m = difference(outer, inner)

    flutes = []
    for i in range(p["flutes"]):
        a = 2 * math.pi * i / p["flutes"]
        flutes.append(cyl(3.0, H + 2, (R + 2.0) * math.cos(a),
                          (R + 2.0) * math.sin(a), H / 2, sections=32))
    m = difference(m, *flutes)

    sw, sh = p["slot_w"], p["slot_h"]
    slot = box3(0, 0, 0, wall + 6, sw, sh)
    slot.apply_translation((R - wall / 2 + 1.0, 0, H - sh / 2 + 0.5))
    m = difference(m, slot)
    lip = cyl(4.0, sw + 2, sections=40)
    lip.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
    lip.apply_translation((R - wall / 2 + 1.0, 0, H - sh))
    m = difference(m, lip)

    if p["drains"]:
        holes = []
        for i in range(p["drains"]):
            a = 2 * math.pi * i / p["drains"]
            rr = (R - wall) * 0.5
            holes.append(cyl(2.6, base_t + 4, rr * math.cos(a), rr * math.sin(a),
                             base_t / 2, sections=32))
        m = difference(m, *holes)
    return finish(m, "cup_holder")


# ── 7. clothes peg ──────────────────────────────────────────────────────────

def build_clothes_peg(p: dict) -> trimesh.Trimesh:
    t, gap, L, loop_r = p["arm_t"], p["gap"], p["length"], p["loop_r"]
    lower = LineString([(0, -t / 2), (L, -t / 2)]).buffer(
        t / 2, cap_style=1, join_style=1)
    upper = LineString([(0, gap + t / 2), (L, gap + t / 2)]).buffer(
        t / 2, cap_style=1, join_style=1)
    back = LineString([(0, -t / 2), (-loop_r, -t / 2), (-loop_r, gap + t / 2),
                       (0, gap + t / 2)]).buffer(t / 2, cap_style=1, join_style=1)
    prof = _valid(unary_union([lower, upper, back]))

    teeth = []
    for i in range(p["teeth"]):
        x = L - 9 - i * 8
        teeth.append(Polygon([(x, 0), (x + 3.2, 0), (x + 1.6, 0.6)]))
        teeth.append(Polygon([(x, gap), (x + 3.2, gap), (x + 1.6, gap - 0.6)]))
    prof = _valid(unary_union([prof] + teeth))
    return finish(extrude(prof, 0, p["width"]), "clothes_peg")


# ── 8. cotton-bud holder ────────────────────────────────────────────────────

def build_swab_holder(p: dict) -> trimesh.Trimesh:
    R = p["diameter"] / 2
    H, wall, base_t = p["height"], p["wall"], p["base_t"]
    outer = cyl(R, H, cz=H / 2, sections=96)
    inner = cyl(R - wall, H - base_t + 2, cz=base_t + (H - base_t + 2) / 2,
                sections=96)
    m = difference(outer, inner)

    flutes = []
    for i in range(p["flutes"]):
        a = 2 * math.pi * i / p["flutes"] + math.pi / p["flutes"]
        flutes.append(cyl(3.0, H + 2, (R + 2.0) * math.cos(a),
                          (R + 2.0) * math.sin(a), H / 2, sections=32))
    m = difference(m, *flutes)

    win = box3(0, 0, 0, wall + 6, p["window_w"], p["window_h"])
    win.apply_translation((R - wall / 2 + 1.0, 0,
                           p["window_z"] + p["window_h"] / 2))
    m = difference(m, win)

    if p["notch"]:
        notch = box3(0, 0, 0, wall + 6, 6.0, 12.0)
        notch.apply_translation((R - wall / 2 + 1.0, 0, H - 4))
        m = difference(m, notch)
        lip = cyl(3.0, 8.0, sections=32)
        lip.apply_transform(rotation_matrix(math.pi / 2, [1, 0, 0]))
        lip.apply_translation((R - wall / 2 + 1.0, 0, H - 10))
        m = difference(m, lip)

    drains = []
    for i in range(3):
        a = 2 * math.pi * i / 3
        rr = (R - wall) * 0.45
        drains.append(cyl(2.4, base_t + 4, rr * math.cos(a), rr * math.sin(a),
                          base_t / 2, sections=32))
    m = difference(m, *drains)
    return finish(m, "swab_holder")


BUILDERS = {
    "cable_clip": build_cable_clip,
    "phone_stand": build_phone_stand,
    "wall_hook": build_wall_hook,
    "desk_organiser": build_desk_organiser,
    "parts_tray": build_parts_tray,
    "cup_holder": build_cup_holder,
    "clothes_peg": build_clothes_peg,
    "swab_holder": build_swab_holder,
}


def build(spec) -> trimesh.Trimesh:
    if spec.family not in BUILDERS:
        raise KeyError(spec.family)
    m = BUILDERS[spec.family](spec.params)
    if m is not None and not isinstance(m, trimesh.Trimesh):
        raise BuildError("builder returned %r" % type(m))
    return m
