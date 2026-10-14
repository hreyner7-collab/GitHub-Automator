"""Decisive check on the OPEN draft form (nothing is submitted):
  1) put a photo into the "Model Images (x/9)" gallery input and read the
     counter back — proves the gallery input + the read-back signal,
  2) put a DIFFERENT photo into the second ratio input and see which cover box
     changes — proves the two required cover slots take separate photos
     (the bug was photo 1 being sent to both).
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from playwright.async_api import async_playwright  # noqa: E402

CDP_URL = "http://127.0.0.1:9222"
DL = os.path.join(ROOT, "downloads")

READ_JS = r"""
() => {
  const all = [...document.querySelectorAll('input[type=file]')];
  const boxes = [...document.querySelectorAll('.upload-cover-box')].map(b => ({
    bg: ((b.style.backgroundImage || '').match(/blob:[^"')]+/) || [''])[0].slice(-18),
    hasImg: b.querySelectorAll('img').length }));
  const wrap = [...document.querySelectorAll('.model-cover-content > .upload-model-card_wrapper')];
  const ratios = wrap.length ? [...wrap[0].querySelectorAll('input[type=file]')].map(f => all.indexOf(f)) : [];
  const galBox = document.querySelector('.upload-cover-last');
  const gal = galBox ? [...galBox.querySelectorAll('input[type=file]')].map(f => ({i: all.indexOf(f), multiple: !!f.multiple})) : [];
  const names = (f) => (f.files ? [...f.files].map(x => x.name) : []);
  return JSON.stringify({
    counter: (document.body.innerText.match(/Model Images\s*\(\d+\/9\)/) || [''])[0],
    covers: boxes,
    ratio_inputs: ratios,
    gallery_inputs: gal,
    ratio_files: ratios.map(i => names(all[i])),
    gallery_items: galBox ? galBox.querySelectorAll('.el-upload-list__item').length : -1,
  });
}
"""


async def confirm(page, work, label):
    """The cover slots open a cropping modal; the gallery may too."""
    for _ in range(6):
        try:
            btn = work.get_by_role("button", name="Confirm").last
            if await btn.is_visible():
                await btn.click(timeout=2500)
                await page.wait_for_timeout(700)
                continue
        except Exception:
            pass
        await page.wait_for_timeout(600)
        try:
            if not await work.get_by_role("button", name="Confirm").last.is_visible():
                break
        except Exception:
            break
    print(label, "done")


async def main():
    photos = [os.path.join(DL, f"minimalist_soap_holder_img_{i}.webp") for i in (3, 5)]
    for p in photos:
        assert os.path.exists(p), p
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = b.contexts[0]
        page = next((p for p in ctx.pages if "create-model" in p.url), None)
        if not page:
            print("no form tab open"); return
        work = page
        for f in page.frames:
            try:
                if await f.locator("input[type=file]").count() > 0:
                    work = f
                    break
            except Exception:
                pass

        import json
        print("BEFORE:", await work.evaluate(READ_JS))
        state = json.loads(await work.evaluate(READ_JS))
        gal_i = next((g["i"] for g in state["gallery_inputs"] if g["multiple"]), -1)
        print("gallery input idx:", gal_i, "ratio inputs:", state["ratio_inputs"])

        # 1) gallery gets ONE photo
        if gal_i >= 0:
            await work.locator("input[type=file]").nth(gal_i).set_input_files(photos[0])
            await confirm(page, work, "gallery upload")
            print("AFTER GALLERY:", await work.evaluate(READ_JS))

        # 2) second ratio slot gets a DIFFERENT photo
        if len(state["ratio_inputs"]) > 1:
            await work.locator("input[type=file]").nth(state["ratio_inputs"][1]).set_input_files(photos[1])
            await confirm(page, work, "app cover upload")
            print("AFTER APP COVER:", await work.evaluate(READ_JS))


asyncio.run(main())
