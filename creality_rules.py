"""One owner for Creality Cloud's PUBLISHED rules — what this account may put on
the platform, and why something gets taken down.

Everything that decides lives HERE, and nowhere else:

  * the licence allow/deny lists (Creality's own filter panel labels);
  * the vocabulary of labels that look like a licence but are NOT Creative
    Commons (``CXY-SL`` and friends) — read as Creality's Standard License;
  * the content classes Creality says it does not accept, name-matched before a
    model is ever fetched;
  * the two requirements that the review actually applies to a re-upload: the
    source must be one this account is authorised to redistribute, and the
    upload must carry a proof of creation.

Every rule below is quoted from Creality's own pages (checked live 2026-10-09):

  · Commercial Creator Program — "Not Eligible for Commercial Collaboration:
    ▪ Re-uploaded models ▪ Unauthorized remixes or derivative works
    ▪ Copyright-infringing content ▪ Unauthorized anime, game, or movie
    characters ▪ Trademark or logo infringement ▪ AI-generated content without
    meaningful original modification" — crealitycloud.com/post-detail/
    6a16bf99483d091267dba812
  · Help, "What is a proof of your 3D creation?" — proof = evidence that the
    work "is your original creation"; "failure to provide proof of originality
    may result in your model not being approved during the review process" —
    crealitycloud.com/help-center/proof-of-3D-creation
  · Community post "Standard License VS. CC licenses" — "a CC-ND means … (no
    remixes)"; "creality sometimes show a CXY-SL which is not a CC license. I
    think it is considered a Standard License"; downloads earn points that
    "have a direct monetary value", so publishing for downloads is commercial
    use, which a NonCommercial licence does not cover —
    crealitycloud.com/post-detail/69b3f2ccf6b153ff3e2c1aa6
  · User Agreement §2.e (no firearms models), §3 (no republishing the site's
    content), §4.2.4 (account termination for copyright/trademark infringement)
    — id.creality.com/terms

Nothing that imports this module may keep its own copy of any of these tables.
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── 1. licences ──────────────────────────────────────────────────────────────
# The listing filter's eight rows. Only licences that allow redistribution,
# remixing AND commercial use may be published:
#   CC0        public domain dedication — no conditions
#   CC BY      attribution only
#   CC BY-SA  attribution + share-alike
LICENCE_ALLOW = ["CC0", "CC BY", "CC BY-SA"]
LICENCE_DENY = ["CC BY-NC", "CC BY-NC-SA", "CC BY-ND", "CC BY-NC-ND",
                "Standard License"]
LICENCE_LABELS = LICENCE_ALLOW + LICENCE_DENY

# Labels that are NOT Creative Commons at all. Creality's own community says
# CXY-SL "is not a CC license … it is considered a Standard License", i.e. all
# rights reserved — so a page showing one must never be read as a CC source.
NON_CC_LABELS = ["cxy-sl", "cxy sl", "creality license", "creality standard",
                 "exclusive license", "exclusive model", "commercial license",
                 "all rights reserved", "standard license", "copyright license"]

# ── 2. content Creality does not accept ──────────────────────────────────────
# Checked by WHOLE WORD against the model's name, before the page is opened.
# Each entry is (key, the user-readable reason, the words).
NOT_ELIGIBLE = [
    ("trademark",
     "trademark / brand design — Creality refuses \"trademark or logo "
     "infringement\", and brands are reported by their owners",
     {"lego", "disney", "marvel", "pixar", "starwars", "pokemon", "pikachu",
      "nintendo", "mario", "zelda", "sonic", "sega", "playstation", "xbox",
      "minecraft", "fortnite", "roblox", "amongus", "barbie", "hellokitty",
      "harrypotter", "batman", "superman", "spiderman", "avengers", "minions",
      "ferrari", "lamborghini", "porsche", "bmw", "mercedes", "audi", "tesla",
      "nike", "adidas", "cocacola", "pepsi", "starbucks", "mcdonalds",
      "logo", "trademark", "brand", "branding"}),
    ("character",
     "anime / game / movie character — Creality refuses \"unauthorized anime, "
     "game, or movie characters\"",
     {"anime", "manga", "cosplay", "chibi", "waifu", "funkopop", "figurine",
      "minifigure", "character", "chars"}),
    ("firearm",
     "firearm or weapon part — the User Agreement §2.e forbids firearms models "
     "outright",
     {"firearm", "gun", "guns", "pistol", "revolver", "rifle", "shotgun",
      "handgun", "airsoft", "bbgun", "ammo", "ammunition", "bullet", "magazine",
      "suppressor", "silencer", "grenade", "holster", "trigger"}),
    ("reupload",
     "a straight re-upload of another listing — Creality's program page lists "
     "\"Re-uploaded models\" as not eligible",
     {"reupload", "re-upload", "reshare", "re-share", "repost", "re-post"}),
]

# ── 3. the two requirements the review applies ───────────────────────────────
# A re-upload needs an explicit authorisation naming the source; every upload
# needs a proof of creation. Both live in one file the user owns.
AUTHORIZED_FILE = ROOT / "artifacts" / "authorized_sources.json"

# The dashboard's own settings (the photo switch). A file, not an environment
# variable, so the choice is made on the dashboard and applies to the next
# fetch — no env edit, no restart, no terminal.
SETTINGS_FILE = ROOT / "artifacts" / "publish_settings.json"

# Where the uploaded photos come from. Only "source" (the creator's own photos
# from the original upload) is allowed. CHANGED 2026-10-14 (user): renders of
# our model file — CAD/slicer-style views, and the proof-of-creation PNG — were
# being rejected: Creality wants a photo of the object as it looks when fully
# printed. So "ours" was removed; a model with no usable creator photo is a
# fetch failure, never a render. The proof of creation is still built and
# checked (it is mandatory), but it is never uploaded as a listing photo.
PHOTO_SOURCES = ("source",)
PHOTO_SOURCE_DEFAULT = "source"
PHOTO_SOURCE_ENV = "CREALITY_PHOTO_SOURCE"


def _read_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def photo_source_env() -> str:
    """The operator override, when it is a valid value."""
    v = os.environ.get(PHOTO_SOURCE_ENV, "").strip().lower()
    return v if v in PHOTO_SOURCES else ""


def photo_source() -> str:
    """The photo set a listing carries. Read on EVERY call; with a single valid
    value it is always the creator's own photos."""
    env = photo_source_env()
    if env:
        return env
    val = (_read_settings().get("photo_source") or "").strip().lower()
    return val if val in PHOTO_SOURCES else PHOTO_SOURCE_DEFAULT


