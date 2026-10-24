"""Build, check, photograph and soak-test original household models.

Nothing here touches a browser, an account or the Creality site. `build` and
`preview` are deliberately offline; publishing is a separate, owner-approved
step. Every render is a Blender photo of the exact STL written beside it.

Run:
  .venv/Scripts/python.exe scripts/make_originals.py list
  .venv/Scripts/python.exe scripts/make_originals.py build cable_clip-01
  .venv/Scripts/python.exe scripts/make_originals.py preview cable_clip-01
  .venv/Scripts/python.exe scripts/make_originals.py soak --count 100
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from origin import build, export, printcheck, shots, spec  # noqa: E402
from origin import publish as publishmod  # noqa: E402

DEFAULT_OUT = ROOT / "artifacts" / "originals"
KEY_RE = re.compile(r"^([a-z_]+)-(\d{1,3})(?:-[0-9a-f]{8})?$")


def parse_key(key: str) -> tuple[str, int]:
    m = KEY_RE.match(key.strip())
    if not m or m.group(1) not in spec.FAMILIES:
        raise SystemExit("unknown model key %r (try `list`)" % key)
    return m.group(1), int(m.group(2))


def build_checked(family: str, variant: int, tries: int = 12):
    """First variant at or after `variant` that builds and passes print checks."""
    last = None
    for attempt in range(tries):
        s = spec.make_spec(family, variant + attempt)
        try:
            m = build.build(s)
            c = printcheck.check(m, s)
        except build.BuildError as e:
            last = "%s: %s" % (s.key, e)
            continue
        if c.ok:
            return s, m, c, attempt
        last = "%s: %s" % (s.key, "; ".join(c.issues))
    raise SystemExit("no printable %s variant in %d tries (%s)"
                     % (family, tries, last))


def cmd_list(args):
    for s in spec.catalogue(args.per_family):
        print("%-34s %s" % (s.key, s.title))


def cmd_build(args):
    fam, var = parse_key(args.key)
    s, m, c, reseeds = build_checked(fam, var)
    print("%s  %s" % (s.key, s.title))
    print("  params   %s" % json.dumps(s.params, sort_keys=True))
    print("  check    %s" % ("pass" if c.ok else "FAIL"))
    for k, v in c.metrics.items():
        print("  %-22s %s" % (k, v))
    for w in c.warnings:
        print("  warning  %s" % w)
    if reseeds:
        print("  reseeds  %d" % reseeds)
    if args.out:
        out = Path(args.out) / s.key
        files = export.export_model(m, out / "files", s.key, s.title)
        print("  exported %s" % files["stl"])
        print("  exported %s" % files["threemf"])


def cmd_preview(args):
    out_root = Path(args.out).resolve()
    made = []
    for key in args.keys:
        fam, var = parse_key(key)
        t0 = time.time()
        s, m, c, reseeds = build_checked(fam, var)
        if not c.ok:
            raise SystemExit("%s failed print checks" % s.key)
        dims = [round(float(v), 1) for v in (m.bounds[1] - m.bounds[0])]
        out = out_root / s.key
        report = shots.make_photos(m, s, c, dims, out, samples=args.samples,
                                   res=args.res)
        made.append({"key": s.key, "title": s.title, "reseeds": reseeds,
                     "contact": report["contact"], "seconds": round(time.time() - t0, 1),
                     "check": c.as_dict(), "photos": report["photos_4x3"]})
        print("%s  %s" % (s.key, report["contact"]))
    (out_root / "previews.json").write_text(json.dumps(made, indent=1),
                                            encoding="utf-8")
    print("\n%d preview(s) in %s" % (len(made), out_root))


def cmd_soak(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    counts = [args.count // len(spec.FAMILIES) +
              (1 if i < args.count % len(spec.FAMILIES) else 0)
              for i in range(len(spec.FAMILIES))]
    passed, failures, timings, reseeds = [], [], [], 0
    t_all = time.time()
    with tempfile.TemporaryDirectory(prefix="origin-soak-") as tmp:
        for family, n in zip(spec.FAMILIES, counts):
            got = 0
            variant = 1
            while got < n and variant < 400:
                t0 = time.time()
                try:
                    s, m, c, tries = build_checked(family, variant, tries=6)
                except SystemExit as e:
                    failures.append({"family": family, "variant": variant,
                                     "error": str(e)})
                    variant += 6
                    continue
                reseeds += tries
                files = export.export_model(m, Path(tmp), s.key, s.title)
                ok = files["three_mf_ok"] and Path(files["stl"]).is_file()
                timings.append(round(time.time() - t0, 2))
                if not ok:
                    failures.append({"family": family, "key": s.key,
                                     "error": "3MF failed: %s" % files["three_mf_detail"]})
                else:
                    passed.append({"family": family, "key": s.key,
                                   "title": s.title, "variant": s.variant,
                                   "reseeds": tries,
                                   "dims_mm": c.metrics["dims_mm"],
                                   "mass_g": c.metrics["est_mass_pla_g"],
                                   "warnings": c.warnings})
                variant = s.variant + 1
                got += 1

    render = None
    if args.render_sample and passed:
        picks = [passed[0], passed[len(passed) // 2], passed[-1]][:args.render_sample]
        seconds = []
        with tempfile.TemporaryDirectory(prefix="origin-shots-") as tmp:
            for rec in picks:
                fam, _ = parse_key(rec["key"])
                s = spec.make_spec(fam, rec["variant"])
                m = build.build(s)
                c = printcheck.check(m, s)
                if s.key != rec["key"] or not c.ok:
                    raise SystemExit("soak sample no longer rebuilds: %s" % rec["key"])
                dims = [round(float(v), 1) for v in (m.bounds[1] - m.bounds[0])]
                report = shots.make_photos(m, s, c, dims, Path(tmp) / s.key,
                                           samples=args.samples, res=args.res)
                seconds.append(report["render"]["wall_seconds"])
        render = {"models": len(seconds),
                  "seconds_per_model": seconds,
                  "mean_seconds": round(st.mean(seconds), 1),
                  "extrapolated_100": round(st.mean(seconds) * 100 / 60.0, 1)}

    summary = {
        "requested": args.count,
        "built": len(passed),
        "pass_rate": round(len(passed) / args.count, 4),
        "reseeds": reseeds,
        "failures": failures,
        "families": dict(zip(spec.FAMILIES, counts)),
        "build_export_seconds": {
            "mean": round(st.mean(timings), 2) if timings else None,
            "total": round(sum(timings), 1),
        },
        "render_sample": render,
        "wall_seconds": round(time.time() - t_all, 1),
        "models": passed,
    }
    path = out / "soak.json"
    path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("%d/%d built, pass rate %.1f%%, %d reseeds, %.0fs total"
          % (len(passed), args.count, 100 * summary["pass_rate"],
             reseeds, summary["wall_seconds"]))
    if render:
        print("render sample: %s s/model (extrapolated 100 models: %.0f min)"
              % (render["mean_seconds"], render["extrapolated_100"]))
    print("wrote %s" % path)
    if failures:
        raise SystemExit("%d soak failure(s); see soak.json" % len(failures))


def cmd_approve(args):
    fam, var = parse_key(args.key)
    s = spec.make_spec(fam, var)
    out = Path(args.out) / s.key
    ap = publishmod.write_approval(out, note=args.note)
    print("approved %s on %s (%s)" % (ap["key"], ap["approved_on"],
                                      ap["note"] or "no note"))


def cmd_publish(args):
    fam, var = parse_key(args.key)
    s = spec.make_spec(fam, var)
    out = Path(args.out) / s.key
    try:
        plan = publishmod.publish(out, live=args.live)
    except (publishmod.PublishRefused, publishmod.PublishError) as e:
        raise SystemExit("%s: %s" % (type(e).__name__, e))
    nov = plan["novelty"]
    print("%s  %s" % (plan["key"], plan["title"]))
    print("  name check  %d listings, %d conflicts, %d near"
          % (nov["checked"], len(nov["conflicts"]), len(nov["near"])))
    for c in nov["near"][:5]:
        print("    near      %.2f  %s" % (c["ratio"], c["name"]))
    print("  approval    %s" % ("signed" if plan["approval"] else "MISSING"))
    print("  source      none (original work), licence %s" % plan["licence"])
    if plan["published"]:
        print("  PUBLISHED   %s" % plan["url"])
    else:
        print("  dry run     nothing was uploaded; pass --live after approval")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show catalogue keys and titles")
    p.add_argument("--per-family", type=int, default=2)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("build", help="build, check and optionally export one model")
    p.add_argument("key")
    p.add_argument("--out", default="", help="export folder (default: check only)")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("preview", help="build + Blender photos + contact sheet")
    p.add_argument("keys", nargs="+")
    p.add_argument("--samples", type=int, default=28)
    p.add_argument("--res", type=int, default=1000)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("soak", help="offline 100-model pass-rate test")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--render-sample", type=int, default=3,
                   help="models to render fully for a time measurement")
    p.add_argument("--samples", type=int, default=28)
    p.add_argument("--res", type=int, default=1000)
    p.add_argument("--out", default=str(DEFAULT_OUT / "soak"))
    p.set_defaults(fn=cmd_soak)

    p = sub.add_parser("approve", help="record the owner's yes for one preview")
    p.add_argument("key")
    p.add_argument("--note", default="")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("publish", help="rehearse (default) or publish one model")
    p.add_argument("key")
    p.add_argument("--live", action="store_true",
                   help="really publish; requires a signed approval")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.set_defaults(fn=cmd_publish)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
