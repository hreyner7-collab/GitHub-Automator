"""Proof of creation — the artefact Creality's review asks for.

Their help centre defines it as evidence that the work "is your original
creation" and warns that "failure to provide proof of originality may result in
your model not being approved during the review process". This module builds
that evidence from the upload's OWN model file: it reads the geometry (STL in
binary or ASCII form, or the 3MF container's 3D/3dmodel.model XML), renders a
high-resolution isometric view, and writes a parameters card beside it — the
"graphics and parameters" the review asks for — plus the file's sha256 so the
proof is tied to the exact artefact being uploaded.

No new dependencies: zipfile + xml for 3MF, struct for STL, Pillow for the
render (already used by editor.py).

CLI:  .venv/Scripts/python.exe proof.py <model-file> [--name NAME] [--out DIR]
      -> prints the JSON verdict on stdout, exit 0 when a proof was built.

Returns, from build(): {ok, png, params, sha256, why}
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
PROOF_DIR = ROOT / "downloads" / "_proof"


def proof_dir() -> Path:
    """Where proofs are written. `CREALITY_PROOF_DIR` moves it (the tests use a
    tmp dir so a test can never write into the project's downloads folder)."""
    return Path(os.environ.get("CREALITY_PROOF_DIR") or PROOF_DIR)
SIZE = 2000                      # the review asks for high resolution
MAX_DRAWN = 60000                # painter's algorithm stays quick on big meshes

THREEMF_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


# ── reading geometry ─────────────────────────────────────────────────────────

def _stl_binary(raw: bytes):
    """Binary STL: 80-byte header, uint32 triangle count, 50 bytes each."""
    if len(raw) < 84:
        return None
    count = struct.unpack_from("<I", raw, 80)[0]
    if count == 0 or 84 + count * 50 > len(raw) + 12:
        return None
    tris = []
    off = 84
    for _ in range(count):
        vals = struct.unpack_from("<12fH", raw, off)
        off += 50
        tris.append(((vals[3], vals[4], vals[5]),
                     (vals[6], vals[7], vals[8]),
                     (vals[9], vals[10], vals[11])))
    return tris


def _stl_ascii(raw: bytes):
    """ASCII STL: 'vertex x y z' lines, three per facet."""
    try:
        text = raw.decode("utf-8", "ignore")
    except Exception:
        return None
    if "facet" not in text.lower():
        return None
    pts = []
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("vertex"):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    pts.append(tuple(float(p) for p in parts[1:4]))
                except ValueError:
                    return None
    return [tuple(pts[i:i + 3]) for i in range(0, len(pts) - 2, 3)]


def read_stl(path: Path):
    return read_stl_bytes(path.read_bytes())


def read_stl_bytes(raw: bytes):
    """STL geometry from the file's bytes (binary first, then ASCII)."""
    return _stl_binary(raw) or _stl_ascii(raw) or []


PROD_NS = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
_UNIT_MM = {"millimeter": 1.0, "centimeter": 10.0, "meter": 1000.0,
            "inch": 25.4, "foot": 304.8, "micron": 0.001}


def _mat(spec):
    """A 3MF transform (12 numbers: 3x3 row-major then translation) as
    (rot, trans) — a point maps as rot(p) + trans."""
    if not spec:
        return None
    try:
        v = [float(x) for x in str(spec).replace(",", " ").split()]
    except ValueError:
        return None
    if len(v) < 12:
        return None
    return ([v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8]],
            (v[9], v[10], v[11]))


def _compose(outer, inner):
    """outer ∘ inner: apply inner first, then outer."""
    if outer is None:
        return inner
    if inner is None:
        return outer
    (a, at), (b, bt) = outer, inner
    m = [sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
         for r in range(3) for c in range(3)]
    t = tuple(sum(a[r * 3 + k] * bt[k] for k in range(3)) + at[r]
              for r in range(3))
    return (m, t)


def _apply(mat, p):
    if mat is None:
        return p
    m, t = mat
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z + t[0],
            m[3] * x + m[4] * y + m[5] * z + t[1],
            m[6] * x + m[7] * y + m[8] * z + t[2])


def read_3mf(source):
    """3MF is a ZIP. The mesh is NOT always in 3D/3dmodel.model: slicers
    (Bambu/Orca, and Creality's own exports) keep it in 3D/Objects/*.model and
    the root file only holds <components> pointing at it, so the references are
    followed here — otherwise a perfectly good 3MF reads as "no geometry".
    Units are applied so the parameters card is in millimetres.

    `source` is a path or the file's bytes (a 3MF read out of a ZIP)."""
    if isinstance(source, (bytes, bytearray)):
        with zipfile.ZipFile(io.BytesIO(source)) as z:
            return _read_3mf_zip(z)
    with zipfile.ZipFile(source) as z:
        return _read_3mf_zip(z)


