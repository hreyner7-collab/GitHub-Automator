"""STL generator: Kimi 2.7 turns a description (or image) into a strict JSON
build spec; the local forge compiles that spec into a real binary STL with
trimesh + manifold3d booleans; the verifier proves it printable.

Public surface:
    design_spec(prompt, image_path=None)  -> (spec dict, meta dict)
    build_stl(spec, meta, out_dir)        -> (Path, verify_report dict)
    generate(prompt, image_path=None)     -> response payload dict
"""
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
DL = BASE / "downloads"

KIMI_URL = "https://api.moonshot.ai/v1/chat/completions"
KIMI_MODEL = "kimi-k2.7-code"   # the account's Kimi 2.7 (vision-capable)
LEDGER = Path(__file__).parent / "lessons.json"
KNOWLEDGE = Path(__file__).parent / "stl_knowledge.md"
MAX_REPAIR_RETRIES = 2


# ────────────────────────── lessons ledger ──────────────────────────

def _read_lessons():
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def add_lesson(lesson):
    """Append one lesson (dict with what/why/fix) to the ledger atomically."""
    entries = _read_lessons()
    entries.append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **lesson,
    })
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(LEDGER)
    return len(entries)


def lessons_prompt_block(max_lessons=12):
    """Render recent lessons as anti-repeat knowledge for the prompt."""
    entries = _read_lessons()[-max_lessons:]
    if not entries:
        return ""
    lines = ["## Lessons from previous failed builds (never repeat these):"]
    for e in entries:
        what = str(e.get("what", ""))[:160]
        fix = str(e.get("fix", ""))[:160]
        lines.append(f"- Failure: {what} → Prevent by: {fix}")
    return "\n".join(lines) + "\n"


def _knowledge_text():
    try:
        return KNOWLEDGE.read_text(encoding="utf-8")
    except Exception:
        return ""


# ────────────────────────── Kimi 2.7 call ──────────────────────────

SYSTEM_PROMPT = """You are the design brain of a 3D-print STL generator.
You translate a user's plain-language request into ONE strict JSON build
spec. You never write triangle data; the forge compiles your spec.

STRICT OUTPUT FORMAT — return ONLY a JSON object, no markdown fence, no prose:
{
  "name": "short listing name (<=60 chars)",
  "description": "1-3 sentence printable listing description",
  "design_notes": "mechanism notes: what each part does",
  "parts": [
    {
      "id": "unique_part_id",
      "op": "add" | "cut",
      "shape": {
        "type": "box" | "cylinder" | "thin_shell_sphere",
        ...type fields below...
      },
      "also_cut": [ {"shape": {...}}, ... ]   (optional: cuts applied to
                                               this part's solid only)
    }
  ]
}

Shape vocab (units = millimeters, Z=0 is the print plate):
- box:                  {"type":"box","size":[x,y,z],"pos":[cx,cy,cz]}
                        pos = CENTER of the box. size all >= 1.6.
- cylinder:             {"type":"cylinder","radius":r,"height":h,
                         "pos":[cx,cy,cz],"axis":"z"|"x"|"y"(default z)}
                        pos = CENTER of the cylinder.
- thin_shell_sphere:    {"type":"thin_shell_sphere","radius":r,
                         "pos":[cx,cy,cz],"thickness_mm":t,
                         "opening_deg":deg (optional vertical angular opening
                         at the top, default 0 = closed ball)}
                        A hollow ball shell; overall diameter = 2r.

Exact positioning rules (breaking these is the #1 failure cause):
1. Every part must END with all coordinates >= 0 in z (the whole design
   rests on the plate: size/2 added to pos.z for boxes, height/2 for
   cylinders, radius for shells). The forge auto-translates, but you must
   still design with z>=0 positions.
2. Two parts that should look like ONE object must overlap by >= 1 mm in
   every common direction (welded) — or be exactly coplanar-contacting
   with >= 8 mm square shared area. Geometrically separate islands are
   rejected by the verifier.
3. Wall/thickness anywhere load-bearing: >= 1.6 mm. Bars below 1.2 mm are
   rejected.
4. Biggest dimension target 30-180 mm (hand-held). Buffer: 5-500 mm hard.
5. To make a hole, use "op":"cut" with a cylinder/box. Cuts must
   actually intersect their host part (>= 0.5 mm overlap each side).
6. Port/slot fits: cable slot width = cable diameter + 0.4 (press fit).

Design the BEST real version of what the user asked for: stable base,
sensible proportions, no floaters, no sub-1.6 mm structure, everything
connected. Prefer fewer, well-proportioned parts over many tiny ones.
"""

