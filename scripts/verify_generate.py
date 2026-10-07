"""Gate oracle for GATES-GENERATE.md — one subcommand per gate, prints a
single GATE-PASS <id> line only after every assertion for that gate passes."""
import json
import os
import struct
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

DL = BASE / "downloads"
VENV_PY = BASE / ".venv" / "Scripts" / "python.exe"
MODEL_PROMPT = "a cable organizer that retracts automatically"

PASS = "GATE-PASS "

def die(msg):
    print("GATE-FAIL:", msg)
    sys.exit(1)

def ok(gate, extra=""):
    print(PASS + gate + ((" :: " + extra) if extra else ""))

def _stls():
    if not DL.is_dir():
        die("downloads/ missing")
    return sorted(DL.glob("generated_*.stl"),
                  key=lambda p: p.stat().st_mtime, reverse=True)

def _pngs_for(stl_path):
    stem = stl_path.stem
    return sorted(DL.glob(stem + "*.png"))

def _newest_pair():
    stls = _stls()
    if not stls:
        die("no generated_*.stl in downloads/")
    stl = stls[0]
    return stl, _pngs_for(stl)

def _struct_audit(stl_path):
    data = stl_path.read_bytes()
    if len(data) < 84:
        die(f"{stl_path.name} too small ({len(data)}b)")
    if data[:5].lower() == b"solid"[:5]:
        die("binary STL header must NOT start with 'solid'")
    for magic, kind in ((b"PK\x03\x04", "zip"), (b"\xff\xd8\xff", "jpeg"),
                        (b"\x89PNG", "png")):
        if data[:len(magic)] == magic:
            die(f"{stl_path.name} is {kind}, not STL")
    (n,) = struct.unpack_from("<I", data, 80)
    expected = 84 + 50 * n
    if len(data) != expected:
        die(f"size {len(data)} != 84+50x{n} ({expected})")
    return data, n

def _load_mesh(stl_path):
    import trimesh
    # process=True merges the per-facet duplicated vertices that binary STL
    # ALWAYS stores (format property, not a defect) — this is what any
    # slicer/tool sees. A truly-leaky mesh is still non-watertight after it.
    m = trimesh.load_mesh(str(stl_path), process=True)
    if m is None or len(m.faces) == 0:
        die("stl does not reload as a mesh")
    return m

def req_env():
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    if not os.getenv("NVIDIA_API_KEY"):
        die("NVIDIA_API_KEY not set in .env")

def _post_generate():
    import requests
    from main import app  # noqa: F401  (import validates the app builds)
    if not VENV_PY.exists():
        die("venv python missing")
    r = requests.post("http://127.0.0.1:8000/generate",
                      json={"prompt": MODEL_PROMPT}, timeout=300)
    if r.status_code != 200:
        die(f"/generate HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    for k in ("stl_path", "preview_paths", "verify"):
        if k not in d:
            die(f"/generate response missing {k}: {str(d)[:200]}")
    stl = Path(d["stl_path"])
    if not stl.is_file():
        die(f"stl_path does not exist: {stl}")
    return d, stl

# ────────────────────────── gates ──────────────────────────

def g1_kimi_reachable():
    """Primary design brain must answer a real completion (not just a
    models-list ping). Uses the app's own probe so what is gated is what
    the dashboard actually calls. NIM 503 flakiness is retried inside."""
    req_env()
    from generator import generate as gen
    s = gen.kimi_status()
    if s["state"] != "ok":
        die(f"primary brain probe failed: {s.get('detail', '')[:200]}")
    ok("G1", f"brain {s.get('model', '?')} answered: {s.get('detail', '')[:40]!r}")

def g2_knowledge():
    p = BASE / "generator" / "stl_knowledge.md"
    if not p.is_file():
        die("generator/stl_knowledge.md missing")
    txt = p.read_text(encoding="utf-8", errors="replace")
    low = txt.lower()
    size = p.stat().st_size
    for kw in ["binary stl", "ascii stl", "80", "watertight", "millimeter",
               "z-min", "attribute byte count"]:
        if kw not in low:
            die(f"stl_knowledge.md missing keyword: {kw!r}")
    if size < 1200:
        die(f"stl_knowledge.md only {size} bytes (<1200)")
    ok("G2", f"{size} bytes, all keywords found")

def g3_lessons():
    p = BASE / "generator" / "lessons.json"
    if not p.is_file():
        die("generator/lessons.json missing")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"lessons.json does not parse: {e}")
    if not isinstance(data, list):
        die("lessons.json is not a JSON list")
    ok("G3", f"{len(data)} lesson(s), valid JSON list")

def g4_generate():
    d, stl = _post_generate()
    pv = d["preview_paths"]
    if not isinstance(pv, list) or not pv:
        die("no preview_paths")
    missing = [p for p in pv if not Path(p).is_file()]
    if missing:
        die(f"previews missing: {missing}")
    if not d.get("verify", {}).get("ok"):
        die(f"/generate returned verify.ok=false: {d.get('verify')}")
    ok("G4", f"{stl.name}, {len(pv)} previews, verify ok")

def g5_binary_stl():
    stl, pngs = _newest_pair()
    data, n = _struct_audit(stl)
    ok("G5", f"{stl.name}: {n} tris, {len(data)} bytes = 84+50x{n}, header clean")

