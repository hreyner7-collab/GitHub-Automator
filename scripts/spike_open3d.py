"""Time-boxed spike: can Open3D's Filament renderer replace Blender?

Not a pipeline module. It renders the same STL, sweep and front camera as
blender/render.py, using the lights Open3D actually exposes (directional only;
there is no area-light API), and reports wall time plus a deltaE against the
Cycles baseline. Quality is judged by eye and by the same gates as the bench.

Run:
  .venv/Scripts/python.exe scripts/spike_open3d.py
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import open3d as o3d
import open3d.visualization.rendering as rn
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "artifacts" / "bench"
BASELINE = BENCH / "parity1000" / "cable_clip-01-a529057e" / "cycles-cpu" / "r1"

DESK_TOP = 0.018
BG = (222, 217, 212)
COLOUR = (191, 96, 78)


def srgb(rgb):
    v = [c / 255.0 for c in rgb[:3]]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in v]


def sweep_mesh():
    profile = []
    for i in range(22):
        y = -2.0 + (2.5 / 21) * i
        profile.append((y, 0.0))
    for i in range(1, 19):
        a = -math.pi / 2 + (math.pi / 2) * i / 18
        profile.append((0.5 + 0.5 * math.cos(a), 0.5 + 0.5 * math.sin(a)))
    for i in range(1, 9):
        profile.append((1.0, 1.0 + 0.2 * i))
    xs = [-2.5, 2.5]
    verts, faces = [], []
    for x in xs:
        for y, z in profile:
            verts.append((x, y, z))
    n = len(profile)
    for j in range(len(xs) - 1):
        for i in range(n - 1):
            a = j * n + i
            b = (j + 1) * n + i
            c = (j + 1) * n + i + 1
            d = j * n + i + 1
            faces.append((a, b, c, d))
    m = o3d.geometry.TriangleMesh()
    m.vertices = o3d.utility.Vector3dVector(np.asarray(verts, dtype=float))
    m.triangles = o3d.utility.Vector3iVector(np.asarray(
        [(a, b, c) for quad in faces for a, b, c in ((quad[0], quad[1], quad[2]),
                                                     (quad[0], quad[2], quad[3]))],
        dtype=np.int32))
    m.compute_vertex_normals()
    return m


def studio_hdr(path: Path, w=512, h=256):
    """A soft three-light studio equirect, written as Radiance RGBE."""
    yy, xx = np.mgrid[0:h, 0:w]
    az = (xx / w) * 2 * math.pi - math.pi
    el = (0.5 - yy / h) * math.pi
    rgb = np.ones((h, w, 3)) * 0.10
    for (a0, e0, amp, sig) in ((-0.7, 0.55, 3.2, 0.45),
                               (0.9, 0.30, 1.1, 0.5),
                               (2.6, 0.65, 1.6, 0.5)):
        d2 = (np.minimum(np.abs(az - a0), 2 * math.pi - np.abs(az - a0)) ** 2
              / sig ** 2 + (el - e0) ** 2 / sig ** 2)
        rgb += (amp * np.exp(-d2))[..., None] * np.array([1.0, 0.97, 0.92])
    rgbe = np.zeros((h, w, 4), dtype=np.uint8)
    v = rgb.max(axis=2)
    e = np.where(v > 1e-6, np.floor(np.log2(v)) + 1, 0)
    scale = np.where(v > 1e-6, 256.0 / np.exp2(e), 0)
    rgbe[..., :3] = np.clip(rgb * scale[..., None], 0, 255).astype(np.uint8)
    rgbe[..., 3] = np.clip(e + 128, 0, 255).astype(np.uint8)
    with path.open("wb") as f:
        f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n")
        f.write(("-Y %d +X %d\n" % (h, w)).encode())
        f.write(rgbe.tobytes())
    return path


def srgb_to_linear_img(rgb):
    c = rgb / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def fit_camera(corners, target, az_deg, el_deg, fill, lens=70.0, sensor=36.0):
    az, el = math.radians(az_deg), math.radians(el_deg)
    to_cam = np.array([math.sin(az) * math.cos(el),
                       -math.cos(az) * math.cos(el), math.sin(el)])
    forward = -to_cam
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    fov = 2 * math.atan(sensor / (2 * lens))
    tan = math.tan(fov / 2)
    need = 0.02
    for c in corners:
        p = np.asarray(c) - target
        need = max(need, abs(p @ right) / (fill * tan) + p @ to_cam,
                   abs(p @ up) / (fill * tan) + p @ to_cam)
    eye = target + to_cam * need
    return eye, up, math.degrees(fov)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stl", default=str(BENCH / "parity1000" /
                                        "cable_clip-01-a529057e" / "model.stl"))
    ap.add_argument("--out", default=str(BENCH / "spike_open3d"))
    ap.add_argument("--res", type=int, default=1000)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    mesh = o3d.io.read_triangle_mesh(a.stl)
    lo, hi = mesh.get_min_bound(), mesh.get_max_bound()
    mesh.translate((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]))
    mesh.compute_vertex_normals()
    lo, hi = mesh.get_min_bound(), mesh.get_max_bound()
    corners = [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
               for z in (lo[2], hi[2])]
    target = np.array([0.0, 0.0, (hi[2] - lo[2]) * 0.48])
    eye, up, fov_deg = fit_camera(corners, target, 0.0, 13.0, 0.64)

    r = rn.OffscreenRenderer(a.res, a.res)
    r.scene.set_background(list(srgb(BG)) + [1.0])
    sweep = sweep_mesh()
    m_sweep = rn.MaterialRecord()
    m_sweep.shader = "defaultLit"
    m_sweep.base_color = srgb(BG) + [1.0]
    m_sweep.base_roughness = 0.92
    m_prod = rn.MaterialRecord()
    m_prod.shader = "defaultLit"
    m_prod.base_color = srgb(COLOUR) + [1.0]
    m_prod.base_roughness = 0.46
    m_prod.base_reflectance = 0.45
    r.scene.add_geometry("sweep", sweep, m_sweep)
    r.scene.add_geometry("product", mesh, m_prod)

    lights = {}
    try:
        sk = r.scene.scene
        sk.add_directional_light("key", [1.0, 0.97, 0.93],
                                 [-0.55, 0.55, -0.63], 65000.0, True)
        sk.add_directional_light("fill", [0.93, 0.96, 1.0],
                                 [0.6, 0.5, -0.3], 18000.0, False)
        sk.add_directional_light("rim", [1.0, 1.0, 1.0],
                                 [0.1, -0.8, -0.5], 30000.0, False)
        sk.enable_light_shadow("key", True)
        lights["directional"] = ["key", "fill", "rim"]
    except Exception as e:
        lights["directional_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        hdr = studio_hdr(out / "studio.hdr")
        ok = r.scene.scene.set_indirect_light(str(hdr))
        r.scene.scene.enable_indirect_light(True)
        r.scene.scene.set_indirect_light_intensity(30000)
        lights["ibl"] = {"file": str(hdr), "loaded": bool(ok)}
    except Exception as e:
        lights["ibl_error"] = "%s: %s" % (type(e).__name__, e)

    r.scene.camera.set_projection(fov_deg, 1.0, 0.001, 100.0,
                                  rn.Camera.FovType.Horizontal)
    r.scene.camera.look_at(target.tolist(), eye.tolist(), up.tolist())

    t0 = time.perf_counter()
    img = r.render_to_image()
    seconds = time.perf_counter() - t0
    dest = out / "cable_clip_front_open3d.png"
    o3d.io.write_image(str(dest), img)
    print(json.dumps({
        "render_s": round(seconds, 3), "file": str(dest),
        "lights": lights, "fov_deg": round(fov_deg, 2),
        "eye": [round(v, 3) for v in eye],
        "vertices": len(mesh.vertices), "triangles": len(mesh.triangles),
    }, indent=1))

    base = BENCH / "parity1000" / "cable_clip-01-a529057e" / "cycles-cpu" / "r1" / "cable_clip-01-a529057e_front.png"
    if base.is_file() and dest.is_file():
        from skimage.color import deltaE_ciede2000, rgb2lab
        mask_path = (BENCH / "parity1000" / "cable_clip-01-a529057e" /
                     "cycles-cpu" / "mask" / "cable_clip-01-a529057e_front_mask.png")
        a_im = np.asarray(Image.open(base).convert("RGB"), dtype=float) / 255
        b_im = np.asarray(Image.open(dest).convert("RGB"), dtype=float) / 255
        if a_im.shape == b_im.shape:
            if mask_path.is_file():
                m = np.asarray(Image.open(mask_path).split()[-1]) > 127
            else:
                m = np.ones(a_im.shape[:2], bool)
            de = deltaE_ciede2000(rgb2lab(a_im)[m], rgb2lab(b_im)[m])
            print("deltaE on product: mean %.2f p95 %.2f"
                  % (de.mean(), np.percentile(de, 95)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
