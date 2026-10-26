"""Pictures nobody photographed: the keyless listing-photo generator.

MakerWorld wants images of the model on the listing. This module draws them from
words — no key, no account, no money — through the free anonymous endpoint at
image.pollinations.ai, which answers a GET with a JPEG.

Two facts measured on 2026-10-26 shape everything here:

  · the anonymous tier is thin. One picture in ~6 seconds, then HTTP 402 for the
    next minute or two, then one again. So `generate` retries on 402/429/5xx with
    a back-off and a wall-clock budget, and `generate_photos` reports how many it
    actually got instead of pretending;
  · that endpoint answers a 402 *as an image* (content-type image/jpeg, a small
    "payment required" picture). A status check alone is not enough, so every
    payload is decoded and measured before it is counted as a photo;
  · `nologo=true` is ignored. Every picture comes back with a "pollinations.ai"
    mark in the bottom-right corner, so the strip it sits in is cut before the
    file is written — see MARK_BAND.

Nothing in here is allowed to quietly hand back somebody else's photograph: a
caller that asks for three pictures and gets two is told it got two.
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse

import requests

ENDPOINT = "https://image.pollinations.ai/prompt/"
MIN_BYTES = 8 * 1024          # anything smaller is an error card, not a photo
MIN_EDGE = 480                # pixels on the short side
MAX_EDGE = 1440
MAX_SET = 16                  # MakerWorld's photo slot takes at most this many
# The bottom-right corner mark, as a fraction of the frame height. Measured on
# the 886x665 files this endpoint returns: the "pollinations.ai" text occupies
# rows 632-664, i.e. the lowest 5% of the picture, so 6% is cut with a margin.
MARK_BAND = 0.06

# The look the user picked: one object, alone, plain studio sweep, no words on
# the picture. Filament colour and backdrop rotate so a set of three is not the
# same photograph three times.
VARIANTS = (
    ("terracotta clay orange", "seamless pale warm-gray studio sweep"),
    ("sage green", "dark graphite stone countertop"),
    ("chalk white", "softly blurred warm wooden shelf"),
    ("deep navy blue", "light concrete surface beside a plain wall"),
    ("matte black", "dusty workshop bench with tools blurred out"),
    ("bright coral red", "white marble surface, morning light"),
)

WORDS = ("a single 3D printed object, {what}, printed in matte {colour} PLA with fine "
         "horizontal layer lines across the plastic, standing alone centred in the frame "
         "on a {backdrop}, soft studio light from the upper left, a gentle contact shadow "
         "under the object, 50mm lens, shallow depth of field, sharp focus on the object, "
         "photorealistic product photograph for a 3D model listing. Exactly one object and "
         "nothing else in the picture. No text, no letters, no numbers, no words, no logo, "
         "no badge, no caption, no watermark, no signature, no corner mark, no people, "
         "no hands.")


def slug(name: str) -> str:
    """Filesystem-safe handle for a listing name."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", (name or "model").strip()).strip("_").lower()
    return (s[:48] or "model")


def subject(name: str, description: str = "") -> str:
    """What the object is, in the few words the generator can actually use.

    The listing name is the honest label; the source description only adds a
    clause or two of shape. Long descriptions are cut rather than summarised —
    a prompt that runs on makes the model invent furniture.
    """
    text = re.sub(r"<[^>]+>", " ", description or "")
    text = re.sub(r"\s+", " ", text).strip()
    first = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(sentence) > 24 and "http" not in sentence.lower():
            first = sentence
            break
    what = (name or "").strip().rstrip(".!")
    if first:
        what = f"{what}: {first[:140].rstrip()}"
    return what or "a small 3D printed household object"


def prompt(name: str, description: str = "", colour: str = "",
           backdrop: str = "", variant: int = 0) -> str:
    """The standing words for one picture. `variant` picks the colour/backdrop."""
    col, bg = VARIANTS[variant % len(VARIANTS)]
    return WORDS.format(what=subject(name, description),
                        colour=(colour or col), backdrop=(backdrop or bg))


def _decode(raw: bytes):
    """Real, decodable, big enough — or None. Guards the 402-as-image case."""
    if not raw or len(raw) < MIN_BYTES:
        return None
    if raw[:2] != b"\xff\xd8" and raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:                                    # noqa: BLE001
        return None
    if img is None:
        return None
    h, w = img.shape[:2]
    if min(h, w) < MIN_EDGE or max(h, w) > MAX_EDGE * 2:
        return None
    # A flat card (the endpoint's own error picture) has no detail to find.
    try:
        import cv2
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if float(cv2.Laplacian(grey, cv2.CV_64F).var()) < 25.0:
            return None
    except Exception:                                    # noqa: BLE001
        pass
    return (h, w)


