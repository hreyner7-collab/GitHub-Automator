// Harness for the model-page gallery reader in downloader.py.
//
// gallery_js is one big browser function, so a change to it used to be verified
// only by a live run on the account. This file extracts the function from the
// Python source and runs it against a hand-built DOM, so the rules can be
// proved (and re-proved) without a browser:
//
//   .venv/Scripts/python.exe -B -c "print(1)"   # nothing to do first
//   bun scripts/check_gallery_js.js
//
// Each case is a page shape Creality really ships; the last line prints the
// photos found and which rule found them. Any FAILED line means the reader
// would leave a listing with no cover photo.
const fs = require('fs');
const path = require('path');

// ── take gallery_js straight out of downloader.py (no copy to drift) ────────
// GALLERY_SRC can point at another copy of downloader.py — used to run these
// same cases against the PREVIOUS reader, so a passing case is proof the old
// one really was blind to that page shape.
const SRC_FILE = process.env.GALLERY_SRC
    || path.join(__dirname, '..', 'downloader.py');
const src = fs.readFileSync(SRC_FILE, 'utf8');
const decl = src.indexOf('gallery_js = ');
if (decl < 0) throw new Error('gallery_js not found in ' + SRC_FILE);
const open = src.indexOf('"""', decl);
if (open < 0) throw new Error('gallery_js has no string body in ' + SRC_FILE);
const start = open + 3;                                  // after r""" or """
const end = src.indexOf('"""', start);
const GALLERY_JS = src.slice(start, end);

