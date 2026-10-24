#!/usr/bin/env python
"""Measure Blender engines on the same STL, cameras and style.

Every run is a fresh `blender -b` process, exactly like origin/shots.py. The
bench copies the STLs into artifacts/bench/<stamp>/ and never writes to
artifacts/originals. It is browser-free and touches no account.

Usage
-----
    python scripts/bench_renderers.py --triage
    python scripts/bench_renderers.py --models all --repeats 3 --parity
    python scripts/bench_renderers.py --dry-run

Triage runs one model once per engine. Parity runs one product-only mask pass
per engine/model, compares silhouettes and CIEDE2000 against cycles-cpu, and
writes one comparison sheet per model (artifacts/bench/<stamp>/<key>_compare.jpg).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from skimage.color import deltaE_ciede2000, rgb2lab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from origin.shots import find_blender  # noqa: E402

STYLE = ROOT / "origin" / "presets" / "style_v1.json"
RENDER_PY = ROOT / "blender" / "render.py"
ORIGINALS = ROOT / "artifacts" / "originals"
BENCH = ROOT / "artifacts" / "bench"

MODELS = ["cable_clip-01-a529057e", "phone_stand-01-86eff01c",
          "parts_tray-02-d621be41"]
ENGINES = ["cycles-cpu", "cycles-gpu", "eevee"]
# Cycles keeps the shipped 28 spp; EEVEE's TAA needs more samples than Cycles
# to settle soft shadows, so it gets 64.
SAMPLES = {"cycles-cpu": 28, "cycles-gpu": 28, "eevee": 64}
SHOTS = ["in_use", "front", "hero_right", "high", "detail"]
GATES = {
    "iou_min": 0.975,
    "bbox_px_max": 3,
    "de_part_mean": 3.0,
    "de_part_p95": 6.0,
    "de_bg_mean": 2.0,
    "de_bg_p95": 4.0,
}


# ---------------------------------------------------------------- measuring
class Jitter(threading.Thread):
    """How late a 50 ms sleep returns: a cheap desktop-responsiveness probe."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.late_ms = []

    def run(self):
        interval = 0.05
        last = time.perf_counter()
        while not self.stop.wait(interval):
            now = time.perf_counter()
            self.late_ms.append(max(0.0, now - last - interval) * 1000)
            last = now

    @property
    def worst_ms(self):
        return round(max(self.late_ms, default=0.0), 1)

    @property
    def p95_ms(self):
        return round(float(np.percentile(self.late_ms, 95)), 1) if self.late_ms else 0.0


def gpu_mem_mb() -> int | None:
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        return int(p.stdout.strip().splitlines()[0])
    except Exception:
        return None


def load_spec(key: str) -> dict:
    path = ORIGINALS / key / ("%s_photos.json" % key)
    data = json.loads(path.read_text(encoding="utf-8"))
    spec = data.get("spec") or {}
    if not spec.get("key"):
        spec["key"] = key
    return spec


def copy_stl(key: str, bench_root: Path) -> Path:
    src = ORIGINALS / key / "files" / ("%s.stl" % key)
    dest = bench_root / key / "model.stl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest


def render_cmd(stl: Path, outdir: Path, spec: dict, engine: str, res: int,
               mask: bool = False) -> list[str]:
    cmd = [str(find_blender()), "-b", "--factory-startup", "-noaudio",
           "--python", str(RENDER_PY), "--",
           "--stl", str(stl), "--out", str(outdir), "--slug", spec["key"],
           "--name", spec.get("title", spec["key"]),
           "--colour", spec.get("colour_hex", "#BF604E"),
           "--family", spec.get("family", ""),
           "--params", json.dumps(spec.get("params") or {}),
           "--style", str(STYLE), "--samples", str(SAMPLES[engine]),
           "--res", str(res), "--engine", engine]
    if mask:
        cmd.append("--mask")
    return cmd


