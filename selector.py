"""One owner for the question the whole pipeline asks before it publishes:
is this model a useful, day-to-day household item?

The user's rule, in their own words: household items only — the kind of thing
somebody prints because they will actually use it at home (a cable organizer, a
phone holder, a cotton-bud holder, water-bottle and storage boxes, clothes clips,
hooks, desk organizers) — and never rubbish (a toilet, a bathroom accessory set,
garage/workshop gear, a novelty or joke model, a decorative trinket). Searching
Creality for the literal phrase "household items" and taking the most-downloaded
result is what put a Toilet and a bathroom set on the account: popularity is not
usefulness, and Creality's own search relevance is not this user's idea of home.

Everything that decides lives HERE, and nowhere else:

  * discovery (discover.find_creality) judges every card as it harvests, so a
    rejected card is never handed to the run at all, never counts towards the
    requested number — the listing keeps looking for an allowed one — and comes
    back with the reason it was passed over, which the run logs;
  * the run (main._auto_worker) re-judges every candidate before queueing it, so
    nothing can reach the uploader even if discovery were bypassed;
  * the dashboard reads the families, the reject rules and the default keywords
    from GET /selector, so the run's own idea of "useful household item" is
    written down once and can be changed in one place.

A human publishing by hand through /upload is NOT filtered: the user picking a
model themselves is the decision, not this module. This gate is what the machine
is allowed to choose on its own.
"""
import re
import unicodedata

# ── word matching ────────────────────────────────────────────────────────────
# Matching is by WHOLE word, not substring: "car" never matches "card holder",
# "tool" never matches "stool". Names arrive in English and Russian (the ledger
# already holds both), so the tables below carry the inflections that actually
# show up in titles — a table word is a word, deliberately, so adding one is a
# one-line edit and never a surprise from a stemmer.

_WORD_RE = re.compile(r"[a-zа-яё0-9]+")


def _fold(w: str) -> str:
    """English plural 's' folded ("clips" -> "clip"); everything else kept."""
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def words(text: str) -> set:
    """The name as a set of whole words, each folded by _fold."""
    # Latin accents are dropped ("móviles" -> "moviles"): the word regex is
    # ASCII-plus-Cyrillic, so an accented letter would otherwise split a word in two.
    # Cyrillic is left alone on purpose (its й must not lose its breve).
    latin = re.sub(r"[\u00c0-\u024f]", lambda m: unicodedata.normalize("NFD", m.group(0))[0],
                   (text or "").lower().replace("ё", "е"))
    return {_fold(w) for w in _WORD_RE.findall(latin)}


# Russian families/rejects are written out in the forms real titles use
# (nominative singular/plural + the genitive/accusative a title usually has).
RU_HOLDER = ["держатель", "держателя", "держатели", "держателей"]
RU_STAND = ["подставка", "подставки", "подставку", "подставок"]
RU_ORGANIZER = ["органайзер", "органайзера", "органайзеры", "органайзеров"]
RU_BOX = ["коробка", "коробки", "коробку", "коробок", "короб"]
RU_CONTAINER = ["контейнер", "контейнера", "контейнеры", "контейнеров"]
RU_STORAGE = ["хранение", "хранения", "хранению"]
RU_POLKA = ["полка", "полки", "полку", "полок"]
RU_KRYUCHOK = ["крючок", "крючки", "крючка", "крючков"]
RU_VESHALKA = ["вешалка", "вешалки", "вешалку", "вешалок"]