// ── a very small DOM: enough CSS for the selectors the reader uses ──────────
class El {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = tag.toUpperCase();
    this.attrs = attrs;
    this.className = attrs.class || '';
    this.currentSrc = attrs.currentSrc || '';
    this.src = attrs.src || '';
    this.naturalWidth = attrs.naturalWidth === undefined ? 0 : attrs.naturalWidth;
    this.parentElement = null;
    this.children = children;
    for (const c of children) c.parentElement = this;
  }
  getAttribute(n) {
    return Object.prototype.hasOwnProperty.call(this.attrs, n) ? this.attrs[n] : null;
  }
  getBoundingClientRect() { return this.attrs.rect || { x: 0, y: 0, width: 24, height: 24 }; }
  matches(sel) { return sel.split(',').some((s) => matchCompound(this, s.trim())); }
  closest(sel) {
    let e = this;
    while (e) { if (matchCompound(e, sel)) return e; e = e.parentElement; }
    return null;
  }
  all() {
    const out = [];
    const walk = (e) => { for (const c of e.children) { out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelectorAll(sel) {
    const alts = sel.split(',').map((s) => s.trim());
    return this.all().filter((e) => alts.some((a) => matchChain(e, a)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

// a chain is "a b c" — c must match, and some ancestor matches a, then b...
function matchChain(el, chain) {
  const parts = chain.split(/\s+/).filter(Boolean);
  if (!parts.length) return false;
  if (!matchCompound(el, parts[parts.length - 1])) return false;
  let e = el.parentElement, i = parts.length - 2;
  while (i >= 0) {
    let found = false;
    while (e) {
      if (matchCompound(e, parts[i])) { found = true; e = e.parentElement; break; }
      e = e.parentElement;
    }
    if (!found) return false;
    i -= 1;
  }
  return true;
}

// a compound is tag + .class + [attr] / [attr=v] / [attr*=v] run together
function matchCompound(el, compound) {
  const re = /([a-zA-Z][\w-]*)|\.([\w-]+)|\[([\w:-]+)(?:([*^$|~]?=)["']?([^\]"']*)["']?)?\]/g;
  let m, hit = false;
  while ((m = re.exec(compound))) {
    const [, tag, cls, attr, op, val] = m;
    if (tag) { if (el.tagName !== tag.toUpperCase()) return false; hit = true; continue; }
    if (cls) { if (String(el.className).split(/\s+/).indexOf(cls) < 0) return false; hit = true; continue; }
    if (attr) {
      const v = el.getAttribute(attr);
      if (v === null) return false;
      if (op) {
        if (op === '=') { if (v !== val) return false; }
        else if (op === '*=') { if (String(v).indexOf(val) < 0) return false; }
        else return false;
      }
      hit = true;
    }
  }
  return hit;
}

// document + getComputedStyle over a tree; background-image comes from attrs.bg
function makeDoc(root, metas, baseURI) {
  const doc = new El('body', {}, root);
  doc.baseURI = baseURI || 'https://www.crealitycloud.com/model-detail/x';
  const extras = metas || [];
  const origAll = doc.all.bind(doc);
  doc.all = () => origAll().concat(extras);
  doc.querySelectorAll = function (sel) {
    const alts = sel.split(',').map((s) => s.trim());
    return this.all().filter((e) => alts.some((a) => matchChain(e, a)));
  };
  doc.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
  return doc;
}

const STYLES = new WeakMap();
function styleOf(el, bg) { STYLES.set(el, bg); return el; }
const getComputedStyle = (el) => ({ backgroundImage: STYLES.get(el) || 'none' });

// ── run the reader the way page.evaluate() runs it ──────────────────────────
function compile(doc) {
  const fn = new Function('document', 'getComputedStyle',
                          'return (' + GALLERY_JS.trim() + ')');
  return fn(doc, getComputedStyle)();
}

function run(label, doc, expect) {
  const g = compile(doc);
  const got = g.urls;
  const ok = JSON.stringify(got.slice().sort()) === JSON.stringify(expect.slice().sort());
  console.log((ok ? 'PASS ' : 'FAIL ') + label);
  if (!ok) {
    console.log('      expected:', expect);
    console.log('      got     :', got);
  }
  console.log('      how=' + JSON.stringify(g.how) +
              ' strip=' + g.strip + ' imgs=' + g.imgs +
              ' creator_imgs=' + g.creator_imgs + ' next=' + !!g.next);
  return ok;
}

const CDN = 'https://pic2-cdn.creality.com/crealityCloud/upload';
const FULL = CDN + '/a001/photo-1.jpg';
const FULL2 = CDN + '/a001/photo-2.jpg';
const COMMON = 'https://pic2-cdn.creality.com/crealityCloud/common/b/photo-3.jpg';

const results = [];

// 1) THE BUG THIS FIXES: a lazy strip whose <img src> is a 1x1 placeholder and
//    whose real photo sits in data-src. The old reader looked at src only.
{
  const placeholder = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
  const strip = new El('div', { class: 'swiper-thumb' }, [
    new El('div', { class: 'thumb' }, [new El('img', { src: placeholder, 'data-src': FULL, naturalWidth: 0 })]),
    new El('div', { class: 'thumb' }, [new El('img', { src: placeholder, 'data-src': FULL2, naturalWidth: 0 })]),
  ]);
  const gallery = new El('div', { class: 'model-gallery' }, [strip]);
  results.push(run('lazy strip: placeholder src + data-src photo',
                   makeDoc([gallery], [], 'https://www.crealitycloud.com/model-detail/x/'),
                   [FULL, FULL2]));
}

// 2) gallery slides that have not loaded yet (naturalWidth 0) must still count
//    — the size floor belongs to the downloaded pixels, not the empty <img>.
{
  const gal = new El('div', { class: 'model-gallery' }, [
    new El('div', { class: 'cover' }, [new El('img', { src: FULL, naturalWidth: 0 })]),
  ]);
  results.push(run('unloaded lazy slide (naturalWidth 0)', makeDoc([gal]), [FULL]));
}

// 3) a <picture> with the photo only in a <source srcset>.
{
  const pic = new El('picture', {}, [
    new El('source', { srcset: 'https://x.example/w400.jpg 400w, ' + FULL + ' 1600w' }),
    new El('img', { src: 'data:image/png;base64,iVBORw0KGgo=', naturalWidth: 1 }),
  ]);
  const gal = new El('div', { class: 'model-gallery' }, [new El('div', { class: 'cover' }, [pic])]);
  results.push(run('picture > source srcset holds the photo', makeDoc([gal]), [FULL]));
}

// 4) a cover painted as a CSS background, no <img> anywhere in the gallery.
{
  const inner = styleOf(new El('div', { class: 'swiper-cover' }, []), 'url("' + COMMON + '")');
  const gal = new El('div', { class: 'model-gallery' }, [inner]);
  results.push(run('gallery painted as a CSS background', makeDoc([gal]), [COMMON]));
}

// 5) nothing rendered at all, but the page declares its cover in og:image.
{
  const meta = new El('meta', { property: 'og:image', content: FULL });
  results.push(run('only og:image declares the photo', makeDoc([], [meta]), [FULL]));
}

// 6) NO REGRESSION: a plate-preview card (list-3mf) is never a cover photo,
//    and a page with no creator photo at all must still come back empty.
{
  const plate = new El('div', { class: 'list-3mf' }, [
    new El('div', { class: 'creality-model-cover' }, [
      new El('img', { src: CDN + '/other/plate.jpg', naturalWidth: 600 })]),
  ]);
  const avatar = new El('div', { class: 'comment-list' }, [
    new El('img', { src: 'https://pic2-cdn.creality.com/faces/u.jpg', naturalWidth: 600 })]);
  const r1 = run('plate + avatar rails are not photos', makeDoc([plate, avatar]), []);
  results.push(r1);
  console.log('      (expected how=[] creator_imgs=0 above)');
}

// 7) NO REGRESSION: the tiny resize variant (an avatar size) is refused, the
//    full-size original of the same photo is kept.
{
  const tiny = CDN + '/a002/photo-1.jpg?x-oss-process=image/resize,w_40/format,webp';
  const big = CDN + '/a002/photo-1.jpg';
  const strip = new El('div', { class: 'swiper-thumb' }, [
    new El('img', { src: tiny, currentSrc: tiny, naturalWidth: 40 }),
    new El('img', { src: big, currentSrc: big, naturalWidth: 1200 }),
  ]);
  const gal = new El('div', { class: 'model-gallery' }, [strip]);
  results.push(run('resize,w_40 variant refused, original kept', makeDoc([gal]), [big]));
}

// 8) a protocol-relative lazy URL must become one the downloader can fetch.
{
  const strip = new El('div', { class: 'swiper-thumb' }, [
    new El('img', { src: 'data:image/gif;base64,R0lGOD', 'data-src': '//pic2-cdn.creality.com/crealityCloud/upload/a003/p.jpg' }),
  ]);
  const gal = new El('div', { class: 'model-gallery' }, [strip]);
  results.push(run('protocol-relative data-src gets a scheme', makeDoc([gal]),
                   ['https://pic2-cdn.creality.com/crealityCloud/upload/a003/p.jpg']));
}

const bad = results.filter((x) => !x).length;console.log(bad ? '\n' + bad + ' case(s) FAILED' : '\nall ' + results.length + ' gallery cases passed');
process.exitCode = bad ? 1 : 0;
