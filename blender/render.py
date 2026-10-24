"""Blender product shots of one exported STL.

Run by origin/shots.py:
  blender.exe -b --factory-startup --python blender/render.py -- \
      --stl model.stl --out shots/ --slug arc-cable-clip --style style_v1.json

The scene is a small studio: a curved paper sweep, three soft area lights and
a 70 mm camera. The geometry is the STL that gets uploaded; nothing is added,
moved or replaced between the render and the export.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

DEFAULTS = {
    "angles_deg": [[-38, 22], [0, 13], [34, 28], [18, 55]],
    "detail_angle": [26, 36],
    "focal_mm": 70,
    "sensor_mm": 36,
    "subject_fill": 0.64,
    "background_rgb": [222, 217, 212],
    "lighting": {"key_w": 60, "fill_w": 18, "rim_w": 32,
                 "softbox_m": 0.35, "world_strength": 0.35},
}


def _srgb(hex_or_rgb):
    if isinstance(hex_or_rgb, str):
        h = hex_or_rgb.lstrip("#")
        vals = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    else:
        vals = [v / 255.0 for v in hex_or_rgb[:3]]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
                 for v in vals) + (1.0,)


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--stl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--colour", default="#BF604E")
    ap.add_argument("--style", default="")
    ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--res", type=int, default=1400)
    ap.add_argument("--device", default="CPU", choices=["CPU", "GPU"])
    return ap.parse_args(argv)


def style_data(path: str) -> dict:
    data = dict(DEFAULTS)
    if path and Path(path).exists():
        preset = json.loads(Path(path).read_text(encoding="utf-8"))
        data.update(preset.get("render") or {})
    return data


def set_input(node, name, value):
    if name in node.inputs:
        node.inputs[name].default_value = value


def add_sweep(bg_rgb):
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
            # tangent_x x tangent_profile = the interior normal (up / -Y)
            faces.append((a, b, c, d))
    mesh = bpy.data.meshes.new("Sweep")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Sweep", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mat = bpy.data.materials.new("SweepPaper")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    set_input(bsdf, "Base Color", _srgb(bg_rgb))
    set_input(bsdf, "Roughness", 0.92)
    set_input(bsdf, "Specular IOR Level", 0.15)
    obj.data.materials.append(mat)
    try:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(28))
        obj.select_set(False)
    except Exception:
        pass
    return obj


def add_area(name, loc, energy, size, target, color=(1.0, 1.0, 1.0)):
    data = bpy.data.lights.new(name, type="AREA")
    data.energy = energy
    data.shape = "SQUARE"
    data.size = size
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = loc
    direction = Vector(target) - Vector(loc)
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


def add_plastic(obj, colour, layer_period_mm=0.2):
    mat = bpy.data.materials.new("Print")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, "Base Color", _srgb(colour))
    set_input(bsdf, "Roughness", 0.46)
    set_input(bsdf, "Specular IOR Level", 0.45)
    set_input(bsdf, "Coat Weight", 0.12)
    set_input(bsdf, "Coat Roughness", 0.35)
    tex = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    wave = nt.nodes.new("ShaderNodeTexWave")
    bump = nt.nodes.new("ShaderNodeBump")
    tex.location = (-1000, -300)
    mapping.location = (-800, -300)
    wave.location = (-600, -300)
    bump.location = (-350, -300)
    wave.wave_type = "BANDS"
    wave.bands_direction = "Z"
    wave.inputs["Scale"].default_value = 1000.0 / layer_period_mm
    wave.inputs["Distortion"].default_value = 0.6
    wave.inputs["Detail"].default_value = 1.0
    bump.inputs["Strength"].default_value = 0.10
    bump.inputs["Distance"].default_value = 0.00008
    nt.links.new(tex.outputs["Object"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
    nt.links.new(wave.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def import_stl(path):
    try:
        bpy.ops.wm.stl_import(filepath=path)
    except Exception:
        bpy.ops.import_mesh.stl(filepath=path)
    meshes = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if not meshes:
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"
                  and o.name != "Sweep"]
    obj = meshes[0]
    obj.scale = (0.001, 0.001, 0.001)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.context.view_layer.update()
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners),
                 min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners),
                 max(c.z for c in corners)))
    obj.location += Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
    bpy.context.view_layer.update()
    try:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(32))
    except Exception:
        bpy.ops.object.shade_smooth()
    return obj


def camera_for(scene, style):
    data = bpy.data.cameras.new("Camera")
    data.lens = float(style.get("focal_mm", 70))
    data.sensor_width = float(style.get("sensor_mm", 36))
    data.clip_start = 0.001
    data.clip_end = 100.0
    cam = bpy.data.objects.new("Camera", data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def fit_camera(cam, corners, target, azimuth, elevation, fill, lens=None):
    if lens:
        cam.data.lens = lens
    az, el = math.radians(azimuth), math.radians(elevation)
    to_cam = Vector((math.sin(az) * math.cos(el),
                     -math.cos(az) * math.cos(el), math.sin(el)))
    rot = (-to_cam).to_track_quat("-Z", "Y").to_matrix()
    right, up = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
    fov = 2 * math.atan(cam.data.sensor_width / (2 * cam.data.lens))
    tan = math.tan(fov / 2)
    # Solve the perspective fit per corner instead of adding a bbox depth to a
    # flat distance. The old shortcut shrank every compact part to ~half the
    # frame; this places the limiting corner exactly at `fill` of the frame.
    need = 0.02
    for c in corners:
        p = c - target
        z = p.dot(to_cam)                 # positive = nearer the camera
        need = max(need,
                   abs(p.dot(right)) / (fill * tan) + z,
                   abs(p.dot(up)) / (fill * tan) + z)
    cam.location = target + to_cam * need
    cam.rotation_euler = (-to_cam).to_track_quat("-Z", "Y").to_euler()
    return need


def main():
    a = args()
    t0 = time.time()
    style = style_data(a.style)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    obj = import_stl(a.stl)
    add_plastic(obj, a.colour)
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners),
                 min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners),
                 max(c.z for c in corners)))
    size = hi - lo
    S = max(size.x, size.y, size.z)
    add_sweep(style.get("background_rgb", [222, 217, 212]))

    world = bpy.data.worlds.new("Studio")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs[0].default_value = _srgb(style.get("background_rgb", [222, 217, 212]))
    bg.inputs[1].default_value = float(style["lighting"].get("world_strength", 0.35))

    target = Vector((0, 0, size.z * 0.48))
    # Fixed studio distances, not distances proportional to the part: a 2 cm
    # clip and a 14 cm stand are photographed in the same soft box. Scaling the
    # lights to the part put them centimetres from the surface and turned a sage
    # print almost black.
    soft = float(style["lighting"].get("softbox_m", 1.1))
    add_area("Key", (-1.3, -1.3, 1.7), float(style["lighting"].get("key_w", 220)),
             soft, target, (1.0, 0.97, 0.93))
    add_area("Fill", (1.4, -1.0, 1.1), float(style["lighting"].get("fill_w", 70)),
             soft * 1.4, target, (0.93, 0.96, 1.0))
    add_area("Rim", (0.2, 1.5, 1.8), float(style["lighting"].get("rim_w", 130)),
             soft * 0.7, target, (1.0, 1.0, 1.0))

    cam = camera_for(scene, style)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = a.samples
    scene.cycles.use_denoising = True
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.02
    scene.cycles.max_bounces = 6
    scene.cycles.device = a.device
    scene.render.resolution_x = a.res
    scene.render.resolution_y = a.res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass
    scene.view_settings.exposure = -0.8

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fill = float(style.get("subject_fill", 0.68))
    angles = list(style.get("angles_deg", DEFAULTS["angles_deg"]))
    shots = []
    names = ["hero_left", "front", "hero_right", "high"]
    for i, (az, el) in enumerate(angles[:4]):
        t = target
        fit_camera(cam, corners, t, az, el, fill)
        dest = out / ("%s_%s.png" % (a.slug, names[i]))
        scene.render.filepath = str(dest)
        t1 = time.time()
        bpy.ops.render.render(write_still=True)
        shots.append({"name": names[i], "azimuth": az, "elevation": el,
                      "file": str(dest), "seconds": round(time.time() - t1, 1)})
        print("RENDERED", names[i], shots[-1]["seconds"], "s")

    az, el = style.get("detail_angle", DEFAULTS["detail_angle"])
    detail_target = target + Vector((0, 0, size.z * 0.07))
    fit_camera(cam, corners, detail_target, az, el, 1.15,
               lens=float(style.get("focal_mm", 70)) * 1.15)
    dest = out / ("%s_detail.png" % a.slug)
    scene.render.filepath = str(dest)
    t1 = time.time()
    bpy.ops.render.render(write_still=True)
    shots.append({"name": "detail", "azimuth": az, "elevation": el,
                  "file": str(dest), "seconds": round(time.time() - t1, 1)})
    print("RENDERED detail", shots[-1]["seconds"], "s")

    manifest = {
        "stl": str(Path(a.stl).resolve()), "slug": a.slug, "name": a.name,
        "colour": a.colour, "samples": a.samples, "resolution": a.res,
        "seconds": round(time.time() - t0, 1),
        "dims_mm": [round(v * 1000, 1) for v in (size.x, size.y, size.z)],
        "shots": shots,
    }
    (out / ("%s_render.json" % a.slug)).write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    print("RENDER_JSON:" + json.dumps(manifest))


if __name__ == "__main__":
    main()
