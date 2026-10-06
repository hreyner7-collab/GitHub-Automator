"""The STL forge: compiles a strict JSON spec into a real binary STL.

Primitive vocabulary (all millimeters; Z=0 is the print plate):
    box:               size=[x,y,z], pos=[cx,cy,cz]  (pos = center)
    cylinder:          radius, height, pos=[cx,cy,cz], axis = z|x|y
    thin_shell_sphere: radius, pos, thickness_mm, opening_deg (top opening)
Part ops: add (weld into the object) / cut (subtract from the object).
Also-cut: per-part cuts applied to that part only.
"""
import struct
from pathlib import Path

import numpy as np
import trimesh


class SpecError(ValueError):
    """Raised for a spec the forge cannot compile at all."""


# ────────────────────────── binary STL writer ──────────────────────────

def write_binary_stl(mesh, path):
    """Emit a spec-compliant binary STL (exactly 84 + 50N bytes,
    header never starting with 'solid', normals outward, attribute=0)."""
    tris = np.asarray(mesh.faces, dtype=np.int64)
    normals = np.ascontiguousarray(mesh.face_normals, dtype=np.float32)
    verts = np.asarray(mesh.vertices, dtype=np.float32)
    n = len(tris)

    # one 12-byte normal per facet, then three 12-byte vertices
    v0 = np.ascontiguousarray(verts[tris[:, 0]], dtype=np.float32)
    v1 = np.ascontiguousarray(verts[tris[:, 1]], dtype=np.float32)
    v2 = np.ascontiguousarray(verts[tris[:, 2]], dtype=np.float32)

    buf = np.zeros((n, 50), dtype=np.uint8)
    buf[:, 0:12] = normals.view(np.uint8).reshape(n, 12)
    buf[:, 12:24] = v0.view(np.uint8).reshape(n, 12)
    buf[:, 24:36] = v1.view(np.uint8).reshape(n, 12)
    buf[:, 36:48] = v2.view(np.uint8).reshape(n, 12)
    # attribute byte count stays zero

    header = b"binary STL by creality pipeline forge".ljust(80, b"\x00")[:80]
    with open(path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", n))
        f.write(buf.tobytes())
    return path


# ────────────────────────── primitives ──────────────────────────

def _shape_mesh(shape):
    """Compile one shape dict into a trimesh Positioned/Trimesh object."""
    t = shape.get("type")
    if t == "box":
        size = [float(x) for x in shape.get("size", [0, 0, 0])]
        if len(size) != 3 or min(size) <= 0:
            raise SpecError(f"box needs size=[x,y,z] all > 0, got {size}")
        cx, cy, cz = _center(shape)
        b = trimesh.creation.box(extents=size)
        b.apply_translation([cx, cy, cz])
        return b
    if t == "cylinder":
        r = float(shape.get("radius", 0))
        h = float(shape.get("height", 0))
        if r <= 0 or h <= 0:
            raise SpecError("cylinder needs radius>0 and height>0")
        axis = shape.get("axis", "z")
        cx, cy, cz = _center(shape)
        mesh = trimesh.creation.cylinder(radius=r, height=h, sections=48)
        if axis == "x":
            mesh.apply_transform(
                trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        elif axis == "y":
            mesh.apply_transform(
                trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        elif axis != "z":
            raise SpecError(f"cylinder axis must be z|x|y, got {axis!r}")
        mesh.apply_translation([cx, cy, cz])
        return mesh
    if t == "thin_shell_sphere":
        r = float(shape.get("radius", 0))
        tmm = float(shape.get("thickness_mm", 1.6))
        if r <= 0:
            raise SpecError("thin_shell_sphere needs radius>0")
        if tmm < 1.2 or r - tmm < 2.5:
            raise SpecError(f"thin_shell_sphere too thin: r={r}, t={tmm}; "
                            "walls < 1.2mm are unprintable")
        inner_r = r - tmm
        outer = trimesh.creation.icosphere(subdivisions=4, radius=r)
        inner = trimesh.creation.icosphere(subdivisions=4, radius=inner_r)
        m = trimesh.boolean.difference([outer, inner])
        opening = float(shape.get("opening_deg", 0.0))
        if opening > 0:
            # cut a hollow-centered channel through the top wall so the
            # ball has a usable opening (robust boolean, no revolve)
            half = np.deg2rad(opening) / 2.0
            cut_r = r * np.sin(half) + tmm * 0.2
            cut = trimesh.creation.cylinder(radius=cut_r, height=r + tmm,
                                            sections=64)
            cut.apply_translation([0, 0, r - (r + tmm) / 2.0 + 0.001])
            m = trimesh.boolean.difference([m, cut])
        cx, cy, cz = _center(shape)
        m.apply_translation([cx, cy, cz])
        return m
    raise SpecError(f"unknown shape type {t!r} (use box|cylinder|"
                    "thin_shell_sphere)")


def _center(shape):
    pos = shape.get("pos", [0, 0, 0])
    if len(pos) != 3:
        raise SpecError("pos must be [cx,cy,cz]")
    try:
        return [float(pos[0]), float(pos[1]), float(pos[2])]
    except Exception:
        raise SpecError("pos must be numbers")


def trimesh_transform(translation):
    """4x4 translation matrix."""
    from trimesh.transformations import translation_matrix
    return translation_matrix(translation)


# ────────────────────────── assembly ──────────────────────────

def build_mesh(spec):
    """Compile the full spec into one trimesh object (Z-min placed on 0)."""
    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts:
        raise SpecError("spec.parts must be a non-empty list of parts")

    obj = None
    pieces = []
    cuts = []
    for i, part in enumerate(parts):
        if not isinstance(part, dict):
            raise SpecError(f"part #{i} is not an object")
        pid = str(part.get("id", f"part{i}"))
        op = part.get("op", "add")
        if op not in ("add", "cut"):
            raise SpecError(f"part {pid}: op must be add|cut, got {op!r}")
        mesh = _shape_mesh(part.get("shape", {}))
        extra_cuts = []
        for c in part.get("also_cut", []) or []:
            shape = c.get("shape", {}) if isinstance(c, dict) else {}
            extra_cuts.append(_shape_mesh(shape))
        if op == "cut":
            cuts.append((pid, mesh, extra_cuts))
        else:
            for c in extra_cuts:
                mesh = trimesh.boolean.difference([mesh, c])
            pieces.append((pid, mesh))

    if not pieces:
        raise SpecError("spec has add-nothing: every part was op=cut")

    obj = pieces[0][1]
    for pid, m in pieces[1:]:
        obj = trimesh.boolean.union([obj, m])
    for pid, m, extra in cuts:
        try:
            obj = trimesh.boolean.difference([obj, m])
        except Exception as e:
            raise SpecError(f"cut {pid} failed: {e}")

    if obj is None or len(obj.faces) == 0:
        raise SpecError("compiled object is empty (cuts removed everything?)")
    return obj


def place_on_plate(obj):
    """Translate the object so its lowest point sits exactly on Z=0."""
    zmin = float(obj.bounds[0][2])
    obj.apply_translation([0, 0, -zmin])
    return obj


def forge(spec, out_path):
    """Full compile: spec -> mesh -> orient -> binary STL on disk.

    Returns (mesh). Raises SpecError for un-compilable specs.
    """
    obj = build_mesh(spec)
    obj = place_on_plate(obj)
    obj.fix_normals()
    write_binary_stl(obj, out_path)
    return obj
