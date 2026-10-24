"""Would this mesh print, and is it one piece?

The checks are deliberately blunt: a valid solid, one connected shell, a bed
footprint that carries the centre of mass, fits a 220 x 220 x 250 bed, and no
large unsupported overhang. Overhangs are reported as a warning with the area,
because a small bridge is normal; a model that needs supports is still
printable but the copy must say so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import trimesh
from scipy.spatial import ConvexHull
from shapely.geometry import MultiPoint, Point
from trimesh.transformations import rotation_matrix

PLA_G_PER_MM3 = 1.24e-3


@dataclass
class Check:
    ok: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def supports_needed(self) -> bool:
        return any("overhang" in w for w in self.warnings)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "issues": self.issues,
                "warnings": self.warnings, "metrics": self.metrics,
                "supports_needed": self.supports_needed}


def print_mesh(mesh: trimesh.Trimesh, family: str) -> trimesh.Trimesh:
    """The orientation that goes on the bed."""
    m = mesh.copy()
    if family == "wall_hook":
        # display: profile in XZ, width along Y. Lie it on its back so the
        # whole hook profile is the bed footprint, no supports under the arm.
        m.apply_transform(rotation_matrix(-math.pi / 2, [1, 0, 0]))
    m.apply_translation((0, 0, -float(m.bounds[0][2])))
    return m


def _contact(m: trimesh.Trimesh) -> tuple[float, bool, float]:
    verts = np.asarray(m.vertices)
    foot = verts[verts[:, 2] <= 0.45][:, :2]
    if len(foot) < 3:
        return 0.0, False, float("nan")
    try:
        hull = ConvexHull(foot)
    except Exception:
        return 0.0, False, float("nan")
    area = float(hull.volume)  # 2D: "volume" is area
    com = m.center_mass
    shp = MultiPoint(foot).convex_hull
    inside = bool(shp.buffer(-1.2).contains(Point(float(com[0]), float(com[1]))))
    return area, inside, area


def _overhang(m: trimesh.Trimesh) -> tuple[float, float]:
    n = np.asarray(m.face_normals)
    area = np.asarray(m.area_faces)
    z = np.asarray(m.triangles_center)[:, 2]
    total = float(area.sum())
    if total <= 0:
        return 0.0, 0.0
    bad = (n[:, 2] < -0.62) & (z > 0.8)
    return float(area[bad].sum()), float(area[bad].sum() / total)


def check(mesh: trimesh.Trimesh, spec) -> Check:
    issues: list[str] = []
    warnings: list[str] = []
    p = spec.params
    m = print_mesh(mesh, spec.family)

    if not m.is_watertight:
        issues.append("not watertight")
    if not m.is_winding_consistent:
        issues.append("inconsistent face winding")
    if m.volume <= 1.0:
        issues.append("volume is not positive")

    parts = m.split(only_watertight=False)
    if len(parts) != 1:
        issues.append("%d disconnected shells (single piece required)" % len(parts))

    lo, hi = m.bounds
    dims = [float(v) for v in (hi - lo)]
    if max(dims) > 220:
        issues.append("largest dimension %.0f mm exceeds a 220 mm bed" % max(dims))
    if max(dims) > 250:
        issues.append("taller than 250 mm")
    if min(dims) < 5.0:
        issues.append("thinnest overall dimension is %.1f mm" % min(dims))

    contact_area, stable, _ = _contact(m)
    if contact_area < 60.0:
        issues.append("bed contact is only %.0f mm2" % contact_area)
    if contact_area >= 60.0 and not stable:
        issues.append("centre of mass falls outside the bed footprint")

    over_area, over_frac = _overhang(m)
    if over_frac > 0.30:
        issues.append("%.0f%% of the surface is an unsupported overhang"
                      % (over_frac * 100))
    elif over_frac > 0.08:
        warnings.append("overhang: %.0f mm2 (%.0f%% of surface) may need "
                        "supports" % (over_area, over_frac * 100))

    design_walls = []
    for key in ("wall", "base_t", "arm_t", "plate_t", "back_t", "divider",
                "lip_t", "rim_w"):
        if key in p:
            design_walls.append(float(p[key]))
    if design_walls and min(design_walls) < 2.0:
        issues.append("a named wall is below 2.0 mm")
    if spec.family == "clothes_peg":
        live_gap = float(p["gap"]) - 1.2
        if live_gap < 1.0:
            issues.append("print-in-place gap is only %.1f mm" % live_gap)

    metrics = {
        "dims_mm": [round(v, 1) for v in dims],
        "volume_mm3": round(float(m.volume), 1),
        "surface_mm2": round(float(m.area), 1),
        "components": len(parts),
        "bed_contact_mm2": round(contact_area, 1),
        "overhang_mm2": round(over_area, 1),
        "overhang_frac": round(over_frac, 4),
        "est_mass_pla_g": round(float(m.volume) * PLA_G_PER_MM3, 1),
        "min_design_wall_mm": round(min(design_walls), 2) if design_walls else None,
        "print_orientation": "on its back" if spec.family == "wall_hook"
                             else "as modelled",
    }
    return Check(ok=not issues, issues=issues, warnings=warnings, metrics=metrics)