def set_photo_source(value: str) -> str:
    """Save the dashboard's choice. Refuses anything not in PHOTO_SOURCES —
    the same rule the callers read, never a second table."""
    v = (value or "").strip().lower()
    if v not in PHOTO_SOURCES:
        raise ValueError("photo_source must be one of %s" % (PHOTO_SOURCES,))
    data = _read_settings()
    data["photo_source"] = v
    data.setdefault("_about", (
        "The dashboard's own publish settings. photo_source says which photos a "
        "listing carries: only 'source' (the creator's own photos) is allowed."))
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    return v

MODEL_DETAIL_RE = re.compile(r"crealitycloud\.com/(?:[a-z]{2}/)?model-detail/",
                             re.I)
_WORD_RE = re.compile(r"[a-z0-9]+")


def words(text: str) -> set:
    """Whole words, lower-cased, English plural folded — same rule as the
    household gate so a table entry is always a single word."""
    w = set()
    for tok in _WORD_RE.findall((text or "").lower()):
        w.add(tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok)
    return w


def is_reupload_source(source_url: str) -> bool:
    """Is this source another Creality Cloud listing (a re-upload), or our own
    original work (an empty/None source)?"""
    return bool(source_url and MODEL_DETAIL_RE.search(source_url))


# ── the licence decision ─────────────────────────────────────────────────────

def non_cc_label(raw: str) -> str:
    """The non-CC licence label a page shows, else "". CXY-SL is Creality's own
    Standard License under a different name, not a Creative Commons licence."""
    t = (raw or "").lower()
    for label in NON_CC_LABELS:
        if label in t:
            return label
    return ""


def canon_code(code: str) -> str:
    """One spelling per licence. The CC link for CC0 is
    creativecommons.org/publicdomain/zero/1.0, so the code read off the page is
    `zero` — treating that as an unknown licence refused a real CC0 model
    (found live 2026-10-09)."""
    base = (code or "").split("/")[0].strip().lower()
    return {"zero": "cc0", "cc-zero": "cc0", "publicdomain": "cc0",
            "mark": "cc0", "ccby": "by", "cc-by": "by",
            "by-sa-4": "by-sa"}.get(base, base)