SPEC_REPAIR_PROMPT = """Your previous spec FAILED verification with:
{error}

Return a CORRECTED full spec (same strict JSON format, no prose).
Apply the verifier's numbers exactly. Do not repeat the failure.
"""


def _call_kimi(messages, max_tokens=3500, temperature=0.35):
    """One Moonshot chat completion. Raises RuntimeError with the API's
    message on any failure (caller decides fallback)."""
    from requests import post
    key = os.getenv("MOONSHOT_API_KEY", "")
    if not key:
        raise RuntimeError("MOONSHOT_API_KEY not set")
    r = post(
        KIMI_URL,
        headers={"Authorization": "Bearer " + key},
        json={
            "model": KIMI_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=90,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Kimi HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _image_block(image_path):
    """Moonshot vision message content for a local image file."""
    data = Path(image_path).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{b64}"},
    }


def _extract_json(text):
    """Pull the first JSON object out of a possible fenced/prose reply."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:text.rfind("}") + 1])


# ────────────────────────── local seed composer ──────────────────────────

def kimi_status():
    """Honest health of the Kimi brain. Returns dict with state.
    state stages: 'ok' (completions work), 'no_key', 'suspended'
    (429 insufficient balance), 'error' (anything else)."""
    if not os.getenv("MOONSHOT_API_KEY"):
        return {"state": "no_key", "detail": "MOONSHOT_API_KEY is not set"}
    try:
        reply = _call_kimi(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=8, temperature=0.0)
        return {"state": "ok", "detail": reply[:40]}
    except RuntimeError as e:
        msg = str(e)
        if "429" in msg or "insufficient balance" in msg or "suspended" in msg:
            return {"state": "suspended", "detail": msg[:300]}
        return {"state": "error", "detail": msg[:300]}


_CONCEPT_WORDS = [
    ("organizer", "organizer"), ("organise", "organizer"),
    ("cable", "cable"), ("hook", "hook"), ("retrect", "retract"),
    ("retract", "retract"), ("rewind", "retract"),
    ("drawer", "drawer"), ("box", "box"), ("cup", "cup"),
    ("holder", "holder"), ("brush", "brush"), ("stand", "stand"),
    ("mount", "mount"), ("clip", "clip"),
]


def _concept_in(prompt, *words):
    p = prompt.lower()
    return any(w in p for w in words)


def local_seed_spec(prompt, image_path=None):
    """Deterministic spec composer inherited from the segmenter library.
    Used ONLY when the Kimi brain is unreachable (no key, suspended or
    down) so the feature still produces a correct, verified STL — never
    a fake or random one. Not an LLM: pure measurement templates."""
    name_seed = "Cable Organizer"
    parts = []
    if _concept_in(prompt, "cup", "brush", "holder", "stand") and not \
            _concept_in(prompt, "cable", "hook"):
        # Cup holder: cylinder with a drain hole and a sturdy base.
        name_seed = "Cup / Brush Holder"
        parts = [
            {"id": "base", "op": "add",
             "shape": {"type": "cylinder", "radius": 45, "height": 6,
                       "pos": [0, 0, 3]}},
            {"id": "cup", "op": "add",
             "shape": {"type": "cylinder", "radius": 40, "height": 80,
                       "pos": [0, 0, 44]}},
            {"id": "hollow", "op": "cut",
             "shape": {"type": "cylinder", "radius": 37, "height": 76,
                       "pos": [0, 0, 44]}},
            {"id": "plate_bottom", "op": "add",
             "shape": {"type": "cylinder", "radius": 37, "height": 2,
                       "pos": [0, 0, 7]}},
        ]
    elif _concept_in(prompt, "hook", "hanger", "towel", "keyring") and not \
            _concept_in(prompt, "cable"):
        # Wall hook: strong L-carriage with screw anchors + eyebrow slot.
        name_seed = "Wall Hook"
        parts = [
            {"id": "plate", "op": "add",
             "shape": {"type": "box", "size": [40, 10, 70],
                       "pos": [0, 0, 35]}},
            {"id": "post", "op": "add",
             "shape": {"type": "box", "size": [20, 30, 14],
                       "pos": [0, 10, 57]}},
            {"id": "nose", "op": "add",
             "shape": {"type": "cylinder", "radius": 9, "height": 22,
                       "pos": [0, 22, 57], "axis": "x"}},
            {"id": "screw_a", "op": "cut",
             "shape": {"type": "cylinder", "radius": 2.1, "height": 12,
                       "pos": [0, 0, 22], "axis": "y"}},
            {"id": "screw_b", "op": "cut",
             "shape": {"type": "cylinder", "radius": 2.1, "height": 12,
                       "pos": [0, 0, 50], "axis": "y"}},
        ]
    else:
        # Cable organizer: base + raised cradle walls + cable slot cut.
        if _concept_in(prompt, "retract", "rewind", "pull"):
            name_seed = "Retracting Cable Organizer"
        else:
            name_seed = "Cable Organizer"
        parts = [
            {"id": "base", "op": "add",
             "shape": {"type": "box", "size": [90, 60, 8],
                       "pos": [0, 0, 4]}},
            {"id": "wall_front", "op": "add",
             "shape": {"type": "box", "size": [90, 10, 30],
                       "pos": [0, 25, 23]}},
            {"id": "wall_back", "op": "add",
             "shape": {"type": "box", "size": [90, 10, 30],
                       "pos": [0, -25, 23]}},
            {"id": "spool_core", "op": "add",
             "shape": {"type": "cylinder", "radius": 14, "height": 40,
                       "pos": [0, 0, 28], "axis": "x"}},
            {"id": "post_l", "op": "add",
             "shape": {"type": "box", "size": [8, 10, 10],
                       "pos": [-14, 0, 11]}},
            {"id": "post_r", "op": "add",
             "shape": {"type": "box", "size": [8, 10, 10],
                       "pos": [14, 0, 11]}},
            {"id": "cable_entry", "op": "cut",
             "shape": {"type": "box", "size": [16, 8, 24],
                       "pos": [38, 0, 12]}},
            {"id": "cable_slot", "op": "cut",
             "shape": {"type": "box", "size": [12, 5.5, 22],
                       "pos": [40, 0, 16]}},
        ]
    return {
        "name": name_seed,
        "description": (
            "Print-ready " + name_seed.lower() +
            " generated from a deterministic library template while the "
            "Kimi design brain is unavailable. Scale and slots sized for "
            "common USB-C and lightning cable bundles."),
        "design_notes": (
            "single-body, Z-min on plate, walls >= 2.0 mm, cable slot "
            "5.5 mm press-fit"),
        "parts": parts,
    }


def design_spec(prompt, image_path=None):
    """Ask Kimi 2.7 for a strict build spec. Returns (spec, meta)."""
    knowledge = _knowledge_text()
    lessons = lessons_prompt_block()
    user_text = (
        f"Design request: {prompt}\n\n"
        "--- STL and forge reference (your training material) ---\n"
        f"{knowledge}\n{lessons}"
    )
    content = [{"type": "text", "text": user_text}]
    if image_path:
        content.append(_image_block(image_path))
        user_text += "\n(An image of the desired look is attached — match its silhouette and proportions.)"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    t0 = time.time()
    reply = _call_kimi(messages)
    spec = _extract_json(reply)
    meta = {
        "model": KIMI_MODEL,
        "elapsed_s": round(time.time() - t0, 1),
        "retries": 0,
    }
    return spec, meta


def repair_spec(spec, error_text):
    """Re-ask Kimi to fix a spec that failed verification (max 2)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": "This was your previous spec:\n" +
                    json.dumps(spec)[:8000]},
        {"role": "user",
         "content": SPEC_REPAIR_PROMPT.format(error=error_text[:1500])},
    ]
    reply = _call_kimi(messages, max_tokens=4000)
    return _extract_json(reply)


