"""GATE (source picker): the remix source is clicked for real, never red, never
the wrong model.

Offline. Two halves, both taken from the live form:

  · the READER — uploader.SOURCE_ROWS_JS run in a headless Chromium over a fake
    suggestion list rebuilt from the user's screenshot (a No-Derivatives row in
    red, the neighbouring "Scale model forklift", a non-English title), proving
    the red warning and the click coordinates really come out of the DOM;
  · the CHOOSER — uploader.pick_source_suggestion over those rows: the red row
    is never clicked; the source's own title wins over a look-alike; and when
    the list does not contain the source model NOTHING is clicked (that state is
    the screenshot the user sent, where the old code left the dropdown open).

Exits non-zero on any wrong decision. No network, no login, no live site.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uploader import SOURCE_ROWS_JS, pick_source_suggestion, source_score  # noqa: E402

# ── the list from the user's screenshot ("scale model pallet jack" search) ────
LEGO_ROW = """
  <div class="origin-item">
    <img src="/thumb.png">
    <div class="name">LEGO - Pallet jack</div>
    <div class="tip" style="color:#f56c6c">The license policy does not allow for
      derivative works or modifications</div>
  </div>"""
ROWS = {
    "scale model forklift": "Scale model forklift",
    "Transport pack - Forklift with cargo H0 scale": "ScaleHQ",
    "1:50 Scale Modular Warehouse Diorama - T3DTiles Edition": "TheTitan3D",
    "INPRIME R/C 1/10 WORKING JACKSTANDS": "INPRIME RC SLOTS",
    "Europallet": "Madgallo79",
}


def _row_html(name, author):
    return ('<div class="origin-item"><img src="/t.png">'
            '<div class="name">%s</div>'
            '<div class="author">Author%s</div></div>' % (name, author))


# Creality's own flag, read off the live form: a No-Derivatives row carries
# class "origin-item flex-center disabled-item" (and often the red sentence).
DISABLED_ROW = ('<div class="origin-item flex-center disabled-item">'
                '<div class="name">hydraulic Car Jack - 1:24 garage diorama</div>'
                '<div class="author">someone</div></div>')


def _page(extra_first=""):
    return ("<!doctype html><html><body><div class='model-source'>"
            "<input placeholder=\"Paste the URL or directly search for models within "
            "Creality Cloud\"><div class='dropdown'>%s%s%s</div></div></body></html>"
            % (extra_first, "".join(_row_html(n, a) for n, a in ROWS.items()),
               DISABLED_ROW))


def die(msg):
    print("FAIL:", msg)
    sys.exit(1)


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("SKIP: playwright is not installed")
        return 0

    # ── 1. the chooser, pure Python ─────────────────────────────────────────
    def rows(names, flagged=()):
        return [{"i": i, "name": n, "href": "", "flagged": n in flagged,
                 "x": 100, "y": 100 + i * 20}
                for i, n in enumerate(names)]

    src_title = "Scale model pallet jack 1/10"
    slug = "scale-model-pallet-jack"
    listed = [("LEGO - Pallet jack", True), ("Scale model forklift", False),
              ("Transport pack - Forklift with cargo H0 scale", False),
              ("Europallet", False)]
    picked = pick_source_suggestion(
        rows([n for n, _ in listed], [n for n, f in listed if f]), src_title, slug)
    if picked["pick"] is not None:
        die("a neighbouring model was chosen for %r: %r"
            % (src_title, picked["pick"]["name"]))
    print("ok: with only look-alikes on the list NOTHING is clicked "
          "(seen: %s)" % ", ".join(picked["rows"][:3]))

    # the source IS the red row: refuse it, and say which row it was
    picked = pick_source_suggestion(
        rows([src_title, "Europallet"], [src_title]), src_title, slug)
    if picked["pick"] is not None:
        die("the red row was clicked because it matched the source")
    if not picked["flagged"] or picked["flagged"][0]["name"] != src_title:
        die("a red row matching the source was not reported as the reason")
    print("ok: a red source row is refused and named (%s)"
          % picked["flagged"][0]["name"])

    listed2 = [("LEGO - Pallet jack", True),
               ("Scale model pallet jack 1/10", False),
               ("Scale model forklift", False)]
    picked = pick_source_suggestion(
        rows([n for n, _ in listed2], [n for n, f in listed2 if f]), src_title, slug)
    if not picked["pick"] or picked["pick"]["name"] != "Scale model pallet jack 1/10":
        die("the source itself was not chosen: %r" % (picked["pick"] or {}).get("name"))
    if picked["pick"]["name"] == "LEGO - Pallet jack":
        die("the red row was clicked")
    print("ok: the exact source row wins (%s, score %s) and the red row is ignored"
          % (picked["pick"]["name"], picked["score"]))

    # the same model, titled in Russian: no words in common with the slug
    ru = "Домкрат для подъема поддонов 1/10-го масштаба"
    picked = pick_source_suggestion(
        rows(["LEGO - Pallet jack", ru, "Europallet"], ["LEGO - Pallet jack"]),
        ru, slug)
    if not picked["pick"] or picked["pick"]["name"] != ru:
        die("a non-English source title was not matched: %r" % (picked["pick"] or {}))
    print("ok: a Cyrillic title is matched by name, not by the English slug")

    # the picker knows the model by its link
    by_href = [{"i": 0, "name": "Some name", "href":
                "https://www.crealitycloud.com/model-detail/%s" % slug,
                "flagged": False, "x": 1, "y": 1}]
    picked = pick_source_suggestion(by_href, "totally different title", slug)
    if not picked["pick"]:
        die("a row whose link IS the source slug was not accepted")
    print("ok: a row linking the source slug is accepted outright")

    # a loose word overlap must never qualify
    if source_score("Scale model forklift", src_title, slug) != 0:
        die("loose word overlap still qualifies a row")
    print("ok: partial word overlap scores 0 (never clickable)")

    # a row that loses ONE word of a long title is a guess, not the source. It
    # must not be clicked: declaring a guess is how a listing ends up crediting
    # a model it was not made from (the 2026-10-09 takedowns).
    guess = "Scale model pallet 1/10"
    sc = source_score(guess, src_title, slug)
    if not 0 < sc < 80:
        die("a one-word-short row scored %s (expected an uncertain <80)" % sc)
    picked = pick_source_suggestion(
        [{"i": 0, "name": guess, "href": "", "flagged": False, "x": 1, "y": 1}],
        src_title, slug)
    if picked["pick"] is not None:
        die("a row missing a word of the source title was clicked")
    print("ok: a one-word-short row (score %s) is not certain and is not clicked"
          % sc)

    # ── 2. the reader, over the real DOM shape ──────────────────────────────
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(_page(extra_first=LEGO_ROW))
        raw = await page.evaluate(SOURCE_ROWS_JS)
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        await browser.close()

    got = {r["name"]: r for r in data.get("rows", [])}
    if len(got) != len(ROWS) + 2:
        die("reader found %d rows, expected %d" % (len(got), len(ROWS) + 2))
    # the class alone (no red sentence at all) must flag a row
    off = got.get("hydraulic Car Jack - 1:24 garage diorama", {})
    if not (off.get("flagged") and off.get("disabled_item")):
        die("a disabled-item row (Creality's own No-Derivatives flag) was not "
            "flagged: %r" % off)
    lego = got.get("LEGO - Pallet jack", {})
    if not lego.get("flagged") or not lego.get("warn"):
        die("the red No-Derivatives row was not flagged: %r" % lego)
    for n in ROWS:
        if got.get(n, {}).get("flagged"):
            die("a clean row %r was flagged" % n)
    if not all(r.get("x") and r.get("y") for r in data["rows"]):
        die("some rows came back without clickable coordinates")
    if data.get("input_value"):
        die("the search input value was mis-read: %r" % data["input_value"])

    # the registered-source reader: the live form shows the chosen model in
    # .origin-choose-mark; an empty mark means nothing registered
    from uploader import _registered_mark
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(
            '<div class="origin-choose"><div class="origin-choose-mark">'
            '<div class="card">chosen <a href="/model-detail/foo">l</a></div></div>'
            '<div class="origin-choose-list"><div class="origin-item">s</div></div></div>')
        mark = await _registered_mark(page)
        await page.set_content('<div class="origin-choose-mark"></div>')
        empty = await _registered_mark(page)
        await browser.close()
    if mark.get("href") != "/model-detail/foo" or "chosen" not in mark.get("text", ""):
        die("the registered-source reader missed the chosen card: %r" % mark)
    if empty.get("text") or empty.get("href"):
        die("an empty mark must read as nothing registered: %r" % empty)
    print("ok: the registered-source area is read (text + link), and an empty "
          "one reads as nothing registered")
    print("ok: the reader flags the red row (warn=%s) and gives clickable "
          "coordinates for all %d rows" % (lego["warn"], len(data["rows"])))
    print("PASS: the source picker never clicks a red row nor the wrong model")
    return 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))
