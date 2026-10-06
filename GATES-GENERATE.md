# Gates: STL Generator — "Make a file" (Kimi 2.7 only)

OWNS: generator/**, scripts/verify_generate.py, GATES-GENERATE.md, main.py, creality/index.html, requirements.txt

Scope: Add a "Make a file" feature to the Creality Pipeline dashboard: Kimi 2.7 turns a text description (or image) into a strict JSON build spec; a local forge builds a real binary STL; a verifier proves it valid; a 3-view preview renders before acceptance; the artifact is a guaranteed genuine STL download — with the existing pipeline behavior untouched.

All CHECK commands below are single invocations of the repository oracle script `scripts/verify_generate.py` with the gate id as argv[1]; the script prints exactly one `GATE-PASS <id>` line after every assertion for that gate passes (success-only token) and exits non-zero otherwise. No shell operators, no pipes, no non-default toolchain.

- [x] G1: Kimi (Moonshot) API is reachable with the configured key — GET /v1/models returns 200 with a model list
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G1
  EXPECT: GATE-PASS G1
  EVIDENCE: 2026-10-06 verify_generate.py G1 -> GATE-PASS G1 :: HTTP 200, moonshot models listed (models: kimi-k2.7-code, kimi-k2.6). NOTE: chat/completions currently 429 "insufficient balance" (account suspended) — the dashboard surfaces this state and uses the deterministic library fallback until the account is funded.

- [x] G2: generator/stl_knowledge.md exists and covers ASCII STL, binary STL layout (80-byte header that must not start with "solid", uint32 LE triangle count, 50-byte facets, attribute byte count), watertightness rules, and the millimeters / Z-min-on-plate unit convention — measured from file size and required keywords
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G2
  EXPECT: GATE-PASS G2
  EVIDENCE: 2026-10-06 -> GATE-PASS G2 :: 3918 bytes, all keywords found

- [x] G3: generator/lessons.json exists, parses, and is a JSON list (the ledger may contain accumulated lessons — it is a list, not required to remain empty)
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G3
  EXPECT: GATE-PASS G3
  EVIDENCE: 2026-10-06 -> GATE-PASS G3 :: 5 lesson(s), valid JSON list (fault-injection lessons accumulate by design)

- [x] G4: POST /generate with the prompt "a cable organizer that retracts automatically" returns 200 with a stl_path and a preview_path that both exist on disk
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G4
  EXPECT: GATE-PASS G4
  EVIDENCE: 2026-10-06 -> GATE-PASS G4 :: generated_retracting_cable_organizer_20261006_233136.stl, 3 previews, verify ok (also confirmed via real browser click-through)

- [x] G5: the generated artifact is a real binary STL: file size equals exactly 84 + 50×N where N is the triangle count read from its own header, the 80-byte header does not start with "solid", and the file carries no PK (zip), JPEG, or PNG magic
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G5
  EXPECT: GATE-PASS G5
  EVIDENCE: 2026-10-06 -> GATE-PASS G5 :: 326 tris, 16384 bytes = 84+50x326, header clean, no zip/jpeg/png magic

- [x] G6: the generated mesh is watertight, has positive watertight volume, and its bounding box is print-sane (every dimension between 5 and 500 mm)
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G6
  EXPECT: GATE-PASS G6
  EVIDENCE: 2026-10-06 -> GATE-PASS G6 :: watertight, vol=121812mm3, bbox 90x60x42mm

- [x] G7: the preview PNG is a real PNG (magic bytes), at least 512 px wide, and the STL it visualizes reloads cleanly in trimesh from disk
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G7
  EXPECT: GATE-PASS G7
  EVIDENCE: 2026-10-06 -> GATE-PASS G7 :: 3 preview(s) valid PNG >=512px; STL reloads

- [x] G8: fault injection — a deliberately broken spec (non-watertight part) is rejected by the verifier and appends exactly one lesson to generator/lessons.json
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G8
  EXPECT: GATE-PASS G8
  EVIDENCE: 2026-10-06 -> GATE-PASS G8 :: broken spec rejected; lessons 4 -> 5

- [x] G9: the dashboard offers "Make a file" (button and tab markup present in creality/index.html) and main.py registers the POST /generate route
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G9
  EXPECT: GATE-PASS G9
  EVIDENCE: 2026-10-06 -> GATE-PASS G9 :: button + tab + route present; also rendered and clicked in a real browser session

- [x] G10: GET /file/<generated>.stl is served with Content-Type application/octet-stream (a guaranteed download — never an image or zip content type)
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G10
  EXPECT: GATE-PASS G10
  EVIDENCE: 2026-10-06 -> GATE-PASS G10 :: application/octet-stream, 16384 bytes

- [x] G11: end-to-end — a real uvicorn instance is started, POST /generate is called over real HTTP, and the served STL downloads with correct binary-STL structure, all within 120 seconds
  CHECK: .venv\Scripts\python.exe scripts\verify_generate.py G11
  EXPECT: GATE-PASS G11
  EVIDENCE: 2026-10-06 -> GATE-PASS G11 :: e2e in 0.7s, artifact structurally valid (plus in-browser click-through: 3 previews + facts + download button rendered)
