"""READ-ONLY: dump the create-model form's image file inputs + upload zones.

No clicking, no uploading, no submitting — it just reads the DOM of the form
tab that is already open in the debug Chrome. Used to learn exactly WHERE the
cover images and the model-image gallery live, so the uploader can fill them by
label instead of by positional index.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.async_api import async_playwright  # noqa: E402

CDP_URL = "http://127.0.0.1:9222"

DUMP_JS = r"""
() => {
  const sec = (el) => {
    // climb a few ancestors collecting the nearest labels/headings
    let out = [];
    let node = el;
    for (let i = 0; i < 7 && node; i++) {
      const own = [...node.children || []]
        .map(c => (c.innerText || '').trim().split('\n')[0])
        .filter(t => t && t.length < 60);
      out = out.concat(own.slice(0, 4));
      const t = (node.innerText || '').trim().split('\n')[0];
      if (t && t.length < 60) out.push(t);
      node = node.parentElement;
    }
    return [...new Set(out)].slice(0, 18);
  };
  const inputs = [...document.querySelectorAll('input[type=file]')].map((inp, i) => ({
    i, accept: inp.accept || '', multiple: !!inp.multiple,
    cls: inp.className || '', visible: !!(inp.offsetWidth || inp.offsetHeight),
    sec: sec(inp),
  }));
  const uploads = [...document.querySelectorAll('.el-upload')].map((u, i) => {
    const inp = u.querySelector('input[type=file]');
    return {
      i,
      text: (u.innerText || '').trim().replace(/\n/g, ' | ').slice(0, 90),
      inputIdx: inp ? [...document.querySelectorAll('input[type=file]')].indexOf(inp) : -1,
      accept: inp ? (inp.accept || '') : '',
      multiple: inp ? !!inp.multiple : null,
      items: u.parentElement ? u.parentElement.querySelectorAll('.el-upload-list__item, .el-upload-list li').length : 0,
      imgs: u.parentElement ? [...u.parentElement.querySelectorAll('img')].map(x => (x.src || '').slice(-60)).slice(0, 10) : [],
    };
  });
  // every zone that already shows uploaded images
  const covers = [...document.querySelectorAll('[class*=cover i], [class*=upload-list i]')]
    .map(e => ({ cls: (e.className || '').toString().slice(0, 60),
                 t: (e.innerText || '').trim().replace(/\n/g, ' | ').slice(0, 70),
                 imgs: e.querySelectorAll('img').length }));
  return JSON.stringify({ url: location.href, inputs, uploads, covers }, null, 1).slice(0, 6000);
}
"""


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "create-model" in p.url), None)
        if not page:
            print("NO create-model tab open — open the upload form first.")
            return
        work = page
        for f in page.frames:
            try:
                if await f.locator("input[type=file]").count() > 0:
                    work = f
                    break
            except Exception:
                pass
        dump = await work.evaluate(DUMP_JS)
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "debug", "form_image_inputs.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(dump)
        print("page:", page.url)
        print("frame:", work.url[:100])
        print("written:", out, len(dump), "chars")


asyncio.run(main())