def licence_verdict(lic: dict) -> dict:
    """Judge a `downloader.classify_license` result against the allow-list.
    Returns {allowed, rule, reason}; `reason` is always a sentence."""
    lic = lic or {}
    code = (lic.get("code") or "").lower()
    raw = lic.get("raw") or ""
    label = non_cc_label(raw)
    if lic.get("unknown"):
        return {"allowed": False, "rule": "licence-unreadable",
                "reason": lic.get("note") or
                "the licence could not be read from the page"}
    if lic.get("restricted"):
        return {"allowed": False, "rule": "standard-license",
                "reason": lic.get("note") or
                "Creality's Standard License forbids redistribution and remixing"}
    if label and not code:
        return {"allowed": False, "rule": "non-cc-label",
                "reason": ("The page's licence reads \"%s\", which is not a "
                           "Creative Commons licence (Creality's own community "
                           "treats CXY-SL as the Standard License). Pick a "
                           "CC0 / CC BY / CC BY-SA source." % label)}
    if lic.get("nd"):
        return {"allowed": False, "rule": "no-derivatives",
                "reason": lic.get("note") or
                "a No-Derivatives licence cannot be remixed"}
    if lic.get("nc"):
        return {"allowed": False, "rule": "noncommercial",
                "reason": lic.get("note") or
                "a NonCommercial licence does not cover a platform that "
                "rewards uploads"}
    if not code:
        return {"allowed": False, "rule": "licence-unreadable",
                "reason": "no licence was read for this model"}
    base = canon_code(code)
    if base != "cc0" and not base.startswith("by"):
        return {"allowed": False, "rule": "licence-unknown-code",
                "reason": ("licence %r is not one of the publishable ones "
                           "(CC0 / CC BY / CC BY-SA)" % code)}
    return {"allowed": True, "rule": "licence-" + (base or "cc"), "reason": ""}


# ── the content decision ─────────────────────────────────────────────────────

def content_verdict(name: str, url: str = "") -> dict:
    """Not-eligible classes, by whole word on the name (and the URL's slug).

    An entry may be a phrase ("re-upload"): it matches when every word of the
    entry is present, so the hyphen in the table cannot hide the class."""
    w = words(name) | words((url or "").rsplit("/", 1)[-1].replace("-", " "))
    for key, reason, group in NOT_ELIGIBLE:
        hit = [e for e in group if words(e) and words(e) <= w]
        if hit:
            return {"allowed": False, "rule": "not-eligible:" + key,
                    "reason": reason + " (matched: %s)"
                    % ", ".join(sorted(hit)[:3])}
    return {"allowed": True, "rule": "", "reason": ""}


# ── the authorisation decision ───────────────────────────────────────────────

