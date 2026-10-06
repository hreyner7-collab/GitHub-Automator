# STL — What the designer must know before designing anything

> Curated from the Stereolithography Interface Specification (3D Systems, 1989),
> the Wikipedia STL reference, and real-world parser behavior. This file is
> injected into every design call — it is the "training" that makes the model
> reliable instead of hand-wavy. Everything here is measurement-level truth.

## What an STL file is
An STL describes only the **surface** of a solid: a list of directed triangles.
It has no units field, no colors, no hierarchy. Everything is millimeters by
convention. Your JSON spec is compiled to exactly this triangle list — you never
write triangles, you write measured shapes, and the forge compiles them.

## Binary STL structure (the format we emit)
```
UINT8[80]  header          — arbitrary bytes, but MUST NOT start with "solid"
                             (parsers would misread the file as ASCII STL)
UINT32 LE  triangle count  — little-endian, 4 bytes
per triangle, 50 bytes:    — repeated count times
  REAL32[3] facet normal   — unit length, pointing OUTWARD (right-hand rule
                             with CCW vertices seen from outside)
  REAL32[3] vertex 1
  REAL32[3] vertex 2
  REAL32[3] vertex 3       — vertices in CCW order as seen from outside;
                             floats are IEEE 754, little-endian
  UINT16 LE attribute byte count — MUST be 0 (most software ignores nonzero)
```
File length must be exactly `84 + 50 × triangle_count` bytes — no trailing
padding, no EOF marker. Our forge computes normals and orientation itself, so
you never worry about them.

## ASCII STL (for reference; we do NOT emit it)
Begins `solid <name>`, then per facet:
`facet normal n n n / outer loop / vertex… ×3 / endloop / endfacet`,
ends `endsolid <name>`. Floats in e-notation. Verbose and slow; binary is the
printing standard.

## Watertight requirements (every part must satisfy all)
- Every edge shared by **exactly two** facets (edge-manifold).
- Shared vertices must match **exactly** — a 1e-6 float difference creates a
  hole slicers must repair or reject.
- One closed shell per part; no stray internal faces, no zero-thickness walls,
  no zero-area triangles.
- Outward normals everywhere; inverted faces flip the volume sign and silently
  break slicers.

## Unit convention (millimeters, Z-min on the plate)
No unit metadata exists in STL. Millimeters always; the finished object must
sit with its lowest points (Z-min) exactly on the print plate — Z-min ≥ 0,
supported by flat downside contact. Target sizes: hand-held parts 30–180 mm
in the largest dimension.

## Design quality bars (measurement level)
- **Minimum wall/thickness anywhere load-bearing or visible: 1.6 mm or more.**
  Bars below ~1.2 mm are rejected outright.
- Minimum feature width 1.2 mm; minimum hole/carved-space width so a
  0.4 mm extrusion can fill it.
- Flat downward faces big enough to grip the plate; avoid tall thin columns
  without a foot; avoid bridges over 12 mm without a designed support.
- Clearance between moving/contacting parts: 0.3–0.5 mm for a firm press fit
  of a cable or plug; 0.8 mm for a loose sliding fit.
- Fillets/chamfers beat sharp corners in every measured stress spot.
- Weight the base: nothing tips over; parts that hang on walls get a flat
  mounting face with 3.5–4.5 mm screw holes (M3/M4 heads) meant for anchors.

## What the forge understands (the only vocabulary you have)
Primitives: `box`, `cylinder`, `sphere_shelled`, `thin_shell_sphere`,
`text_emboss` (future), plus boolean ops `add`/`cut` per part, and spec-level
`assembly constraints` (keep parts touching or cut from one solid). If a design
idea needs a mechanism the forge cannot express, choose the nearest honest
printable form — a wall cradle, a spring slot, a screw-anchor pocket — and say
so in `design_notes` instead of inventing shapes the forge will reject.