# ── what we will NOT publish ─────────────────────────────────────────────────
# Checked FIRST, in this order: one hit and the candidate is out, whatever else
# its name looks like ("toilet paper holder" is a holder, and still rubbish).
REJECTS = [
    ("bathroom",
     "bathroom fixture or bathroom set — not a day-to-day useful household item",
     {"toilet", "bathroom", "washroom", "wc", "bidet", "urinal", "shower",
      "bathtub", "tub", "basin", "soap", "soapdish", "toothbrush", "toothpaste",
      "razor", "shaving",
      "туалет", "туалета", "туалеты", "туалетный", "ванная", "ванной", "ванную",
      "унитаз", "унитаза", "мыльница", "мыльницы", "биде", "душ", "бритва",
      "бритвы", "зубная", "зубной", "зубные"}),
    ("garage",
     # "clamp" is deliberately NOT a reject word: the live listing's own "Usbc
     # Cable Holder Screw Clamp" is a cable tidy and was refused by it. A real
     # workshop clamp falls to no-family anyway, which reads honestly.
     "garage / automotive / workshop gear — not a house item",
     {"garage", "automotive", "car", "truck", "motorcycle", "motorbike", "bike",
      "bicycle", "engine", "wheel", "tire", "tyre", "mower", "lawn", "garden",
      "tractor", "trailer", "workshop", "workbench", "toolbox", "tool", "drill",
      "chainsaw", "welding", "lathe", "vise", "wrench", "spanner", "hose",
      "гараж", "гаража", "гаражи", "гаражный", "авто", "автомобиль", "автомобиля",
      "машина", "машины", "машину", "двигатель", "двигателя", "мотор", "мотора",
      "колесо", "колеса", "колес", "шина", "шины", "велосипед", "велосипеда",
      "мотоцикл", "мотоцикла", "инструмент", "инструмента", "инструменты",
      "верстак", "верстака", "мастерская", "мастерской"}),
    ("printer-part",
     "3D-printer part — not a household item",
     {"filament", "nozzle", "extruder", "hotend", "printer", "gcode",
      "филамент", "филамента", "сопло", "сопла", "экструдер", "экструдера",
      "принтер", "принтера", "принтеры"}),
    ("toy-novelty",
     "toy / novelty or joke model — fun to print, not useful at home",
     {"toy", "figurine", "dice", "novelty", "joke", "gag", "prank", "meme",
      "fidget", "squishy", "plush", "keychain", "miniature", "minifigure",
      "cosplay", "prop", "replica",
      "сувенир", "сувениры", "сувенира", "игрушка", "игрушки", "игрушку",
      "фигурка", "фигурки", "фигурку", "кубик", "кубики", "кубика",
      "брелок", "брелка", "брелки"}),
    ("decor",
     "decorative trinket — a look, not a working household item",
     {"decor", "decoration", "ornamental", "ornament", "vase", "statue",
      "sculpture", "plaque", "sign", "art", "artwork", "lantern", "candle",
      "candleholder", "incense", "jewelry", "earring", "necklace",
      "декор", "декора", "ваза", "вазы", "вазу", "подсвечник", "подсвечника",
      "украшение", "украшения", "статуя", "статуи", "статуэтка", "статуэтки"}),
    # live listing: "Tableware Plate Bowl Cup Spoon Fork Knife" was refused here as
    # a weapon — cutlery is household. A bare knife/blade matches no family anyway,
    # so it leaves the allowed set through the same door as any other off-family
    # name ("not one of the useful household families"), which is the honest reason.
    ("weapon-adult",
     "not an everyday household item",
     {"gun", "pistol", "rifle", "sword", "weapon", "ammo", "ammunition",
      "grenade", "sex", "sexual", "adult", "nsfw", "bdsm"}),
]