# ────────────────────────── orchestration ──────────────────────────

import re as _re


def _slug(name, fallback="model"):
    s = _re.sub(r"[^a-z0-9]+", "_", str(name or fallback).lower()).strip("_")
    return (s or fallback)[:40]


def build_stl(spec, meta, out_dir, allow_repair=True, deadline_s=100.0):
    """Compile + verify a spec. Returns (stl_path, verify_report).

    On verify failure: logs a lesson, then (if allowed and within the
    deadline) re-asks Kimi for a corrected spec, up to MAX_REPAIR_RETRIES.
    Raises VerifyError (with the last report) when the design never passes.
    """
    from generator.forge import forge, SpecError
    from generator.verify import verify, VerifyError

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = "generated_" + _slug(spec.get("name") if isinstance(spec, dict)
                                else None)
    stl_path = out_dir / f"{base}_{stamp}.stl"

    deadline = time.monotonic() + deadline_s
    attempts = 0
    while True:
        attempts += 1
        try:
            forge(spec, stl_path)
            report = verify(stl_path)
            if meta is not None:
                meta["attempts"] = attempts
            return stl_path, report
        except (SpecError, VerifyError) as e:
            err = getattr(e, "report", None)
            err_text = " ".join(str(m) for m in (err or {}).get(
                "errors", [])) or str(e)
            add_lesson({
                "what": f"spec for {spec.get('name', 'part')!r} failed: "
                        + err_text[:200],
                "why": "verifier rejected the compiled mesh",
                "fix": "overlap parts >=1mm, single body, walls >=1.6mm, "
                       "keep parts within 5-500mm and on the plate",
                "spec_name": str(spec.get("name", ""))[:80],
                "attempt": attempts,
                "source": (meta or {}).get("source", "dashboard"),
            })
            if (not allow_repair or attempts > MAX_REPAIR_RETRIES
                    or time.monotonic() > deadline):
                report = err if isinstance(err, dict) else {
                    "ok": False,
                    "errors": [err_text],
                    "warnings": [],
                    "stl_path": str(stl_path) if stl_path.exists() else None,
                    "binary_layout_ok": None,
                    "png_paths": [],
                }
                raise VerifyError(report) from None
            spec = repair_spec(spec, err_text)
            if isinstance(meta, dict):
                meta["retries"] = meta.get("retries", 0) + 1


