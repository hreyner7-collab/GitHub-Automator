"""Mesh verification for the STL generator. Raises VerifyError carrying a
structured report; the forge transports that report back to Kimi for repair."""
import struct
from pathlib import Path


class VerifyError(Exception):
    """Raised when a generated artifact fails any printability gate."""

    def __init__(self, report):
        self.report = report
        msg = report.get("errors") or ["verification failed"]
        super().__init__("; ".join(str(m) for m in msg))


def _report_from(mesh, stl_path=None):
    """Full verify report dict for a trimesh mesh + optional on-disk STL."""
    report = {
        "ok": False,
        "errors": [],
        "warnings": [],
        "watertight": False,
        "volume_mm3": None,
        "extents_mm": None,
        "tris": None,
        "binary_layout_ok": None,
        "stl_path": None,
        "png_paths": [],
    }
    try:
        name = getattr(mesh, "source_file_name", None)
    except Exception:
        name = None
    if stl_path is None:
        stl_path = name
    if stl_path is not None:
        report["stl_path"] = str(stl_path)
    if mesh is None:
        report["errors"].append("no mesh produced")
        return report
    report["tris"] = int(len(mesh.faces))
    report["watertight"] = bool(mesh.is_watertight)
    try:
        report["volume_mm3"] = float(mesh.volume)
    except Exception:
        report["volume_mm3"] = 0.0
    ext = [float(x) for x in mesh.extents]
    report["extents_mm"] = ext
    if not report["watertight"]:
        report["errors"].append(
            "mesh is not watertight (holes or separate islands); "
            "every part must weld/overlap >=1mm or contact >=8mm^2")
    if report["watertight"] and (not report["volume_mm3"] or
                                 report["volume_mm3"] <= 0):
        report["errors"].append(
            f"watertight but volume is {report['volume_mm3']}; inverted "
            "normals (faces inside-out)")
    if mesh.body_count > 1:
        report["errors"].append(
            f"{mesh.body_count} disconnected bodies; parts must overlap "
            ">=1mm so they print as one solid (the fault case uses two "
            "separate 20mm boxes 30mm apart)")
    for i, d in enumerate(ext):
        if d > 500.0:
            report["errors"].append(
                f"bounding extent {d:.0f}mm on axis {i} exceeds printable "
                "500mm")
    return report


def _audit_binary(stl_path):
    """Structural audit of the on-disk binary STL (magic, count, EOF)."""
    path = Path(stl_path)
    data = path.read_bytes()
    if data[:5].lower() == b"solid":
        return False, "binary STL header starts with 'solid' (ASCII-confusable)"
    for magic, kind in ((b"PK\x03\x04", "zip"), (b"\xff\xd8\xff", "jpeg"),
                        (b"\x89PNG", "png")):
        if data[:len(magic)] == magic:
            return False, f"file is {kind}, not STL"
    if len(data) < 84:
        return False, "file too small for a binary STL"
    (n,) = struct.unpack_from("<I", data, 80)
    if len(data) != 84 + 50 * n:
        return False, f"size {len(data)} != 84+50x{n}"
    return True, None


def generate_previews(stl_path, out_dir=None):
    """Render 3 orthogonal shaded views of the mesh to PNGs (>=768px)."""
    import numpy as np
    from PIL import Image, ImageDraw
    import trimesh

    out_dir = Path(out_dir or Path(stl_path).parent)
    mesh = trimesh.load_mesh(str(stl_path), process=False)
    verts = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    n = np.asarray(mesh.face_normals, dtype=float)
    center = verts.mean(axis=0)
    span = max(verts.max(axis=0) - verts.min(axis=0)) or 1.0

    # light direction and simple lambert shading (albedo by axis color)
    light = np.array([0.4, 0.35, 0.85])
    light = light / np.linalg.norm(light)
    base = np.array([230, 120, 40], dtype=float)          # safety orange
    axis_tint = np.array([[210, 210, 220], [210, 220, 210],
                          [240, 170, 60]], dtype=float)[2]

    size = 800
    views = [("top", np.array([0, 0, 1.0])), ("front", np.array([0, -1.0, 0.6])),
             ("iso", np.array([0.7, -0.7, 0.55]))]
    pngs = []
    for vname, vdir in views:
        vdir = vdir / np.linalg.norm(vdir)
        right = np.cross(vdir, [0, 0, 1.0])
        if np.linalg.norm(right) < 1e-6:
            right = np.array([1.0, 0, 0])
        right /= np.linalg.norm(right)
        up = np.cross(right, vdir)
        p = (verts - center) @ np.stack([right, up]).T
        scale = (size * 0.42) / span
        xy = p * scale + size / 2
        xy[:, 1] = size - xy[:, 1]          # flip y for image coords
        # painter's sort: far faces first (use per-FACE view depth)
        depth = (verts - center) @ vdir
        fd = depth[faces].mean(axis=1)
        order = np.argsort(fd)
        img = Image.new("RGB", (size, size), (18, 18, 20))
        d = ImageDraw.Draw(img)
        for fi in order:
            tri = xy[faces[fi]]
            lum = float(np.clip(abs(n[fi] @ light), 0.0, 1.0))
            col = tuple(int(c) for c in (axis_tint * (0.35 + 0.65 * lum)))
            d.polygon([tuple(q) for q in tri], fill=col,
                      outline=(10, 10, 10))
        png_path = out_dir / (Path(stl_path).stem + "_" + vname + ".png")
        img.save(png_path, "PNG")
        pngs.append(str(png_path))
    return pngs


def verify(path, out_dir=None):
    """Verify + preview an on-disk STL. Returns the report dict; raises
    VerifyError on printability failure (carrying the report for repair)."""
    import trimesh

    path = Path(path)
    layout_ok, layout_err = _audit_binary(path)
    if not layout_ok:
        rep = {"ok": False, "errors": [layout_err], "warnings": [],
               "watertight": False, "volume_mm3": None, "extents_mm": None,
               "tris": None, "binary_layout_ok": False, "stl_path": str(path),
               "png_paths": []}
        raise VerifyError(rep)

    try:
        mesh = trimesh.load_mesh(str(path), process=True)
    except Exception as e:
        rep = _report_from(None, str(path))
        rep["errors"].append(f"mesh reload failed: {e}")
        rep["binary_layout_ok"] = True
        raise VerifyError(rep)

    rep = _report_from(mesh, str(path))
    rep["binary_layout_ok"] = True

    if rep["errors"]:
        raise VerifyError(rep)

    # Only fully-valid parts get previews (they are the acceptance artifact)
    try:
        rep["png_paths"] = generate_previews(path, out_dir)
    except Exception as e:
        rep["warnings"].append(f"preview render failed: {e}")
    rep["ok"] = True
    return rep