def g6_watertight():
    stl, _ = _newest_pair()
    m = _load_mesh(stl)
    if not m.is_watertight:
        die("mesh is not watertight")
    vol = m.volume  # relies on watertight+oriented mesh
    if not vol or vol <= 0:
        die(f"volume not positive: {vol}")
    ext = m.extents
    if not all(5.0 <= float(x) <= 500.0 for x in ext):
        die(f"bounding extents not print-sane: {ext}")
    ok("G6", f"watertight, vol={vol:.0f}mm3, bbox "
             f"{ext[0]:.0f}x{ext[1]:.0f}x{ext[2]:.0f}mm")

def g7_preview():
    stl, pngs = _newest_pair()
    if not pngs:
        die(f"no preview PNGs for {stl.name}")
    good = 0
    for p in pngs:
        b = p.read_bytes()
        if b[:8] != b"\x89PNG\r\n\x1a\n":
            die(f"{p.name} not a PNG")
        import struct as _s
        (w, h) = _s.unpack_from(">II", b, 16)
        if w < 512:
            die(f"{p.name} only {w}px wide (<512)")
        good += 1
    _load_mesh(stl)  # proves the visualized artifact reloads
    ok("G7", f"{good} preview(s) valid PNG >=512px; STL reloads")

def g8_fault_injection():
    lessons_p = BASE / "generator" / "lessons.json"
    before = len(json.loads(lessons_p.read_text(encoding="utf-8"))) \
        if lessons_p.is_file() else 0
    sys.path.insert(0, str(BASE))
    from generator.verify import VerifyError
    from generator.generate import build_stl
    # Supported shapes, broken assembly: two boxes with a 10 mm air gap → the
    # forge builds both components but the verifier must reject the result as
    # not-a-single-printable-solid and log a lesson.
    broken = {
        "name": "broken test part",
        "parts": [
            {"id": "a", "op": "add",
             "shape": {"type": "box", "size": [20, 20, 10], "pos": [0, 0, 5]}},
            {"id": "b", "op": "add",
             "shape": {"type": "box", "size": [20, 20, 10], "pos": [30, 0, 5]}},
        ],
    }
    try:
        path, rep = build_stl(broken, {"source": "gate"}, DL,
                              allow_repair=False)
        if rep.get("ok"):
            die("fault spec unexpectedly verified clean — negative control failed")
    except VerifyError:
        pass  # expected: verifier caught the broken part
    after = len(json.loads(lessons_p.read_text(encoding="utf-8")))
    if after <= before:
        die(f"lessons ledger did not grow ({before} -> {after})")
    ok("G8", f"broken spec rejected; lessons {before} -> {after}")

def g9_dashboard():
    html = (BASE / "creality" / "index.html").read_text(encoding="utf-8")
    if 'id="generate-btn"' not in html:
        die("dashboard missing #generate-btn")
    if "Make a file" not in html:
        die("dashboard missing 'Make a file' label")
    if "generate-file" not in html:
        die("dashboard missing generate-file section")
    main_src = (BASE / "main.py").read_text(encoding="utf-8")
    if "@app.post(\"/generate\")" not in main_src and \
       "@app.post('/generate')" not in main_src:
        die("main.py missing POST /generate route")
    ok("G9", "button + tab + route present")

def g10_download_type():
    import requests
    stls = _stls()
    if not stls:
        die("no generated stl to download")
    stl = stls[0]
    r = requests.get(f"http://127.0.0.1:8000/file/{stl.name}", timeout=15)
    if r.status_code != 200:
        die(f"/file -> {r.status_code}")
    ct = r.headers.get("content-type", "").lower()
    if "application/octet-stream" not in ct:
        die(f"content-type is {ct!r}, not application/octet-stream")
    if not r.content or len(r.content) != stl.stat().st_size:
        die("downloaded bytes do not match file size")
    if r.content[:5].lower() == b"solid"[:5]:
        die("downloaded artifact is ASCII/other, not binary STL")
    if r.content[:2] == b"PK":
        die("downloaded artifact is a zip")
    ok("G10", f"application/octet-stream, {len(r.content)} bytes")

def g11_e2e():
    # real server must be up (started by the caller); quick probe
    import requests
    try:
        requests.get("http://127.0.0.1:8000/health", timeout=5)
    except Exception:
        die("dashboard not running — start it with main.py before running G11")
    t0 = time.time()
    d, stl = _post_generate()   # full brain→forge→verify→preview round trip
    dt = time.time() - t0
    # Budget matches the measured Nemotron design path (76-90 s design,
    # 78-98 s e2e) with headroom for one 503-retry cycle; the old 120 s
    # budget silently assumed GLM-class latency.
    if dt > 240:
        die(f"e2e took {dt:.0f}s (>240)")
    _struct_audit(stl)          # downloaded artifact is a valid binary STL
    ok("G11", f"e2e in {dt:.1f}s, artifact structurally valid")


if __name__ == "__main__":
    gates = {
        "G1": g1_kimi_reachable,
        "G2": g2_knowledge,
        "G3": g3_lessons,
        "G4": g4_generate,
        "G5": g5_binary_stl,
        "G6": g6_watertight,
        "G7": g7_preview,
        "G8": g8_fault_injection,
        "G9": g9_dashboard,
        "G10": g10_download_type,
        "G11": g11_e2e,
    }
    gid = sys.argv[1].upper() if len(sys.argv) > 1 else ""
    if gid not in gates:
        print("usage: verify_generate.py G1..G11")
        sys.exit(2)
    try:
        gates[gid]()
    except SystemExit:
        raise
    except Exception as e:  # unexpected crash = gate fails honestly
        print("GATE-FAIL: unexpected", type(e).__name__, str(e)[:300])
        sys.exit(1)