def parse_manifest(log_text: str) -> dict | None:
    for line in log_text.splitlines():
        if line.startswith("RENDER_JSON:"):
            try:
                return json.loads(line[len("RENDER_JSON:"):])
            except Exception:
                return None
    return None


def run_one(key: str, engine: str, rep: int, bench_root: Path, res: int,
            results: list, dry: bool = False) -> dict | None:
    spec = load_spec(key)
    stl = copy_stl(key, bench_root)
    outdir = bench_root / key / engine / ("r%d" % rep)
    cmd = render_cmd(stl, outdir, spec, engine, res)
    if dry:
        print(" ".join(cmd))
        return None
    outdir.mkdir(parents=True, exist_ok=True)
    log_path = bench_root / key / engine / ("r%d.log" % rep)
    base_mem = gpu_mem_mb()
    probe = Jitter()
    probe.start()
    t0 = time.perf_counter()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        peak_mem = base_mem or 0
        while proc.poll() is None:
            mem = gpu_mem_mb()
            if mem:
                peak_mem = max(peak_mem, mem)
            time.sleep(0.2)
    wall = round(time.perf_counter() - t0, 1)
    probe.stop.set()
    probe.join(timeout=2)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    man = parse_manifest(text)
    shots = (man or {}).get("shots") or []
    per_shot = [s.get("seconds", 0) for s in shots]
    rec = {
        "key": key, "engine": engine, "repeat": rep, "res": res,
        "samples": SAMPLES[engine], "wall_s": wall,
        "blender_s": (man or {}).get("seconds"),
        "first_shot_s": per_shot[0] if per_shot else None,
        "shot_mean_s": round(float(np.mean(per_shot)), 1) if per_shot else None,
        "shots": [{"name": s.get("name"), "seconds": s.get("seconds")}
                  for s in shots],
        "engine_info": (man or {}).get("engine_info"),
        "peak_vram_mb": peak_mem or None,
        "base_vram_mb": base_mem,
        "jitter_max_ms": probe.worst_ms,
        "jitter_p95_ms": probe.p95_ms,
        "exit": proc.returncode,
        "error": "" if man else text[-1200:],
    }
    results.append(rec)
    print("  %-9s %-22s r%d  wall %6.1fs  shot1 %5.1fs  mean %5.1fs  "
          "vram %5sMB  jitter %5.1fms  rc=%d"
          % (engine, key, rep, rec["wall_s"],
             rec["first_shot_s"] or 0, rec["shot_mean_s"] or 0,
             rec["peak_vram_mb"], rec["jitter_max_ms"], rec["exit"]))
    return rec


# ------------------------------------------------------------------ parity
def mask_dir(bench_root: Path, key: str, engine: str) -> Path:
    return bench_root / key / engine / "mask"


def run_mask(key: str, engine: str, bench_root: Path, res: int) -> Path:
    outdir = mask_dir(bench_root, key, engine)
    if all((outdir / ("%s_%s_mask.png" % (key, s))).is_file() for s in SHOTS):
        return outdir
    spec = load_spec(key)
    stl = copy_stl(key, bench_root)
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = render_cmd(stl, outdir, spec, engine, res, mask=True)
    log_path = outdir / "mask.log"
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    return outdir


def _rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0


def _alpha(path: Path) -> np.ndarray:
    im = Image.open(path)
    if im.mode == "RGBA":
        return np.asarray(im.split()[-1]) > 127
    return np.asarray(im.convert("L")) > 127


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return (0, 0, 0, 0)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def image_for(bench_root: Path, key: str, engine: str, shot: str,
              rep: int | None) -> Path:
    folder = bench_root / key / engine / ("r%d" % rep)
    return folder / ("%s_%s.png" % (key, shot))


def latest_rep(results: list, key: str, engine: str) -> int | None:
    ok = [r["repeat"] for r in results
          if r["key"] == key and r["engine"] == engine and r["exit"] == 0
          and not r.get("error")]
    return max(ok) if ok else None