def _read_3mf_zip(z: zipfile.ZipFile):
    members = [n for n in z.namelist() if n.lower().endswith(".model")]
    if not members:
        return []
    trees = {}
    for n in members:
        try:
            trees[n.lstrip("/")] = ET.fromstring(z.read(n))
        except Exception:
            continue
    if not trees:
        return []
    root_key = next((k for k in trees if k.lower().endswith("3d/3dmodel.model")),
                    sorted(trees)[0])
    root = trees[root_key]
    scale = _UNIT_MM.get((root.get("unit") or "millimeter").lower(), 1.0)

    def objects_of(tree):
        out = {}
        for obj in tree.iter(THREEMF_NS + "object"):
            if obj.get("id"):
                out[obj.get("id")] = obj
        return out

    index = {k: objects_of(t) for k, t in trees.items()}
    tris, seen = [], set()

    def emit(file_key, objid, mat, depth=0):
        if depth > 8 or not objid:
            return
        key = (file_key, str(objid))
        if key in seen:
            return
        obj = index.get(file_key, {}).get(str(objid))
        if obj is None:
            return
        seen.add(key)
        mesh = obj.find(THREEMF_NS + "mesh")
        if mesh is not None:
            vs = []
            for v in mesh.iter(THREEMF_NS + "vertex"):
                vs.append(_apply(mat, (float(v.get("x", 0)),
                                       float(v.get("y", 0)),
                                       float(v.get("z", 0)))))
            for t in mesh.iter(THREEMF_NS + "triangle"):
                try:
                    tris.append((vs[int(t.get("v1", 0))],
                                 vs[int(t.get("v2", 0))],
                                 vs[int(t.get("v3", 0))]))
                except (IndexError, TypeError, ValueError):
                    continue
        comps = obj.find(THREEMF_NS + "components")
        if comps is not None:
            for c in comps:
                ref = c.get(PROD_NS + "path") or c.get("path") or file_key
                ref = str(ref).lstrip("/")
                if ref not in index:
                    ref = file_key
                emit(ref, c.get("objectid"),
                     _compose(mat, _mat(c.get("transform"))), depth + 1)

    build = root.find(THREEMF_NS + "build")
    items = list(build) if build is not None else []
    for item in items:
        emit(root_key, item.get("objectid"), _mat(item.get("transform")))
    if not tris:                       # no <build>: every root object
        for oid in index.get(root_key, {}):
            emit(root_key, oid, None)
    if not tris:                       # last resort: any mesh in the package
        for fk, objs in index.items():
            for oid in objs:
                emit(fk, oid, None)
    if scale != 1.0:
        tris = [tuple((x * scale, y * scale, z * scale) for (x, y, z) in t)
                for t in tris]
    return tris


def _zip_names(path: Path):
    try:
        with zipfile.ZipFile(path) as z:
            return z.namelist()
    except Exception:
        return []


def read_step_points(raw: bytes):
    """The CARTESIAN_POINT coordinates of a STEP file (AP203/AP242 text form).
    Not a mesh — the caller builds triangles/degen facets from the point set
    (a point-cloud render: honest geometry evidence, no new dependencies)."""
    if not raw:
        return []
    try:
        text = raw.decode("utf-8", "ignore")
    except Exception:
        return []
    if "CARTESIAN_POINT" not in text:
        return []
    pts = []
    for m in re.finditer(
            r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)", text):
        try:
            xyz = [float(v) for v in m.group(1).replace(",", " ").split()]
        except ValueError:
            continue
        if len(xyz) >= 3:
            # the render space is mm: STEP files are commonly in mm already,
            # metres/inch units do not matter for a shape-evidence proof
            pts.append((xyz[0], xyz[1], xyz[2]))
    return pts


def _facets_from_points(pts, cap=40000):
    """A renderable facet soup from a point cloud: consecutive points become
    degenerate-free thin triangles (connected triples) — enough for the
    isometric painter to show the part's SURFACE SAMPLING honestly."""
    if len(pts) < 3:
        return []
    if len(pts) > cap:
        step = len(pts) / cap
        pts = [pts[int(i * step)] for i in range(cap)]
    return [(pts[i], pts[i + 1], pts[i + 2])
            for i in range(len(pts) - 2)]


