# Gates: generation — the dashboard card stays retired; originals are made offline

## 2026-10-24 — the capability is authorised again, the dashboard card is not

The user approved `C:\Users\henry\.qoder\plans\polished-ledge-char.md`: instead of
copying Creality models, the pipeline now designs NEW household models and renders
its own photos of that exact geometry. That is generation, so the blanket ban in
this file's first version no longer applies to the offline pipeline.

What is authorised, and where it lives:

  · `origin/spec.py` — seeded parameter families (8 household types)
  · `origin/build.py` — trimesh + manifold3d geometry, no fetched meshes as input
  · `origin/printcheck.py` — watertight, single-shell, bed contact, dimensions
  · `blender/render.py` + `origin/shots.py` — Blender photos of the exported STL
  · `origin/publish.py` — original-work route (`source_url=""`), CC BY, novelty guard
  · `scripts/make_originals.py` — the only entry point; dry run by default

Still forbidden:

  · No fetched or downloaded model is ever passed to the generator, and no
    designer's photo is reused, recoloured, or mutated. Geometry is new; photos
    show the geometry we actually upload.
  · The dashboard must NOT grow a "Make a file" card. `scripts/verify_buttons.py`
    pins the absence of `#generate-file` / `generateFile` / `acceptGenerated`.
    The originals CLI is offline; its results reach the dashboard only as
    finished, owner-approved listings (V13).
  · No live publish from the generator. One model is published only after the
    owner says yes to that specific model.

## History (kept honestly)

The first version of this gate (2026-10-09) retired a dashboard generator the user
had asked to remove; `generator/`, `scripts/verify_generate.py`,
`scripts/time_nemotron.py`, `POST /generate` and its card were deleted then. This
file now records both decisions: the 2026-10-09 removal of the *dashboard card*,
and the 2026-10-24 approval of a separate, offline *originals* pipeline.