def compare_engine(bench_root: Path, key: str, engine: str, rep: int,
                   baseline_rep: int) -> dict:
    out = {"key": key, "engine": engine, "shots": {}, "pass": True}
    mdir = mask_dir(bench_root, key, engine)
    bdir = mask_dir(bench_root, key, "cycles-cpu")
    for shot in SHOTS:
        base_m = _alpha(bdir / ("%s_%s_mask.png" % (key, shot)))
        cand_m = _alpha(mdir / ("%s_%s_mask.png" % (key, shot)))
        inter = np.logical_and(base_m, cand_m).sum()
        union = np.logical_or(base_m, cand_m).sum()
        iou = float(inter / union) if union else 0.0
        bb0, bb1 = _bbox(base_m), _bbox(cand_m)
        bbox_px = max(abs(a - b) for a, b in zip(bb0, bb1))

        base_im = _rgb(image_for(bench_root, key, "cycles-cpu", shot, baseline_rep))
        cand_path = image_for(bench_root, key, engine, shot, rep)
        cand_im = _rgb(cand_path)
        if base_im.shape != cand_im.shape:
            raise SystemExit("size mismatch %s %s %s" % (key, engine, shot))
        inner = ndimage.binary_erosion(base_m, iterations=3)
        outside = ~ndimage.binary_dilation(
            np.logical_or(base_m, cand_m), iterations=12)
        if inner.sum() < 100:
            inner = base_m
        de_part = deltaE_ciede2000(rgb2lab(base_im)[inner],
                                   rgb2lab(cand_im)[inner])
        de_bg = deltaE_ciede2000(rgb2lab(base_im)[outside],
                                 rgb2lab(cand_im)[outside])
        lum = cand_im @ np.array([0.2126, 0.7152, 0.0722])
        shadow = None
        if shot in ("front", "high", "in_use"):
            x0, y0, x1, y1 = _bbox(base_m)
            h = max(8, y1 - y0)
            band = np.zeros_like(base_m)
            ya, yb = min(base_m.shape[0], y1 + 2), min(base_m.shape[0],
                                                       y1 + max(10, h // 5))
            band[ya:yb, max(0, x0):min(base_m.shape[1], x1)] = True
            band &= ndimage.binary_dilation(base_m, iterations=2) | True
            band &= ~ndimage.binary_dilation(
                np.logical_or(base_m, cand_m), iterations=2)
            if band.sum() > 50:
                bg_lum = float(lum[outside].mean()) if outside.sum() else 1.0
                shadow = round(float(lum[band].mean()) / max(bg_lum, 1e-6), 3)
        rec = {
            "iou": round(iou, 4), "bbox_px": int(bbox_px),
            "de_part_mean": round(float(de_part.mean()), 2),
            "de_part_p95": round(float(np.percentile(de_part, 95)), 2),
            "de_bg_mean": round(float(de_bg.mean()), 2),
            "de_bg_p95": round(float(np.percentile(de_bg, 95)), 2),
            "mean_luma": round(float(lum.mean()), 3),
            "luma_std": round(float(lum.std()), 3),
            "shadow_ratio": shadow,
        }
        rec["pass"] = bool(
            iou >= GATES["iou_min"] and bbox_px <= GATES["bbox_px_max"]
            and rec["de_part_mean"] <= GATES["de_part_mean"]
            and rec["de_part_p95"] <= GATES["de_part_p95"]
            and rec["de_bg_mean"] <= GATES["de_bg_mean"]
            and rec["de_bg_p95"] <= GATES["de_bg_p95"]
            and 0.02 < rec["mean_luma"] < 0.98 and rec["luma_std"] > 0.03)
        out["shots"][shot] = rec
        out["pass"] = out["pass"] and rec["pass"]
    return out


def comparison_sheet(bench_root: Path, key: str, engines: list[str],
                     reps: dict, parity: dict, stamp: str) -> Path:
    cell, label_h, pad = 280, 22, 4
    cols = SHOTS + ["detail_crop"]
    width = pad + len(cols) * (cell + pad)
    height = pad + len(engines) * (cell + label_h + pad) + label_h
    sheet = Image.new("RGB", (width, height), (28, 28, 30))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        small = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font = small = ImageFont.load_default()
    for c, name in enumerate(cols):
        x = pad + c * (cell + pad)
        draw.text((x + 4, 3), name, fill=(230, 230, 230), font=small)
    for r, engine in enumerate(engines):
        y = label_h + pad + r * (cell + label_h + pad)
        rec = next((x for x in parity.get("engines", [])
                    if x["engine"] == engine), {})
        wall = next((x["wall_s"] for x in parity.get("runs", [])
                     if x["engine"] == engine and x["key"] == key), None)
        verdict = ("BASELINE" if engine == "cycles-cpu"
                   else "PASS" if rec.get("pass") else "CHECK")
        draw.text((pad + 4, y), "%-10s wall %ss  %s" %
                  (engine, wall, verdict),
                  fill=((200, 200, 200) if engine == "cycles-cpu"
                        else (120, 230, 140) if rec.get("pass")
                        else (240, 190, 90)),
                  font=font)
        for c, name in enumerate(cols):
            src_name = "detail" if name == "detail_crop" else name
            rep = reps.get(engine)
            path = image_for(bench_root, key, engine, src_name, rep)
            if not path.is_file():
                continue
            im = Image.open(path).convert("RGB")
            if name == "detail_crop":
                w, h = im.size
                side = min(cell, w, h)
                im = im.crop(((w - side) // 2, (h - side) // 2,
                              (w + side) // 2, (h + side) // 2))
            else:
                im = im.resize((cell, cell), Image.LANCZOS)
            sheet.paste(im, (pad + c * (cell + pad), y + label_h))
            if name != "detail_crop":
                s = parity.get("engines")
                shot_rec = next((e["shots"][name] for e in s or []
                                 if e["engine"] == engine), None)
                if shot_rec:
                    draw.text((pad + c * (cell + pad) + 4,
                               y + label_h + cell - 16),
                              "IoU %.3f  dE %.1f" % (shot_rec["iou"],
                                                     shot_rec["de_part_mean"]),
                              fill=(255, 255, 255), font=small)
    out = bench_root / ("%s_compare.jpg" % key)
    sheet.save(out, "JPEG", quality=92)
    return out


# -------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default="",
                    help="comma-separated keys, or 'all' (default when not triage)")
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--res", type=int, default=1000)
    ap.add_argument("--triage", action="store_true",
                    help="one model, one repeat per engine (fast first look)")
    ap.add_argument("--parity", action="store_true",
                    help="mask pass, CIEDE2000/IoU gates, comparison sheets")
    ap.add_argument("--stamp", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--skip-render", action="store_true",
                    help="reuse results.json from --stamp; parity/masks only")
    ap.add_argument("--append", action="store_true",
                    help="add these runs to results.json from --stamp")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    models = ([MODELS[0]] if args.triage
              else MODELS if args.models in ("", "all")
              else [m.strip() for m in args.models.split(",") if m.strip()])
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    for e in engines:
        if e not in ENGINES:
            raise SystemExit("unknown engine %r (choose %s)" % (e, ENGINES))
    bench_root = BENCH / args.stamp
    bench_root.mkdir(parents=True, exist_ok=True)
    spec_path = bench_root / "bench.json"
    spec_path.write_text(json.dumps({
        "models": models, "engines": engines, "repeats": args.repeats,
        "res": args.res, "samples": SAMPLES, "gates": GATES,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=1), encoding="utf-8")
    print("bench %s  models=%s engines=%s repeats=%d res=%d"
          % (args.stamp, models, engines, args.repeats, args.res))

    results = []
    prior = bench_root / "results.json"
    if args.skip_render:
        if not prior.is_file():
            raise SystemExit("no results.json in %s" % bench_root)
        results = json.loads(prior.read_text(encoding="utf-8"))
        print("reusing %d runs from %s" % (len(results), prior))
    elif args.append and prior.is_file():
        results = json.loads(prior.read_text(encoding="utf-8"))
        print("appending to %d runs from %s" % (len(results), prior))
    for rep in range(1, args.repeats + 1):
        if args.skip_render:
            break
        for key in models:
            for engine in engines:
                try:
                    run_one(key, engine, rep, bench_root, args.res, results,
                            dry=args.dry_run)
                except Exception as ex:
                    results.append({"key": key, "engine": engine,
                                    "repeat": rep, "exit": -1,
                                    "error": "%s: %s" % (type(ex).__name__, ex)})
                    print("  %-9s %-22s r%d  EXCEPTION %s" % (engine, key, rep, ex))
                (bench_root / "results.json").write_text(
                    json.dumps(results, indent=1), encoding="utf-8")
    if args.dry_run:
        return 0

    ok = [r for r in results if r.get("exit") == 0 and not r.get("error")]
    print("\nSUMMARY")
    for engine in engines:
        runs = [r for r in ok if r["engine"] == engine]
        if not runs:
            print("  %-9s no successful run" % engine)
            continue
        print("  %-9s wall mean %6.1fs  first shot %5.1fs  shot mean %5.1fs"
              "  peak vram %5sMB  jitter max %5.1fms  n=%d"
              % (engine, np.mean([r["wall_s"] for r in runs]),
                 np.mean([r["first_shot_s"] or 0 for r in runs]),
                 np.mean([r["shot_mean_s"] or 0 for r in runs]),
                 max((r["peak_vram_mb"] or 0) for r in runs),
                 max(r["jitter_max_ms"] for r in runs), len(runs)))

    if args.parity:
        baseline = "cycles-cpu"
        parity = {"runs": ok, "engines": [], "models": {}}
        for key in models:
            base_rep = latest_rep(results, key, baseline)
            if base_rep is None:
                print("  %s: no %s run; cannot gate" % (key, baseline))
                continue
            reps = {}
            for engine in engines:
                rep = latest_rep(results, key, engine)
                if rep is None:
                    continue
                reps[engine] = rep
                try:
                    run_mask(key, engine, bench_root, args.res)
                except Exception as ex:
                    print("  %s %s mask failed: %s" % (key, engine, ex))
                    continue
                if engine == baseline:
                    continue
                try:
                    cmp = compare_engine(bench_root, key, engine, rep, base_rep)
                except Exception as ex:
                    print("  %s %s parity failed: %s" % (key, engine, ex))
                    continue
                parity["engines"].append({"key": key, **cmp})
                print("  %-9s %-22s %s" % (
                    engine, key, "PASS" if cmp["pass"] else "CHECK"))
                for shot, rec in cmp["shots"].items():
                    print("    %-10s IoU %.4f  bbox %dpx  dE part %.2f/%.2f  "
                          "bg %.2f/%.2f  luma %.2f±%.2f  shadow %s"
                          % (shot, rec["iou"], rec["bbox_px"],
                             rec["de_part_mean"], rec["de_part_p95"],
                             rec["de_bg_mean"], rec["de_bg_p95"],
                             rec["mean_luma"], rec["luma_std"],
                             rec["shadow_ratio"]))
                parity["models"].setdefault(key, []).append(engine)
            if len(reps) > 1:
                sheet = comparison_sheet(bench_root, key, [e for e in engines
                                                           if e in reps],
                                         reps, parity, args.stamp)
                print("  sheet: %s" % sheet)
        (bench_root / "parity.json").write_text(json.dumps(parity, indent=1),
                                                encoding="utf-8")

    (bench_root / "results.json").write_text(json.dumps(results, indent=1),
                                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
