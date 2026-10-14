# Gates: STL Generator — RETIRED (2026-10-09, by the user's instruction)

RETIRED. The dashboard must NOT offer "Make a file". `scripts/verify_buttons.py` pins the
retirement (it fails if `#generate-file` or the `generateFile` / `acceptGenerated` handlers come
back), and the user confirmed it on the dashboard: "Remove this too". The engine, its oracle and
this gate file's original claims are kept in git commits `8913258` and `3259463`, recoverable if
the feature is ever wanted again — nothing in the working tree serves or imports it any more:

  · `generator/` (the design brain + forge + verifier) — deleted
  · `scripts/verify_generate.py` (its 11 gates) — deleted with it
  · `scripts/time_nemotron.py` (measured that brain) — deleted with it
  · `POST /generate`, `GenerateRequest`, the dashboard card and its JS/CSS — removed
  · `requirements.txt`: `trimesh` / `manifold3d` were used by nothing else — removed

WHY: the user never ordered the generator on this dashboard, and asked for it to be gone
(twice — once implicitly by the earlier `verify_buttons.py` assertion, once explicitly on the
dashboard). The earlier reading of the tree that treated the card's absence as an unauthorised
deletion was wrong: `verify_buttons.py` — written during the same campaign — already asserted
"the generator section is gone by design". That is recorded honestly in GATES-REUPLOAD R18.

The rest of the app's gates are unaffected: `scripts/verify_buttons.py` (dashboard handlers +
a real fetch), `scripts/check_page_syntax.py` (served inline script), and every gate in
GATES-REUPLOAD.md.