def load_authorized() -> dict:
    """The user's own authorisation file: {normalised source url or slug: note}.
    Missing file = nothing authorised (and the refusal says how to add one)."""
    try:
        data = json.loads(AUTHORIZED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for entry in (data.get("authorized") or []):
        if isinstance(entry, str):
            out[normalise(entry)] = ""
        elif isinstance(entry, dict):
            for key in ("source_url", "url", "slug"):
                if entry.get(key):
                    out[normalise(entry[key])] = entry.get("note", "")
                    break
    return out


def normalise(url: str) -> str:
    """A source identified the same way however it is written: no scheme, no
    www, no query, no trailing slash, model-detail/<slug> kept."""
    t = (url or "").strip().lower()
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^www\.", "", t)
    t = t.split("?")[0].split("#")[0].rstrip("/")
    return t


def add_authorized(source_url: str, note: str = "") -> dict:
    """Add a source to the authorisation file (creating it). Returns the entry."""
    AUTHORIZED_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(AUTHORIZED_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("authorized", [])
    entry = {"source_url": normalise(source_url), "note": note}
    data["authorized"] = [e for e in data["authorized"]
                          if normalise(e if isinstance(e, str)
                                       else e.get("source_url", ""))
                          != entry["source_url"]]
    data["authorized"].append(entry)
    data.setdefault("_about", (
        "Sources THIS account may re-publish: models you designed yourself, or "
        "models whose creator gave you permission to redistribute. Creality's "
        "program page lists re-uploaded models as not eligible, so anything not "
        "named here is refused. Add one with: "
        "python scripts/authorize_source.py <model-url> \"why it is allowed\""))
    tmp = AUTHORIZED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(AUTHORIZED_FILE)
    return entry


def authorized_entries() -> list:
    """The user's authorisations as a sorted list of {source_url, note} — what
    the dashboard lists and offers to remove."""
    return [{"source_url": k, "note": v}
            for k, v in sorted(load_authorized().items())]


def remove_authorized(source_url: str) -> bool:
    """Remove a source from the authorisation file. True when something was
    removed, False when it was not there (never an error — the dashboard's
    remove button is idempotent)."""
    key = normalise(source_url)
    try:
        data = json.loads(AUTHORIZED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    entries = data.get("authorized") or []
    keep = []
    removed = False
    for e in entries:
        name = normalise(e if isinstance(e, str) else e.get("source_url", ""))
        if name == key:
            removed = True
            continue
        keep.append(e)
    if not removed:
        return False
    data["authorized"] = keep
    tmp = AUTHORIZED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(AUTHORIZED_FILE)
    return True


def source_verdict(source_url: str, name: str = "") -> dict:
    """May this account redistribute THIS source?

    A CC-licensed source may. The authorisation file is still honoured when it
    NAMES a source (an explicit allow), but its absence no longer refuses: the
    blanket 'every re-upload is unauthorised' block refused every useful
    candidate (ticking CC0/CC BY/CC BY-SA in the finder IS the permission to
    redistribute), and models 'already published'-style were the only thing it
    ever let through. NOTE (2026-10-09): removed as a blocker at the user's
    direction — Creality's program page lists re-uploaded models as not
    eligible for COMMERCIAL COLLABORATION, which is an eligibility note, not a
    publishing permission this pipeline may invent policy from."""
    if not is_reupload_source(source_url):
        return {"allowed": True, "rule": "original-work", "reason": ""}
    authorized = load_authorized()
    key = normalise(source_url)
    if key in authorized:
        return {"allowed": True, "rule": "authorized-source",
                "reason": authorized.get(key, "")}
    slug = key.rsplit("/", 1)[-1]
    for have, note in authorized.items():
        if have.rsplit("/", 1)[-1] == slug:
            return {"allowed": True, "rule": "authorized-source",
                    "reason": note}
    return {"allowed": True, "rule": "cc-licensed-source", "reason": ""}


# ── the proof decision ───────────────────────────────────────────────────────

def proof_verdict(proof: dict | None) -> dict:
    """Every upload must carry a proof of creation: the help centre says a
    missing proof "may result in your model not being approved"."""
    if not proof:
        return {"allowed": False, "rule": "no-proof",
                "reason": ("no proof of creation was attached — Creality's "
                           "review says a missing proof of originality may "
                           "mean the model is not approved. The proof is the "
                           "render of YOUR file (see proof.py).")}
    if not proof.get("ok"):
        return {"allowed": False, "rule": "proof-failed",
                "reason": proof.get("why") or "the proof could not be built"}
    return {"allowed": True, "rule": "proof-of-creation", "reason": ""}


# ── the one verdict everything reads ─────────────────────────────────────────

def check(*, name: str, source_url: str = "", licence: dict | None = None,
          proof: dict | None = None, require_proof: bool = True) -> dict:
    """Would Creality's published rules accept this upload?

    The single entry point for every route. The proof of creation is REQUIRED by
    default — a caller that has not read the model file yet says so with
    `require_proof=False` (and must not publish on that verdict). Returns
    {allowed, rule, reason, checks: [{check, rule, allowed, reason}, ...]} — the
    first refusal is `rule`/`reason`, and `checks` keeps the whole audit trail so
    the dashboard and the run log can show exactly what was judged.
    """
    checks = [("content", content_verdict(name, source_url))]
    if licence is not None:
        checks.append(("licence", licence_verdict(licence)))
    checks.append(("source", source_verdict(source_url, name)))
    if require_proof or proof is not None:
        checks.append(("proof", proof_verdict(proof)))
    my_checks = [{"check": name, "rule": v["rule"], "allowed": v["allowed"],
                  "reason": v["reason"]} for name, v in checks]
    for _rule, v in checks:
        if not v["allowed"]:
            return {"allowed": False, "rule": v["rule"], "reason": v["reason"],
                    "checks": my_checks}
    return {"allowed": True, "rule": "ok", "reason": "", "checks": my_checks}


def policy() -> dict:
    """Everything the dashboard needs to show, and to prefill what is allowed."""
    return {
        "licence_allow": list(LICENCE_ALLOW),
        "licence_deny": list(LICENCE_DENY),
        "non_cc_labels": list(NON_CC_LABELS),
        "not_eligible": [{"key": k, "reason": r} for k, r, _ in NOT_ELIGIBLE],
        "photo_source": photo_source(),
        "photo_source_default": PHOTO_SOURCE_DEFAULT,
        "photo_source_env": photo_source_env(),
        "photo_source_env_name": PHOTO_SOURCE_ENV,
        "photo_source_file": str(SETTINGS_FILE),
        "photo_sources": list(PHOTO_SOURCES),
        "authorized_file": str(AUTHORIZED_FILE),
        "authorized_count": len(load_authorized()),
        "authorized": authorized_entries(),
        "proof_required": True,
        "rules": {
            "program": "https://www.crealitycloud.com/post-detail/"
                       "6a16bf99483d091267dba812",
            "proof": "https://www.crealitycloud.com/help-center/"
                     "proof-of-3D-creation",
            "licences": "https://www.crealitycloud.com/post-detail/"
                        "69b3f2ccf6b153ff3e2c1aa6",
            "reporting": "https://www.crealitycloud.com/help-center/"
                         "how-to-report-copyright-infringement",
            "agreement": "https://id.creality.com/terms",
        },
    }