# ── what we WILL publish ─────────────────────────────────────────────────────
# Each family is a list of alternative matchers; a matcher is a list of word
# groups and EVERY group must be hit. The first family that matches names the
# find — the label is what the run log and the dashboard show.
FAMILIES = {
    "cable-organizer": {
        "label": "cable / headphone organizer",
        "search": "cable organizer",
        "matchers": [
            [{"cable", "cord", "wire", "charger", "usb", "headphone", "earphone",
              "кабель", "кабеля", "кабелей", "провод", "провода", "проводов",
              "зарядка", "зарядки", "наушник", "наушники", "наушников",
              "cabos", "cabo", "cavi", "cavo"},
             {"organizer", "organiser", "tidy", "holder", "clip", "spool", "winder",
              "keeper", "management", "rack", "stand", "box", "case", "hanger",
              "organizador",
              *RU_ORGANIZER, *RU_HOLDER, "катушка", "катушки", "хомут", "хомуты",
              "стяжка", "стяжки", "стакан", "стакана"}],
            [{"headphone", "earphone", "наушник", "наушники", "наушников"},
             {"stand", "holder", "hanger", "hook", *RU_STAND, *RU_HOLDER}],
            # a zip tie or its holder
            [{"zip"}, {"tie", "ties"}],
            # a cable kept tidy by a guide, chain, protector, reel or tie
            [{"cable", "cord", "cabos", "cabo", "cavi", "cavo", "usb", "charger"},
             {"chain", "protector", "protection", "guide", "reel", "rollup", "roll",
              "tie", "ties", "port", "channel", "duct", "sleeve", "clamp", "wrap"}],
        ],
    },
    "phone-holder": {
        "label": "phone holder / stand",
        "search": "phone holder",
        "matchers": [
            [{"phone", "iphone", "smartphone", "tablet", "mobile", "cell",
              "телефон", "телефона", "телефоны", "смартфон", "смартфона",
              "планшет", "планшета",
              "movil", "moviles", "celular"},
             {"holder", "stand", "mount", "dock", "docking", "cradle", "rack",
              "rest", "support", "station", "organizador", *RU_HOLDER, *RU_STAND}],
        ],
    },
    "swab-holder": {
        "label": "cotton-bud holder",
        "search": "cotton bud holder",
        "matchers": [
            [{"cotton", "swab", "qtip", "ватная", "ватные", "ватных", "палочка",
              "палочки", "палочек", "палочку"},
             {"holder", "dispenser", "box", "jar", "stand", "organizer", "organiser",
              "case", "canister", *RU_HOLDER, "диспенсер", *RU_BOX, *RU_STAND}],
        ],
    },
    "bottle-storage": {
        "label": "bottle / cup storage",
        "search": "water bottle holder",
        "matchers": [
            [{"bottle", "cup", "mug", "tumbler", "thermos", "can",
              "бутылка", "бутылки", "бутылку", "бутылок", "бутылочка", "бутылочки",
              "кружка", "кружки", "кружку", "стакан", "стакана", "стаканы",
              "чашка", "чашки", "банка", "банки", "банку"},
             {"storage", "holder", "rack", "organizer", "organiser", "shelf", "stand",
              "tray", "dispenser", "stacker", "caddy",
              *RU_STORAGE, *RU_HOLDER, *RU_POLKA, *RU_STAND, *RU_ORGANIZER}],
        ],
    },
    "clothes-clip": {
        "label": "clothes clip / peg",
        "search": "clothes pegs",
        "matchers": [
            [{"clothespin", "peg", "прищепка", "прищепки", "прищепок",
              "прищепку"}],
            [{"clothes", "clothe", "laundry", "белье", "белья", "бельевой",
              "бельевые", "бельевых"},
             {"clip", "peg", "pin", "hanger", "прищепка", "прищепки", "прищепку",
              "прищепок"}],
            [{"bag", "bread", "chip", "food", "packet", "snack", "kitchen"},
             {"clip", "sealer", "sealing", "зажим", "зажима", "зажимы"}],
        ],
    },
    "hook": {
        "label": "hook / key holder",
        "search": "wall hooks",
        "matchers": [
            [{"hook", *RU_KRYUCHOK, *RU_VESHALKA, "ключница", "ключницы",
              "ключницу"}],
            [{"key", "ключ", "ключи", "ключей", "ключница", "ключницы"},
             {"holder", "rack", "hook", "organizer", "organiser", "hanger", "board",
              *RU_HOLDER, *RU_POLKA, "доска", "доски", *RU_VESHALKA, "крючок",
              "крючки"}],
            [{"coat", "towel", "wall", "hat", "сумка", "сумки", "сумку",
              "полотенце", "полотенца", "полотенец"},
             {"hook", "hanger", "rack", *RU_VESHALKA, *RU_KRYUCHOK}],
        ],
    },
    "desk-organizer": {
        "label": "desk organizer / pen stand",
        "search": "desk organizer",
        "matchers": [
            [{"desk", "office", "stationery", "pencil", "pen", "marker", "book",
              "настольный", "настольная", "настольные", "карандаш", "карандаша",
              "карандаши", "карандашей", "ручка", "ручки", "ручку", "ручек"},
             {"organizer", "organiser", "holder", "tidy", "stand", "cup", "caddy",
              "box", "tray", *RU_ORGANIZER, *RU_HOLDER, *RU_STAND, "стакан",
              "стакана"}],
            [{"pen", "pencil", "ручка", "ручки", "ручек", "карандаш",
              "карандаша", "карандашей"},
             {"holder", "stand", "cup", "pot", *RU_STAND}],
        ],
    },
    # Broadest family, so it is checked LAST: "desk organizer" must name itself as
    # a desk organizer, not disappear into plain "organizer".
    "storage-box": {
        "label": "storage box / basket / tray",
        "search": "storage box organizer",
        "matchers": [
            [{"storage", "organizer", "organiser", "box", "bin", "basket", "container",
              "crate", "tray", "drawer", "caddy", "shelf", "rack",
              *RU_STORAGE, *RU_ORGANIZER, *RU_BOX, *RU_CONTAINER, "лоток", "лотка",
              "лотки", "ящик", "ящика", "ящики", *RU_POLKA, *RU_STAND}],
        ],
    },
}