def generate(prompt, image_path=None, out_dir=None):
    """Full pipeline: prompt -> spec -> STL -> verify -> previews.

    Returns the dashboard-ready payload dict. Raises VerifyError when no
    acceptable design was produced (the caller surfaces Kimi's design +
    verifier errors to the user honestly instead of shipping junk).
    """
    out_dir = Path(out_dir or DL)
    kimi = kimi_status()
    if kimi["state"] == "ok":
        spec, meta = design_spec(prompt, image_path)
        meta["source"] = "dashboard"
        meta["brain"] = kimi
        stl_path, report = build_stl(spec, meta, out_dir)
    else:
        # Kimi unreachable (no key / suspended / down): the deterministic
        # library composer designs it instead — never a broken guess, and
        # the response states the degradation honestly.
        spec = local_seed_spec(prompt, image_path)
        meta = {"model": KIMI_MODEL, "source": "dashboard", "retries": 0,
                "used_fallback": True, "brain": kimi}
        stl_path, report = build_stl(spec, meta, out_dir,
                                     allow_repair=False)
    return {
        "ok": True,
        "prompt": prompt,
        "name": spec.get("name", "Generated model"),
        "description": spec.get("description", ""),
        "design_notes": spec.get("design_notes", ""),
        "stl_path": str(stl_path),
        "stl_url": "/file/" + stl_path.name,
        "preview_paths": report.get("png_paths", []),
        "preview_urls": ["/file/" + Path(p).name
                         for p in report.get("png_paths", [])],
        "verify": {k: report.get(k) for k in
                   ("ok", "watertight", "volume_mm3", "extents_mm",
                    "tris", "binary_layout_ok", "warnings")},
        "meta": meta,
        "brain": kimi,
        "used_fallback": bool(meta.get("used_fallback")),
        "spec": spec,
    }