def _largest_inner_model(path: Path):
    """A ZIP of model FILES (Creality's "download all" answers with
    <model>_part1.zip holding the .3mf files) — return (bytes, name) of the
    largest model file inside, else None."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if n.lower().endswith((".3mf", ".stl"))]
            if not names:
                return None
            big = max(names, key=lambda n: z.getinfo(n).file_size)
            return z.read(big), big
    except Exception:
        return None


def read_mesh(path: str | Path):
    """(triangles, format) for an STL, a 3MF, or a ZIP holding model files;
    ([], why) when unreadable."""
    p = Path(path)
    if not p.is_file():
        return [], "no model file on disk (%s)" % p
    ext = p.suffix.lower()
    try:
        if ext == ".3mf":
            tris = read_3mf(p)
            if tris:
                return tris, "3MF"
        elif ext in (".stl",):
            tris = read_stl(p)
            if tris:
                return tris, "STL"
        elif ext in (".zip", ".rar", ".7z"):
            inner = _largest_inner_model(p)
            if inner:
                blob, name = inner
                tris = (read_3mf(blob) if name.lower().endswith(".3mf")
                        else (read_stl_bytes(blob) or []))
                if tris:
                    return tris, name.rsplit(".", 1)[-1].upper()
            # a CAD-only zip (Creality's split download can hand back .step
            # parts with no mesh): read the STEP's points — an honest point-
            # cloud proof beats refusing a model Creality itself will accept
            steps = [n for n in _zip_names(p)
                     if n.lower().endswith((".step", ".stp"))]
            for n in steps:
                try:
                    with zipfile.ZipFile(p) as z:
                        pts = read_step_points(z.read(n))
                except Exception:
                    pts = []
                if pts:
                    return _facets_from_points(pts), n.rsplit(".", 1)[-1].upper()
        elif ext in (".step", ".stp"):
            pts = read_step_points(p.read_bytes())
            if pts:
                return _facets_from_points(pts), "STEP"
        else:
            for reader in (read_3mf, read_stl):
                try:
                    tris = reader(p)
                except Exception:
                    tris = []
                if tris:
                    return tris, reader.__name__.replace("read_", "").upper()
    except Exception as e:
        return [], "could not read %s: %s" % (p.name, str(e)[:120])
    return [], ("could not read any geometry from %s — the proof must be built "
                "from the file being uploaded" % p.name)


# ── rendering ────────────────────────────────────────────────────────────────

def _transform(tris, yaw=-35.0, pitch=22.0):
    """Rotate into an isometric-ish view: (screen x, screen y, depth) per vertex."""
    a, b = math.radians(yaw), math.radians(pitch)
    ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
    out = []
    for tri in tris:
        view = []
        for (x, y, z) in tri:
            x1 = x * ca - y * sa
            y1 = x * sa + y * ca
            y2 = y1 * cb - z * sb          # away from the camera
            z2 = y1 * sb + z * cb          # up
            view.append((x1, -z2, y2))
        out.append(view)
    return out


def render(tris, out_path: Path, name: str = "", params: dict | None = None,
           yaw: float = -35.0, pitch: float = 22.0, card: bool = True,
           size: int = SIZE):
    """High-resolution isometric render, via Pillow. With `card` the render
    carries the parameters block the review asks for ("graphics and
    parameters"); without it, it is a plain gallery photo of the model."""
    from PIL import Image, ImageDraw
    size = int(max(400, min(4000, size)))
    view = _transform(tris, yaw, pitch)
    xs = [v[0] for t in view for v in t]
    ys = [v[1] for t in view for v in t]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = size * 0.08
    span = max(maxx - minx, maxy - miny) or 1.0
    scale = (size - 2 * pad) / span
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    img = Image.new("RGB", (size, size + (190 if card else 0)), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    light = (-0.45, -0.55, 0.70)

    def shade(t):
        (ax, ay, az), (bx, by, bz), (cx2, cy2, cz2) = t
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx2 - ax, cy2 - ay, cz2 - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        lam = abs((nx * light[0] + ny * light[1] + nz * light[2]) / ln)
        v = int(90 + 150 * lam)
        return (v, int(v * 0.97), int(v * 0.92))

    drawn = view if len(view) <= MAX_DRAWN else view[::max(1, len(view) // MAX_DRAWN)]
    for t in sorted(drawn, key=lambda t: -sum(v[2] for v in t) / 3.0):
        pts = [((v[0] - cx) * scale + size / 2.0,
                (v[1] - cy) * scale + (size + 40) / 2.0) for v in t]
        draw.polygon(pts, fill=shade(t))
    if not card:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG", optimize=True)
        return out_path
    # parameters card — "graphics and parameters", as the help centre asks
    y = size + 16
    lines = [("Design: " + (name or out_path.stem))[:110]]
    if params:
        lines.append(("Triangles: %d   Format: %s   Bounding box: %.1f x %.1f x %.1f mm"
                      % (params.get("triangles", 0), params.get("format", "?"),
                         params.get("size_mm", [0, 0, 0])[0],
                         params.get("size_mm", [0, 0, 0])[1],
                         params.get("size_mm", [0, 0, 0])[2]))[:110])
        lines.append(("Vertices: %d   File: %s   sha256: %s"
                      % (params.get("vertices", 0), params.get("file", ""),
                         (params.get("sha256", "") or "")[:16]))[:110])
    lines.append("Proof of creation: rendered from this model file by the "
                 "pipeline (own geometry, not a photograph)")
    for text in lines:
        draw.text((28, y), text, fill=(25, 25, 25))
        y += 26
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


# ── the public entry point ───────────────────────────────────────────────────

# The angles of the CAD-style renders of a model. Kept for the render helper
# (views), which is no longer used for listing photos: Creality rejects these
# views because they do not look like the object as it is fully printed.
VIEW_ANGLES = (-35.0, 35.0, 95.0, 150.0, 215.0, 290.0)


def views(model_path: str | Path, name: str = "", slug: str = "",
          out_dir: str | Path | None = None, angles=VIEW_ANGLES,
          size: int = 1400) -> dict:
    """Our OWN photos of the model: one plain render per angle (no card, the
    cover-friendly shape). Returns {ok, pngs, why} — `pngs` empty means no photo
    could be made from this file, which the caller must treat as a refusal."""
    p = Path(model_path) if model_path else None
    if not p or not p.is_file():
        return {"ok": False, "pngs": [], "why": "no model file to render photos from"}
    tris, fmt = read_mesh(p)
    if not tris:
        return {"ok": False, "pngs": [], "why": fmt}
    out = Path(out_dir) if out_dir else proof_dir()
    stem = slug or p.stem
    pngs = []
    for i, yaw in enumerate(angles):
        dest = out / ("%s_view%d.png" % (stem, i))
        try:
            render(tris, dest, name=name, params=None, yaw=yaw, card=False,
                   size=size)
        except Exception as e:
            return {"ok": False, "pngs": pngs,
                    "why": "could not render view %d: %s" % (i, str(e)[:140])}
        pngs.append(str(dest))
    return {"ok": True, "pngs": pngs, "why": ""}


def build(model_path: str | Path, name: str = "",
          out_dir: str | Path | None = None, slug: str = "") -> dict:
    """Build the proof for one model file. Returns
    {ok, png, params, sha256, why} — `ok` False means the upload is refused."""
    p = Path(model_path) if model_path else None
    if not p or not p.is_file():
        return {"ok": False, "why": "no model file to build a proof from",
                "png": "", "params": {}, "sha256": ""}
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    tris, fmt = read_mesh(p)
    if not tris:
        return {"ok": False, "why": fmt, "png": "", "params": {},
                "sha256": sha}
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    params = {"triangles": len(tris), "vertices": len(tris) * 3, "format": fmt,
              "file": p.name, "sha256": sha, "bytes": len(raw),
              "size_mm": [round(max(xs) - min(xs), 1),
                          round(max(ys) - min(ys), 1),
                          round(max(zs) - min(zs), 1)],
              "watertight_guess": None}
    out = Path(out_dir) if out_dir else proof_dir()
    png = out / ("%s_proof.png" % (slug or p.stem))
    try:
        render(tris, png, name=name, params=params)
    except Exception as e:                      # never fake a proof
        return {"ok": False, "why": "could not render the proof: %s" % str(e)[:160],
                "png": "", "params": params, "sha256": sha}
    side = png.with_suffix(".json")
    # Written through a temp file and swapped in: this sidecar is the machine-
    # readable half of the proof, and a crash mid-write would leave a truncated
    # JSON next to a perfectly good PNG. Same pattern as the ledger and the
    # tried-listings record.
    side_tmp = side.with_name(side.name + ".tmp")
    side_tmp.write_text(json.dumps({"name": name or p.stem, "params": params,
                                    "source_file": str(p), "png": str(png)},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    side_tmp.replace(side)
    return {"ok": True, "png": str(png), "params": params, "sha256": sha,
            "why": ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model")
    ap.add_argument("--name", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    res = build(args.model, name=args.name, out_dir=args.out or None)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
