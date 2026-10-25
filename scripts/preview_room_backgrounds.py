"""Render a same-model before/after pair with a realistic room background.

This is a preview tool only. It never writes to artifacts/originals or the
Dashboard. The STL is imported once; the after shot changes only the scene
around it and reuses the same object, material, lights and camera.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "studio_render", ROOT / "blender" / "render.py")
studio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(studio)


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--stl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--colour", required=True)
    ap.add_argument("--scene", choices=["keyboard", "cables"], required=True)
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--res", type=int, default=1000)
    return ap.parse_args(argv)


def desk_material():
    mat = studio._simple_material("Grey laminate", (126, 130, 134), 0.72)
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    tex = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 260.0
    noise.inputs["Detail"].default_value = 3.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.18
    ramp.color_ramp.elements[0].color = studio._srgb((70, 74, 78))
    ramp.color_ramp.elements[1].position = 0.82
    ramp.color_ramp.elements[1].color = studio._srgb((105, 109, 113))
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.00012
    nt.links.new(tex.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def cable(name, points, radius=0.0022, colour=(38, 41, 44)):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.resolution_u = 20
    data.bevel_depth = radius
    data.bevel_resolution = 4
    data.use_fill_caps = True
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for point, co in zip(spline.bezier_points, points):
        point.co = co
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(studio._simple_material(
        name + " rubber", colour, 0.58))
    return obj


def key_label(root, body, x, y, mat):
    data = bpy.data.curves.new("Key legend", "FONT")
    data.body = body
    data.size = 0.0035
    data.align_x = "CENTER"
    data.align_y = "CENTER"
    obj = bpy.data.objects.new("Key legend", data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = root
    obj.location = (x, y, 0.0188)
    obj.data.materials.append(mat)


def keyboard(origin):
    root = bpy.data.objects.new("Keyboard", None)
    bpy.context.scene.collection.objects.link(root)
    root.location = origin
    root.rotation_euler.z = math.radians(-7)
    base_mat = studio._simple_material("Keyboard aluminium", (154, 158, 162), 0.36, 0.35)
    key_mat = studio._simple_material("Warm white keycaps", (205, 206, 204), 0.42)
    legend_mat = studio._simple_material("Key legends", (70, 73, 76), 0.65)
    base = studio._rounded_box("Keyboard base", (0.36, 0.125, 0.014), 0.005)
    base.parent = root
    base.location = (0, 0, 0.007)
    base.data.materials.append(base_mat)
    legends = {1: "ZXCVBNM,./", 2: "ASDFGHJKL;", 3: "QWERTYUIOP", 4: "1234567890"}
    for row in range(5):
        for col in range(14):
            if row == 0 and 4 <= col <= 9:
                continue
            x, y = (col - 6.5) * 0.023, (row - 2) * 0.021
            key = studio._rounded_box("Key", (0.019, 0.017, 0.0045), 0.0015)
            key.parent = root
            key.location = (x, y, 0.0165)
            key.data.materials.append(key_mat)
            if col < len(legends.get(row, "")):
                key_label(root, legends[row][col], x, y, legend_mat)
    space = studio._rounded_box("Space bar", (0.132, 0.017, 0.0045), 0.0015)
    space.parent = root
    space.location = (0, -0.042, 0.0165)
    space.data.materials.append(key_mat)
    bpy.context.view_layer.update()
    start = root.matrix_world @ Vector((0.08, 0.064, 0.009))
    cable("Keyboard cable", [start,
          start + Vector((0.025, 0.07, -0.005)),
          start + Vector((-0.02, 0.15, -0.006)),
          start + Vector((0.08, 0.23, -0.006))], radius=0.0018)


def usb_connector(location, angle=0.0):
    body = studio._rounded_box("USB connector", (0.017, 0.008, 0.006), 0.0015)
    body.location = location
    body.rotation_euler.z = angle
    body.data.materials.append(studio._simple_material(
        "Connector plastic", (32, 34, 37), 0.48))
    tip = studio._rounded_box("USB metal tip", (0.008, 0.007, 0.003), 0.0005)
    offset = Vector((0.011, 0, -0.001))
    offset.rotate(body.rotation_euler)
    tip.location = Vector(location) + offset
    tip.rotation_euler.z = angle
    tip.data.materials.append(studio._simple_material(
        "Connector nickel", (165, 169, 173), 0.28, 0.85))


def add_room(kind):
    desk = studio.add_box("Desk", (1.5, 1.3, 0.03), (0, 0, -0.015),
                          desk_material(), bevel=0.0015)
    desk.name = "Background desk"
    studio.add_box("Background wall", (3.0, 0.02, 2.0), (0, 0.72, 0.98),
                   studio._wall_material())
    if kind == "keyboard":
        keyboard((-0.14, 0.22, 0.0))
        cable("Loose desk cable", [
            (-0.24, 0.07, 0.0024), (-0.18, 0.12, 0.0024),
            (-0.22, 0.19, 0.0024), (-0.29, 0.25, 0.0024),
            (-0.25, 0.34, 0.0024)])
        usb_connector((0.18, 0.095, 0.003), angle=-0.35)
        cable("USB lead", [
            (0.18, 0.095, 0.0025), (0.22, 0.13, 0.0025),
            (0.19, 0.21, 0.0025), (0.25, 0.30, 0.0025)])
    else:
        cable("Background cable A", [
            (-0.075, 0.018, 0.0026), (-0.040, 0.030, 0.0026),
            (-0.024, 0.056, 0.0026), (-0.047, 0.084, 0.0026),
            (-0.083, 0.103, 0.0026)])
        cable("Background cable B", [
            (0.055, 0.014, 0.0022), (0.029, 0.033, 0.0022),
            (0.039, 0.058, 0.0022), (0.071, 0.074, 0.0022)])
        usb_connector((0.027, 0.060, 0.0032), angle=1.05)


def signature(obj, cam):
    return {
        "mesh_pointer": obj.data.as_pointer(),
        "material_pointer": obj.data.materials[0].as_pointer(),
        "model_matrix": [list(row) for row in obj.matrix_world],
        "camera_matrix": [list(row) for row in cam.matrix_world],
    }


def main():
    a = args()
    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    obj = studio.import_stl(a.stl)
    studio.add_plastic(obj, a.colour)
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners),
                 min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners),
                 max(c.z for c in corners)))
    size = hi - lo
    target = Vector((0, 0, size.z * 0.48))
    sweep = studio.add_sweep([222, 217, 212])
    world = bpy.data.worlds.new("Room preview world")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs[0].default_value = studio._srgb([222, 217, 212])
    bg.inputs[1].default_value = 0.32
    studio.add_area("Key", (-1.3, -1.3, 1.7), 220, 1.1, target,
                    (1.0, 0.97, 0.93))
    studio.add_area("Fill", (1.4, -1.0, 1.1), 70, 1.54, target,
                    (0.93, 0.96, 1.0))
    studio.add_area("Rim", (0.2, 1.5, 1.8), 130, 0.77, target)
    cam = studio.camera_for(scene, studio.DEFAULTS)
    studio.fit_camera(cam, corners, target, 34, 28, 0.64)
    cam.data.dof.use_dof = True
    cam.data.dof.focus_distance = (cam.location - target).length
    cam.data.dof.aperture_fstop = 16.0 if max(size.x, size.y) > 0.06 else 32.0
    cam.data.dof.aperture_blades = 7
    engine_info = studio.configure_engine(scene, "cycles-gpu", a.samples)
    scene.render.resolution_x = a.res
    scene.render.resolution_y = a.res
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -1.2
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    before = out / (a.slug + "_before.png")
    after = out / (a.slug + "_after.png")
    bpy.context.view_layer.update()
    before_signature = signature(obj, cam)

    scene.render.filepath = str(before)
    bpy.ops.render.render(write_still=True)

    sweep.hide_render = True
    add_room(a.scene)
    bpy.context.view_layer.update()
    if signature(obj, cam) != before_signature:
        raise RuntimeError("model, material or camera changed between shots")
    scene.render.filepath = str(after)
    bpy.ops.render.render(write_still=True)

    report = {
        "stl": str(Path(a.stl).resolve()),
        "stl_sha256": hashlib.sha256(Path(a.stl).read_bytes()).hexdigest(),
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "dims_mm": [round(v * 1000, 2) for v in size],
        "material": obj.data.materials[0].name,
        "same_model_material_lights_camera": True,
        "background": a.scene,
        "before": str(before),
        "after": str(after),
        "engine": engine_info,
        "samples": a.samples,
        "resolution": a.res,
        "seconds": round(time.time() - t0, 1),
    }
    (out / (a.slug + "_comparison.json")).write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print("COMPARISON_JSON:" + json.dumps(report))


if __name__ == "__main__":
    main()
