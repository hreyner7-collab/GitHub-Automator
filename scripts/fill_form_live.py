"""Fill the OPEN step-2 form live: covers, gallery, licence answers, consent.
Run ONLY against the form tab that is already mid-flight. Does not submit."""
import asyncio, glob, json, os, sys
from playwright.async_api import async_playwright
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uploader import CDP_URL, license_answer_indexes

DL = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads"))
SOURCE_URL = "https://www.crealitycloud.com/model-detail/modular-key-accessories-wall-holder"
LICENSE_RAW = ("creativecommons.org/licenses/by/4 Licence Cette \u0153uvre est sous licence "
               "Creative Commons Attribution 4.0.")

ZONES_JS = """() => {
    const ip = [...document.querySelectorAll('input[type=file]')]
        .filter(i => (i.accept||'').includes('.jpeg'));
    const covers = ip.filter(i => i.closest('.model-cover-content')
                                 && !i.closest('.upload-cover-last'));
    const galBox = document.querySelector('.upload-cover-last');
    const gal = galBox ? [...galBox.querySelectorAll('input[type=file]')]
                        .find(i => i.multiple) : null;
    const all = [...document.querySelectorAll('input[type=file]')];
    return JSON.stringify({web: all.indexOf(covers[0]),
                           app: covers.length > 1 ? all.indexOf(covers[1]) : -1,
                           gal: gal ? all.indexOf(gal) : -1});
}"""

LIC_JS = """([qs]) => qs.map(([qText, wantIdx]) => {
    const heads = [...document.querySelectorAll('div,span,h3,label,p')]
        .filter(e => (e.innerText||'').trim() === qText);
    for (const h of heads) {
        let box = h.parentElement;
        for (let i = 0; i < 6 && box; i++) {
            const radios = [...box.querySelectorAll('input[type=radio]')];
            if (radios.length >= 2) {
                const target = radios[wantIdx];
                if (!target) return [false];
                const lab = target.closest('label') || target.parentElement;
                if (lab) lab.click(); else target.click();
                return [true, radios.findIndex(r => r.checked)];
            }
            box = box.parentElement;
        }
    }
    return [false];
})"""


async def main():
    imgs = (sorted(glob.glob(os.path.join(DL, 'support_mural_modulaire*.webp')))
            + sorted(glob.glob(os.path.join(DL, 'support_mural_modulaire*.jpeg'))))
    print('photos:', len(imgs))
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(CDP_URL)
        page = next(p for p in b.contexts[0].pages if "create-model" in p.url)
        zones = json.loads(await page.evaluate(ZONES_JS))
        print('zones:', zones)
        locs = page.locator('input[type=file]')
        if zones['web'] >= 0 and len(imgs) > 0:
            await locs.nth(zones['web']).set_input_files(imgs[0])
            await page.wait_for_timeout(1500)
        if zones['app'] >= 0 and len(imgs) > 1:
            await locs.nth(zones['app']).set_input_files(imgs[1])
            await page.wait_for_timeout(1500)
        if zones['gal'] >= 0 and len(imgs) > 2:
            await locs.nth(zones['gal']).set_input_files(imgs[2:])
            await page.wait_for_timeout(3000)
        # dismiss any crop modal
        for _ in range(8):
            btn = page.get_by_role("button", name="Confirm").last
            try:
                if await btn.is_visible():
                    await btn.click(timeout=2000)
                    await page.wait_for_timeout(700)
            except Exception:
                break
        # licence answers
        code, (a, b2, c) = license_answer_indexes(LICENSE_RAW)
        qs = [("Allow adaptations of your work to be shared?", a),
              ("Allow commercial uses of your work?", b2),
              ("Allow sharing or redistributing of your work or its derivatives?", c)]
        res = await page.evaluate(LIC_JS, [qs])
        print('licence clicks:', res)
        await b.close()

asyncio.run(main())
