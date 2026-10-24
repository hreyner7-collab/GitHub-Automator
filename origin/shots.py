"""Turn a built model into listing photos and a contact sheet.

Blender does the photography; this module owns the file layout, the 4:3 and
3:4 crops the upload form needs, and the contact sheet the owner approves.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

from origin import export

ROOT = Path(__file__).resolve().parent.parent
RENDER_PY = ROOT / "blender" / "render.py"
STYLE = ROOT / "origin" / "presets" / "style_v1.json"

# Measured on this laptop (3 models x 5 shots at 1000 px): cycles-gpu is
# pixel-identical to the Cycles CPU baseline and ~3.75x faster. One-line
# rollback: set this back to "cycles-cpu".
DEFAULT_ENGINE = "cycles-gpu"
# Cycles keeps its shipped 28 spp; EEVEE needs more TAA samples to settle.
ENGINE_SAMPLES = {"cycles-cpu": 28, "cycles-gpu": 28, "eevee": 64}


class RenderError(RuntimeError):
    pass


def find_blender() -> Path:
    env = Path(shutil.which("blender") or "")
    if env.is_file():
        return env
    for p in sorted((ROOT / "tools" / "blender").glob("*/blender.exe")):
        if p.is_file():
            return p
    raise RenderError("no Blender found under tools/blender (and none on PATH)")


def _blender_cmd(exe, stl: Path, outdir: Path, spec, samples: int, res: int,
                 style: Path, engine: str = DEFAULT_ENGINE) -> list[str]:
    return [str(exe), "-b", "--factory-startup", "-noaudio",
            "--python", str(RENDER_PY), "--",
            "--stl", str(stl), "--out", str(outdir), "--slug", spec.key,
            "--name", spec.title, "--colour", spec.colour_hex,
            "--family", spec.family, "--params", json.dumps(spec.params),
            "--style", str(style), "--samples", str(samples),
            "--res", str(res), "--engine", engine]


def _run_blender(stl: Path, outdir: Path, spec, samples: int, res: int,
                 style: Path, engine: str = DEFAULT_ENGINE,
                 timeout: int = 1200) -> dict:
    exe = find_blender()
    stl, outdir, style = (Path(stl).resolve(), Path(outdir).resolve(),
                          Path(style).resolve())
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = _blender_cmd(exe, stl, outdir, spec, samples, res, style, engine)
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise RenderError("Blender failed (%d):\n%s" % (
            p.returncode, (p.stdout + p.stderr)[-2500:]))
    line = ""
    for ln in p.stdout.splitlines():
        if ln.startswith("RENDER_JSON:"):
            line = ln[len("RENDER_JSON:"):]
    if not line:
        raise RenderError("Blender produced no manifest:\n%s"
                          % (p.stdout + p.stderr)[-2000:])
    man = json.loads(line)
    man["wall_seconds"] = round(time.time() - t0, 1)
    for shot in man["shots"]:
        if not Path(shot["file"]).is_file() or Path(shot["file"]).stat().st_size < 5000:
            raise RenderError("render missing or empty: %s" % shot["file"])
    return man


def _crops(png: Path, dest: Path, stem: str, box_name: str) -> list[Path]:
    im = Image.open(png).convert("RGB")
    w, h = im.size
    out = []
    for ratio, tag in ((4 / 3, "4x3"), (3 / 4, "3x4")):
        if w / h > ratio:
            nw, nh = int(round(h * ratio)), h
        else:
            nw, nh = w, int(round(w / ratio))
        x0, y0 = (w - nw) // 2, (h - nh) // 2
        crop = im.crop((x0, y0, x0 + nw, y0 + nh))
        p = dest / ("%s_%s_%s.jpg" % (stem, box_name, tag))
        crop.save(p, "JPEG", quality=93)
        out.append(p)
    return out


def _font(size: int, bold: bool = False):
    names = ("segoeuib.ttf", "segoeui.ttf") if bold else ("segoeui.ttf", "arial.ttf")
    for n in names:
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def contact_sheet(spec, dims, check, photos: list[Path], dest: Path,
                  licence: str = "CC BY 4.0",
                  labels: list[str] | None = None) -> Path:
    W, H = 2400, 1500
    canvas = Image.new("RGB", (W, H), (16, 18, 21))
    d = ImageDraw.Draw(canvas)
    d.text((70, 55), spec.title, fill=(241, 243, 246), font=_font(64, True))
    sub = "%s  ·  %s  ·  %s filament" % (spec.label, "Original design",
                                        spec.colour_name)
    d.text((72, 140), sub, fill=(155, 166, 178), font=_font(32))

    cells = [(60, 210), (840, 210), (1620, 210), (60, 800), (840, 800)]
    labels = labels or ["Hero", "Front", "Side 3/4", "High 3/4", "Detail"]
    for (x, y), label, p in zip(cells, labels, photos):
        im = Image.open(p).convert("RGB")
        im = im.resize((700, 525))
        canvas.paste(im, (x, y))
        d.rectangle([x, y, x + 700, y + 525], outline=(48, 53, 60), width=2)
        d.text((x + 4, y + 537), label, fill=(180, 190, 200),
               font=_font(26))

    x, y = 1620, 800
    d.rounded_rectangle([x, y, x + 700, y + 525], radius=18, fill=(25, 28, 33),
                        outline=(48, 53, 60), width=2)
    lines = [
        ("Size", "%s x %s x %s mm" % tuple("%.1f" % v for v in dims)),
        ("Volume", "%.1f cm³" % (check.metrics["volume_mm3"] / 1000.0)),
        ("Est. mass", "%.0f g in PLA" % check.metrics["est_mass_pla_g"]),
        ("Print", check.metrics["print_orientation"]),
        ("Supports", "yes, small areas" if check.supports_needed else "none needed"),
        ("Licence", licence),
        ("Source", "none — designed here"),
    ]
    ty = y + 34
    for k, v in lines:
        d.text((x + 34, ty), k.upper(), fill=(126, 140, 155), font=_font(22, True))
        d.text((x + 34, ty + 30), v, fill=(231, 235, 240), font=_font(30))
        ty += 68
    d.text((70, 1400),
           "Every photo is a render of the exported STL. The in-use shot adds "
           "plain props for scale; no designer's image, model or listing was "
           "reused.", fill=(126, 140, 155), font=_font(26))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=92)
    return dest


SHOT_ORDER = {"in_use": 0, "front": 1, "hero_right": 2, "high": 3,
              "detail": 4}


def order_shots(shots: list[dict]) -> list[dict]:
    """In-use first: it is the photo that shows what the part does."""
    return sorted(shots, key=lambda s: (SHOT_ORDER.get(s["name"], 9),
                                        s["name"]))


def make_photos(mesh, spec, check, dims, outdir: Path, samples: int | None = None,
                res: int = 1400, style: Path = STYLE,
                licence: str = "CC BY 4.0",
                engine: str = DEFAULT_ENGINE) -> dict:
    if engine not in ENGINE_SAMPLES:
        raise RenderError("unknown render engine %r (choose %s)"
                          % (engine, sorted(ENGINE_SAMPLES)))
    samples = int(samples or ENGINE_SAMPLES[engine])
    outdir = Path(outdir).resolve()
    files = export.export_model(mesh, outdir / "files", spec.key, spec.title,
                                licence=licence)
    if not files["three_mf_ok"]:
        raise RenderError("3MF failed: %s" % files["three_mf_detail"])
    shots_dir = outdir / "renders"
    man = _run_blender(Path(files["stl"]), shots_dir, spec, samples, res, style,
                       engine)
    man["shots"] = order_shots(man["shots"])

    photos_dir = outdir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    crops_43, crops_34 = [], []
    for shot in man["shots"]:
        a, b = _crops(Path(shot["file"]), photos_dir, spec.key, shot["name"])
        crops_43.append(a)
        crops_34.append(b)
    label_map = {"in_use": "In use", "front": "Front",
                 "hero_right": "Side 3/4", "high": "High 3/4",
                 "detail": "Detail"}
    labels = [label_map.get(s["name"], s["name"].replace("_", " ").title())
              for s in man["shots"]]
    sheet = contact_sheet(spec, dims, check, crops_43,
                          outdir / ("%s_contact.jpg" % spec.key), licence,
                          labels=labels)

    stats = {}
    for p in crops_43:
        im = Image.open(p).convert("L")
        st = ImageStat.Stat(im)
        stats[p.name] = {"mean": round(st.mean[0], 1),
                         "std": round(st.stddev[0], 1),
                         "min": int(im.getextrema()[0]),
                         "max": int(im.getextrema()[1])}
    # A black or blank frame must never reach a listing: the record is written
    # last, so raising here leaves the folder incomplete rather than "ready".
    for name, st in stats.items():
        if st["mean"] < 8 or st["std"] < 2.0:
            raise RenderError("blank render: %s (mean %.1f, std %.1f)"
                              % (name, st["mean"], st["std"]))

    out = {
        "spec": spec.as_dict(), "files": files, "render": man,
        "engine": man.get("engine"),
        "photos_4x3": [str(p) for p in crops_43],
        "photos_3x4": [str(p) for p in crops_34],
        "contact": str(sheet), "brightness": stats,
        "dims_mm": [round(float(v), 1) for v in dims],
        "check": check.as_dict(),
        "licence": licence,
    }
    (outdir / ("%s_photos.json" % spec.key)).write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    return out