# The run's default search phrases are the user's own object families — NOT the
# phrase "household items", which is what returned a toilet.
DEFAULT_KEYWORDS = [f["search"] for f in FAMILIES.values()]

# The tables are written in natural words; matching folds both sides with _fold,
# so a table's plural still matches a name (and vice versa).
_REJECTS_FOLDED = [(k, r, {_fold(w) for w in g}) for k, r, g in REJECTS]
_FAMILIES_FOLDED = {k: [[{_fold(w) for w in grp} for grp in m]
                        for m in f["matchers"]] for k, f in FAMILIES.items()}

NO_FAMILY_REASON = ("not one of the useful household families we publish (%s)"
                    % ", ".join(sorted({f["label"] for f in FAMILIES.values()})))


def _family_of(w: set):
    for key, matchers in _FAMILIES_FOLDED.items():
        label = FAMILIES[key]["label"]
        for matcher in matchers:
            if all(w & group for group in matcher):
                return key, label
    return "", ""


def reject_reason(name: str) -> tuple:
    """(key, reason) for the first rubbish rule the name breaks, else ("", "")."""
    w = words(name)
    for key, reason, group in _REJECTS_FOLDED:
        if w & group:
            return key, reason
    return "", ""


def classify(name: str, url: str = "") -> dict:
    """Judge one model on its own (no run context). Returns
    {allowed, family, label, reason, source} — `reason` is always a sentence the
    user can read, whether the model is kept or turned down."""
    name = (name or "").strip()
    if url and not name:
        name = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    key, why = reject_reason(name)
    if key:
        return {"allowed": False, "family": "", "label": "", "reason": why,
                "source": "reject:" + key}
    fam, label = _family_of(words(name))
    if fam:
        return {"allowed": True, "family": fam, "label": label, "reason": "",
                "source": "family:" + fam}
    return {"allowed": False, "family": "", "label": "", "reason": NO_FAMILY_REASON,
            "source": "no-family"}


def allow(name: str, url: str = "", used_families: set | None = None) -> dict:
    """The verdict the pipeline acts on. Pass the run's own `used_families` set to
    apply the one-of-each rule: a second item from a family that is already going
    out in THIS run is turned down (the set is updated when one is accepted)."""
    v = classify(name, url)
    if v["allowed"] and used_families is not None:
        if v["family"] in used_families:
            return {"allowed": False, "family": v["family"], "label": v["label"],
                    "source": "duplicate-family",
                    "reason": ("a %s is already going out in this run — one of each "
                               "is enough" % v["label"])}
        used_families.add(v["family"])
    return v


def filter_candidates(items: list) -> dict:
    """Judge a whole candidate list at once (what the run does): returns
    {kept: [...], rejected: [{...item, reason, source}]}."""
    used: set = set()
    kept, rejected = [], []
    for it in items or []:
        name, url = it.get("name") or "", it.get("url") or ""
        if it.get("spare"):
            # a repeat family kept to fill the requested count: only the
            # household gate applies, the one-of-each preference does not
            v = classify(name, url)
        else:
            v = allow(name, url, used)
        if v["allowed"]:
            kept.append({**it, "family": v["family"], "label": v["label"]})
        else:
            rejected.append({**it, "reason": v["reason"], "source": v["source"]})
    return {"kept": kept, "rejected": rejected}


def policy() -> dict:
    """Everything the dashboard needs to show and prefill what this gate allows."""
    return {
        "default_keywords": list(DEFAULT_KEYWORDS),
        "families": [{"key": k, "label": f["label"], "search": f["search"]}
                     for k, f in FAMILIES.items()],
        "rejects": [{"key": k, "reason": r} for k, r, _ in REJECTS],
    }
