"""Blender product shots of one exported STL.

Run by origin/shots.py:
  blender.exe -b --factory-startup --python blender/render.py -- \
      --stl model.stl --out shots/ --slug arc-cable-clip --style style_v1.json

The scene is a small studio: a curved paper sweep, three soft area lights and
a 70 mm camera. The studio photos contain only the STL that gets uploaded. The
first, in-use photo adds a plain desk or wall and simple props (a cable, a
phone, two screws) so the part is shown doing its job; the print itself is
still exactly the exported STL.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

DESK_TOP = 0.018
DESK_RGB = (196, 172, 137)
WALL_RGB = (214, 209, 200)

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
    ap.add_argument("--family", default="")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--style", default="")
    ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--res", type=int, default=1400)
    ap.add_argument("--device", default="CPU", choices=["CPU", "GPU"],
                    help="legacy switch; --engine supersedes it")
    ap.add_argument("--engine", default="",
                    choices=["", "cycles-cpu", "cycles-gpu", "eevee"],
                    help="cycles-cpu (baseline), cycles-gpu (OptiX/CUDA) "
                         "or eevee (EEVEE Next)")
    ap.add_argument("--mask", action="store_true",
                    help="bench only: transparent product-only silhouettes")
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


def _simple_material(name, colour, roughness, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    set_input(bsdf, "Base Color", _srgb(colour))
    set_input(bsdf, "Roughness", roughness)
    set_input(bsdf, "Metallic", metallic)
    return mat


def _add_bump(mat, scale=120.0, strength=0.15, distance=0.0003, stretch=None):
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    tex = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    bump = nt.nodes.new("ShaderNodeBump")
    if stretch:
        mapping.inputs["Scale"].default_value = stretch
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 4.0
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = distance
    nt.links.new(tex.outputs["Object"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])


def _wood_material():
    mat = bpy.data.materials.new("DeskOak")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, "Roughness", 0.55)
    set_input(bsdf, "Specular IOR Level", 0.35)
    tex = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (0.12, 2.0, 1.0)
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 26.0
    noise.inputs["Detail"].default_value = 7.0
    noise.inputs["Roughness"].default_value = 0.58
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.30
    ramp.color_ramp.elements[0].color = _srgb((168, 140, 106))
    ramp.color_ramp.elements[1].position = 0.72
    ramp.color_ramp.elements[1].color = _srgb((198, 174, 140))
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.06
    bump.inputs["Distance"].default_value = 0.0012
    nt.links.new(tex.outputs["Object"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def _wall_material():
    mat = _simple_material("WallPaint", WALL_RGB, 0.94)
    _add_bump(mat, scale=420.0, strength=0.06, distance=0.0004)
    return mat


def _floor_material():
    mat = _simple_material("FloorPaint", (203, 197, 189), 0.9)
    _add_bump(mat, scale=260.0, strength=0.08, distance=0.0005)
    return mat


def _screen_material():
    mat = _simple_material("PhoneScreen", (18, 21, 26), 0.14)
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    tex = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = _srgb((12, 15, 19))
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = _srgb((58, 68, 82))
    nt.links.new(tex.outputs["Generated"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def add_box(name, size, center, mat, bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
        bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.data.materials.append(mat)
    return obj


def _rounded_box(name, size, bevel):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    mod = obj.modifiers.new("bevel", "BEVEL")
    mod.width = bevel
    mod.segments = 5
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return obj


def obj_corners(*objs):
    out = []
    for obj in objs:
        out += [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return out


def mesh_bounds(obj):
    corners = obj_corners(obj)
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners),
                 min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners),
                 max(c.z for c in corners)))
    return lo, hi


def add_desk():
    desk = add_box("Desk", (1.2, 1.16, DESK_TOP),
                   (0, -0.12, DESK_TOP / 2), _wood_material(), bevel=0.0012)
    # With the paper sweep hidden, a ray past the desk's right edge reached the
    # world and showed as a dark band; the floor closes that gap.
    add_box("Floor", (8.0, 8.0, 0.002), (0, 0, -0.001), _floor_material())
    add_box("RoomWall", (20.0, 0.004, 3.0), (0, 0.472, 1.48),
            _wall_material())
    return desk


def add_wall():
    return add_box("RoomWall", (2.4, 0.004, 1.2), (0, 0.0328, 0.55),
                   _wall_material())


def add_cable(axis_z, cable_d):
    mat = _simple_material("CableRubber", (43, 45, 47), 0.62)
    _add_bump(mat, scale=900.0, strength=0.25, distance=0.00008)
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48, radius=cable_d / 2 / 1000.0 * 0.98, depth=0.5,
        location=(0, 0, axis_z), rotation=(math.pi / 2, 0, 0))
    obj = bpy.context.object
    obj.name = "Cable"
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return obj


def add_phone(p, desk_top, stand_x_min):
    D, t = p["depth"], p["back_t"]
    base_t, lip_t, groove_w = p["base_t"], p["lip_t"], p["groove_w"]
    H = base_t + (D - t - (lip_t + groove_w)) * math.tan(
        math.radians(p["angle"]))
    A = Vector((lip_t + groove_w - 2.0, base_t))
    B = Vector((D - t, H))
    u2 = (B - A).normalized()
    u = Vector((u2.x, 0.0, u2.y))
    out = Vector((-u2.y, 0.0, u2.x))
    P = Vector((stand_x_min + A.x / 1000.0, 0.0, desk_top + A.y / 1000.0))

    w, l, th = 0.0715, 0.147, 0.0078
    center = P + u * (l / 2) + out * (th / 2 + 0.00012)
    X = Vector((0.0, -1.0, 0.0))
    R = Matrix(((X.x, u.x, out.x),
                (X.y, u.y, out.y),
                (X.z, u.z, out.z))).to_4x4()

    body = _rounded_box("Phone", (w, l, th), 0.003)
    body.matrix_world = Matrix.Translation(center) @ R
    body.data.materials.append(_simple_material("PhoneGraphite",
                                                (58, 62, 67), 0.34, 0.25))

    sw, sl, st = 0.0665, 0.139, 0.0009
    scenter = center + out * (th / 2 + st / 2 + 0.0002)
    screen = _rounded_box("PhoneScreen", (sw, sl, st), 0.0012)
    screen.matrix_world = Matrix.Translation(scenter) @ R
    screen.data.materials.append(_screen_material())

    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.0022,
                                        depth=0.0004)
    hole = bpy.context.object
    hole.name = "PhoneCamera"
    hole.matrix_world = Matrix.Translation(
        scenter + u * (sl / 2 - 0.009) + out * 0.00035) @ R
    hole.data.materials.append(_simple_material("CameraGlass",
                                                (10, 11, 13), 0.2))
    return body, screen


def add_screws(x_min, plate_t, zs):
    steel = _simple_material("Steel", (150, 154, 158), 0.3, 0.85)
    slot_mat = _simple_material("ScrewSlot", (20, 20, 22), 0.5)
    for z in zs:
        y = -(x_min + (plate_t - 1.1) / 1000.0)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=32, radius=0.0041, depth=0.0012,
            location=(0, y, z / 1000.0), rotation=(math.pi / 2, 0, 0))
        head = bpy.context.object
        head.name = "ScrewHead"
        head.data.materials.append(steel)
        bpy.ops.object.shade_smooth()
        add_box("ScrewSlot", (0.0009, 0.0002, 0.0036),
                (0, y - 0.00055, z / 1000.0), slot_mat)


def setup_context(family, p, obj, sweep, rim, style):
    """Add the desk or wall and props; return the framing for the first photo."""
    if family == "wall_hook":
        sweep.hide_render = True
        rim.hide_render = True
        obj.rotation_euler[2] = -math.pi / 2
        bpy.context.view_layer.update()
        lo, hi = mesh_bounds(obj)
        add_wall()
        plate_h = p.get("plate_h", 89.0)
        add_screws(lo.x, p.get("plate_t", 5.0),
                   [plate_h * 0.20, plate_h * 0.78])
        return obj_corners(obj), (lo + hi) / 2, 38.0, 12.0, 0.62

    obj.location.z += DESK_TOP
    sweep.hide_render = True
    rim.hide_render = True
    add_desk()
    # A broad, soft wash from the camera side keeps the prop's shadow on the
    # room wall from turning into a hard dark slab behind the product.
    add_area("RoomWash", (0.0, -1.8, 0.9), 120, 2.0,
             Vector((0, 0.47, 0.25)), (1.0, 0.99, 0.97))
    if family == "cable_clip":
        cy = (p.get("base_t", 2.85) + p.get("cable_d", 10.0) / 2
              + p.get("wall", 3.37) - 1.0) / 1000.0
        add_cable(DESK_TOP + cy, p.get("cable_d", 10.0))
        lo, hi = mesh_bounds(obj)
        return obj_corners(obj), (lo + hi) / 2, -42.0, 18.0, 0.75
    if family == "phone_stand":
        lo, _ = mesh_bounds(obj)
        phone, _screen = add_phone(p, DESK_TOP, lo.x)
        corners = obj_corners(obj, phone)
        lo2 = Vector((min(c.x for c in corners), min(c.y for c in corners),
                      min(c.z for c in corners)))
        hi2 = Vector((max(c.x for c in corners), max(c.y for c in corners),
                      max(c.z for c in corners)))
        return corners, (lo2 + hi2) / 2, -62.0, 20.0, 0.62
    lo, hi = mesh_bounds(obj)
    return obj_corners(obj), (lo + hi) / 2, -42.0, 20.0, 0.62


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


def _cycles(scene, samples: int, device: str) -> dict:
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.02
    scene.cycles.max_bounces = 6
    scene.cycles.device = device
    info = {"device": device}
    if device != "GPU":
        return info
    # OptiX first, CUDA second. Only the GPU is enabled: a CPU+GPU hybrid
    # render on this 4-thread laptop adds sync cost and starves the desktop.
    prefs = bpy.context.preferences.addons["cycles"].preferences
    compute = ""
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            if any(d.type in ("OPTIX", "CUDA") for d in prefs.devices):
                compute = kind
                break
        except Exception:
            continue
    if not compute:
        # Never pretend a GPU run happened: fall back and say so.
        scene.cycles.device = "CPU"
        info.update(device="CPU", fallback="no OptiX/CUDA device")
        return info
    for d in prefs.devices:
        d.use = d.type == compute
    info.update(compute=compute,
                devices=[d.name for d in prefs.devices if d.use])
    return info


def configure_engine(scene, engine: str, samples: int) -> dict:
    """Point the scene at one measured engine and report what it settled on."""
    if engine == "eevee":
        scene.render.engine = "BLENDER_EEVEE_NEXT"
        e = scene.eevee
        e.taa_render_samples = samples
        # Screen-space ray tracing plus fast GI is what keeps the sweep and
        # the part's bounce light close to Cycles; without both they go flat.
        e.use_raytracing = True
        e.ray_tracing_method = "SCREEN"
        e.use_fast_gi = True
        e.fast_gi_method = "GLOBAL_ILLUMINATION"
        try:
            e.ray_tracing_options.resolution_scale = "1"
            e.ray_tracing_options.use_denoise = True
            e.ray_tracing_options.trace_max_roughness = 0.5
        except Exception:
            pass
        e.shadow_ray_count = 2
        e.shadow_step_count = 6
        # Area lights need jitter for a soft shadow at the sweep contact.
        for light in bpy.data.lights:
            if light.type == "AREA":
                try:
                    light.use_shadow_jitter = True
                except Exception:
                    pass
        return {"engine": "eevee", "device": "GPU", "samples": samples}
    device = "GPU" if engine == "cycles-gpu" else "CPU"
    info = _cycles(scene, samples, device)
    info.update(engine=engine, samples=samples)
    return info


def main():
    a = args()
    t0 = time.time()
    style = style_data(a.style)
    try:
        params = json.loads(a.params)
    except Exception:
        params = {}
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
    sweep = add_sweep(style.get("background_rgb", [222, 217, 212]))

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
    rim = add_area("Rim", (0.2, 1.5, 1.8),
                   float(style["lighting"].get("rim_w", 130)),
                   soft * 0.7, target, (1.0, 1.0, 1.0))

    cam = camera_for(scene, style)
    engine = a.engine or ("cycles-gpu" if a.device == "GPU" else "cycles-cpu")
    # A silhouette is geometry coverage, not light: one sample is exact for
    # the bench mask pass and keeps it cheap.
    samples = 1 if a.mask else a.samples
    engine_info = configure_engine(scene, engine, samples)
    if a.mask:
        try:
            scene.cycles.use_denoising = False
            scene.eevee.taa_render_samples = 1
            scene.eevee.use_raytracing = False
        except Exception:
            pass
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
    studio = []
    fits = []
    # The old left-hero angle is replaced by the in-use photo, so five photos
    # stay five and the first one shows what the part does.
    names = ["front", "hero_right", "high"]
    for i, (az, el) in enumerate(angles[1:4]):
        fits.append((names[i], corners, target, az, el, fill, None))
        if a.mask:
            continue
        fit_camera(cam, corners, target, az, el, fill)
        dest = out / ("%s_%s.png" % (a.slug, names[i]))
        scene.render.filepath = str(dest)
        t1 = time.time()
        bpy.ops.render.render(write_still=True)
        studio.append({"name": names[i], "azimuth": az, "elevation": el,
                       "file": str(dest), "seconds": round(time.time() - t1, 1)})
        print("RENDERED", names[i], studio[-1]["seconds"], "s")

    az, el = style.get("detail_angle", DEFAULTS["detail_angle"])
    detail_target = target + Vector((0, 0, size.z * 0.07))
    detail_lens = float(style.get("focal_mm", 70)) * 1.15
    fits.append(("detail", corners, detail_target, az, el, 1.15, detail_lens))
    if not a.mask:
        fit_camera(cam, corners, detail_target, az, el, 1.15,
                   lens=detail_lens)
        dest = out / ("%s_detail.png" % a.slug)
        scene.render.filepath = str(dest)
        t1 = time.time()
        bpy.ops.render.render(write_still=True)
        studio.append({"name": "detail", "azimuth": az, "elevation": el,
                       "file": str(dest), "seconds": round(time.time() - t1, 1)})
        print("RENDERED detail", studio[-1]["seconds"], "s")

    ctx_corners, ctx_target, ctx_az, ctx_el, ctx_fill = setup_context(
        a.family, params, obj, sweep, rim, style)
    bpy.context.view_layer.update()
    lens = float(style.get("focal_mm", 70))
    fits.append(("in_use", ctx_corners, ctx_target, ctx_az, ctx_el,
                 ctx_fill, lens))
    in_use = None
    if not a.mask:
        fit_camera(cam, ctx_corners, ctx_target, ctx_az, ctx_el, ctx_fill,
                   lens=lens)
        dest = out / ("%s_in_use.png" % a.slug)
        scene.render.filepath = str(dest)
        t1 = time.time()
        bpy.ops.render.render(write_still=True)
        in_use = {"name": "in_use", "azimuth": ctx_az, "elevation": ctx_el,
                  "file": str(dest), "seconds": round(time.time() - t1, 1)}
        print("RENDERED in_use", in_use["seconds"], "s")

    if a.mask:
        # Product only, transparent film: alpha is the silhouette the parity
        # bench compares. Lights and props are hidden; alpha ignores them.
        for o in scene.objects:
            if o is not obj and o.type != "CAMERA":
                o.hide_render = True
        scene.render.film_transparent = True
        scene.render.image_settings.color_mode = "RGBA"
        for name, cs, tgt, maz, mel, mfill, mlens in fits:
            fit_camera(cam, cs, tgt, maz, mel, mfill, lens=mlens)
            dest = out / ("%s_%s_mask.png" % (a.slug, name))
            scene.render.filepath = str(dest)
            bpy.ops.render.render(write_still=True)
            print("MASKED", name)

    manifest = {
        "stl": str(Path(a.stl).resolve()), "slug": a.slug, "name": a.name,
        "family": a.family,
        "colour": a.colour, "samples": a.samples, "resolution": a.res,
        "engine": engine_info.get("engine", engine),
        "device": engine_info.get("device", a.device),
        "engine_info": engine_info,
        "mask": bool(a.mask),
        "seconds": round(time.time() - t0, 1),
        "dims_mm": [round(v * 1000, 1) for v in (size.x, size.y, size.z)],
        "shots": [] if a.mask else [in_use] + studio,
    }
    (out / ("%s_render.json" % a.slug)).write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    print("RENDER_JSON:" + json.dumps(manifest))


if __name__ == "__main__":
    main()