def _clean(raw: bytes) -> bytes:
    """Cut the generator's corner mark off the bottom of an already-checked frame.

    This is the only edit a drawn picture gets. A picture too short to spare the
    band is left whole rather than resized or painted over.
    """
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return raw
        h, w = img.shape[:2]
        cut = int(round(h * MARK_BAND))
        if cut and min(w, h - cut) >= MIN_EDGE:
            ok, buf = cv2.imencode(".jpg", img[: h - cut],
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            if ok:
                return buf.tobytes()
    except Exception:                                    # noqa: BLE001
        pass
    return raw


RETRY_ON = ("402", "429", "500", "502", "503", "504")
BACKOFF = (0, 20, 45, 90, 120, 180)


class Busy(RuntimeError):
    """The endpoint refused for a reason that waiting can fix.

    Only this is retried. A `requests` failure on a machine with no network says
    "HTTPSConnectionPool(host=...)", which any substring check on "HTTP" would
    read as a rate limit — and the back-off then spends four minutes per picture
    on a wall that will never come down.
    """

    def __init__(self, code):
        super().__init__("HTTP %s" % code)
        self.code = str(code)


def fetch_jpeg(prompt_text: str, seed: int = 0, width: int = 1024, height: int = 768,
               timeout: int = 120) -> bytes:
    """One GET to the anonymous endpoint. Raises Busy on a queue, anything else
    on a refusal that a wait cannot fix."""
    url = (ENDPOINT + urllib.parse.quote(prompt_text) +
           f"?width={width}&height={height}&nologo=true&model=flux&seed={seed}")
    r = requests.get(url, timeout=timeout, headers={"Accept": "image/*"})
    if r.status_code != 200:
        code = str(r.status_code)
        if code in RETRY_ON:
            raise Busy(code)
        raise RuntimeError(f"HTTP {code}")
    return r.content


def generate(prompt_text: str, out_dir: str, name: str = "photo", seed: int = 0,
             fetch=None, waits=BACKOFF, budget: float = 420.0,
             sleep=time.sleep, log=None) -> dict:
    """Draw one picture into `out_dir`, retrying inside the free tier's limits.

    {ok, path, prompt, tries, why}. Never raises for a rate limit: the caller has
    to look at `ok`, which is the whole point of keeping this apart from the
    photograph restyler.
    """
    fetch = fetch or fetch_jpeg
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    why = ""
    tried = 0
    for attempt, wait in enumerate(waits):
        tried = attempt + 1
        if attempt and time.time() - started + wait > budget:
            why = f"the free tier is busy and the {int(budget)}s budget ran out ({why})"
            break
        if attempt and wait:
            sleep(wait)
        try:
            raw = fetch(prompt_text, seed=seed + attempt * 7,
                        timeout=max(15, int(budget - (time.time() - started))))
        except Busy as exc:
            why = str(exc)
            continue
        except Exception as exc:                         # noqa: BLE001
            why = f"{type(exc).__name__}: {exc}"
            if "timeout" not in why.lower():
                break                                    # a wall, not a queue
            continue
        if not _decode(raw):
            why = "the endpoint answered with something that is not a usable photograph"
            continue
        body = _clean(raw)
        ext = ".png" if body[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        stem = "ai_%s_%s_%d" % (slug(name), time.strftime("%H%M%S"), seed)
        path = os.path.join(out_dir, stem + ext)
        n = 1
        while os.path.exists(path):
            # Two variants drawn in the same second must not share a file: the
            # cover slot and the photo slot would get the same picture, which is
            # the "Please set the model cover" rejection this split exists to avoid.
            path = os.path.join(out_dir, "%s_%d%s" % (stem, n, ext))
            n += 1
        with open(path, "wb") as fh:
            fh.write(body)
        if log:
            log(f"drew {os.path.basename(path)} in {int(time.time() - started)}s")
        return {"ok": True, "path": path, "prompt": prompt_text,
                "tries": attempt + 1, "seconds": round(time.time() - started, 1)}
    return {"ok": False, "path": "", "prompt": prompt_text,
            "tries": tried, "why": why or "no picture came back"}


def generate_photos(name: str, description: str = "", count: int = 3,
                    out_dir: str = "downloads", colour: str = "", backdrop: str = "",
                    start_variant: int = 0, fetch=None, budget_each: float = 420.0,
                    budget_set: float = 900.0, waits=BACKOFF, min_photos: int = 3,
                    sleep=time.sleep, log=None) -> dict:
    """A whole photo set for one listing, generated from its own words.

    Returns {photos, notes, asked, got, ok, seconds}. `ok` is only True when the
    set clears `min_photos`, which is MakerWorld's own floor — a short set is
    reported short, and the caller must not top it up with someone else's
    photograph. `budget_set` bounds the whole set, so a publish cannot sit
    waiting on a busy free tier for half an hour.
    """
    count = min(max(int(count), int(min_photos or 0)), MAX_SET)
    started = time.time()
    photos, notes = [], []
    for i in range(count):
        if time.time() - started > budget_set:
            notes.append(f"picture {i + 1} of {count} was never tried: the set ran "
                         f"out of its {int(budget_set)}s budget")
            break
        p = prompt(name, description, colour=colour, backdrop=backdrop,
                   variant=start_variant + i)
        one = generate(p, out_dir, name=name, seed=1000 + i * 37, fetch=fetch,
                       waits=waits, budget=min(budget_each,
                                               budget_set - (time.time() - started)),
                       sleep=sleep, log=log)
        if one["ok"]:
            photos.append(one["path"])
            notes.append(f"{os.path.basename(one['path'])}: {p[:70]}…")
        else:
            notes.append(f"picture {i + 1} of {count} did not come back — {one['why']}")
    got = len(photos)
    return {"photos": photos, "notes": notes, "asked": count, "got": got,
            "ok": got >= int(min_photos or 0), "seconds": round(time.time() - started, 1),
            "generated": True}
