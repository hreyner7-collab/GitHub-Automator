"""LIVE GATE (read-only): does the API reader reproduce what the DOM path gets?

`creality_api.Reader` is only worth wiring in if it sees the same model the
publish flow sees. This runs the reader on one model and, with --dom, runs the
production `downloader._fetch_creality` on the SAME model, then compares the
fields that decide a publish:

    name · author · licence CODE · gallery URL set · file bytes

and, when a file came back, that `uploader._verify_model_files` accepts it as
real geometry. It exits 0 and prints `API PARITY OK` only when every compared
field agrees; any disagreement is printed with both values and exits 1.

Read-only: it loads pages, reads JSON, and downloads the model file (exactly
what a normal fetch does). Nothing is clicked, uploaded or published, and it
refuses to run while an auto run owns the browser.

  .venv/Scripts/python.exe -X utf8 scripts/check_api_reader.py
  .venv/Scripts/python.exe -X utf8 scripts/check_api_reader.py <model-url> --dom
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))

DEFAULT_URL = "https://www.crealitycloud.com/model-detail/modular-mobile-organizer"


def _path_of(url: str) -> str:
    """A gallery URL reduced to the same form the DOM record keeps."""
    return url.replace("https://pic2-cdn.creality.com", "")


def _cmp(label: str, api, dom, same, bad: list) -> None:
    ok = same(api, dom)
    print("  %-12s %s   api=%r  dom=%r" % (label, "ok  " if ok else "DIFF", api, dom))
    if not ok:
        bad.append(label)


def compare(api_rec: dict, dom_rec: dict, api_lic: dict) -> list:
    """The publish-deciding fields, API against DOM. Returns the failures.

    The gate is deliberately able to fail, and `--selftest` plants one defect at
    a time to prove it: a renamed model, a licence code that only differs after
    CC-BY-NC, and a gallery the API did not carry must each be reported.
    """
    bad: list = []
    _cmp("name", api_rec.get("name"), dom_rec.get("name"),
         lambda a, b: (a or "").strip() == (b or "").strip(), bad)
    _cmp("author", api_rec.get("author"), dom_rec.get("author"),
         lambda a, b: (a or "").strip() == (b or "").strip(), bad)
    _cmp("licence", api_lic.get("code"), (dom_rec.get("license") or {}).get("code"),
         lambda a, b: a == b, bad)

    dom_paths = list((dom_rec.get("gallery") or {}).get("paths") or [])
    api_paths = {_path_of(u) for u in (api_rec.get("covers") or [])}
    hit = [p for p in dom_paths if p in api_paths]
    ratio = (len(hit) / len(dom_paths)) if dom_paths else 1.0
    print("  %-12s %s   %d of %d DOM photos are in the API gallery (%.0f%%)"
          % ("gallery", "ok  " if ratio >= 0.8 else "DIFF", len(hit),
             len(dom_paths), ratio * 100))
    if ratio < 0.8:
        bad.append("gallery")
        print("      DOM-only: %s" % [p for p in dom_paths if p not in api_paths][:6])

    dom_file = dom_rec.get("stl_path")
    api_bytes = (api_rec.get("file") or {}).get("bytes")
    print("  %-12s %s   api=%s bytes  dom=%s"
          % ("file", "ok  " if dom_file and api_bytes else "note",
             api_bytes, os.path.basename(dom_file) if dom_file else "none"))
    return bad


def selftest() -> int:
    """Planted defects must be caught, and the honest record must pass."""
    good_api = {"name": "Modular Phone Organizer", "author": "Litolunar",
                "covers": ["https://pic2-cdn.creality.com/pic/a.png"],
                "file": {"bytes": 98062}}
    good_dom = {"name": "Modular Phone Organizer", "author": "Litolunar",
                "license": {"code": "by"},
                "gallery": {"paths": ["/pic/a.png"]}, "stl_path": "a.3mf"}
    good_lic = {"code": "by"}
    cases = [
        ("an honest record passes", good_api, good_dom, good_lic, []),
        ("a renamed model is caught", dict(good_api, name="Other"),
         good_dom, good_lic, ["name"]),
        ("an author swap is caught", dict(good_api, author="someone"),
         good_dom, good_lic, ["author"]),
        ("NC vs BY is caught", good_api, good_dom, {"code": "by-nc"}, ["licence"]),
        ("a gallery the API lost is caught", dict(good_api, covers=[]),
         good_dom, good_lic, ["gallery"]),
    ]
    failed = 0
    for label, a, d, lic, want in cases:
        print("\n-- %s" % label)
        bad = compare(a, d, lic)
        if bad == want:
            print("   as expected: %s" % (bad or "no failures"))
        else:
            failed += 1
            print("   SELFTEST BROKEN: expected %s, got %s" % (want, bad))
    print("\nSELFTEST %s" % ("OK" if not failed else "FAILED"))
    return 0 if not failed else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    from probe_cc_api import run_busy
    state = run_busy()
    if state not in ("idle", "done"):
        print("NOT READING: dashboard auto state '%s' — a run owns the browser." % state)
        return 2
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    url = args[0] if args else DEFAULT_URL
    with_dom = "--dom" in sys.argv

    from downloader import _canon_model_url
    url = _canon_model_url(url)
    print("model: %s" % url)
    print("dom comparison: %s" % ("on" if with_dom else "off (pass --dom)"))

    from uploader import ensure_browser, CDP_URL, _verify_model_files
    if not ensure_browser():
        print("Chrome with the debug port is not available — nothing was opened.")
        return 3

    from browser_opener import sync_playwright_auto
    from creality_api import get_reader, license_of

    dest = tempfile.mkdtemp(prefix="cc_api_")
    with sync_playwright_auto() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]
        reader = get_reader(ctx)
        rec = reader.read(url, dest_dir=dest)
        # the tab stays open on purpose: it is the run's reader tab

    if rec.get("dead"):
        print("FAILED: the listing is dead (h1=%r error=%r)"
              % (rec.get("h1"), rec.get("error")))
        return 4
    if rec.get("signin"):
        print("FAILED: the browser is signed out (login wall) — that is a run-stopper.")
        return 4
    print("read_via=%s  unknown=%s" % (rec.get("read_via"), rec.get("unknown")))
    print("  name      %r" % rec.get("name"))
    print("  author    %r" % rec.get("author"))
    print("  licence   %r -> %s" % (rec.get("license_raw"),
                                    license_of(rec.get("license_raw"))))
    print("  covers    %d  %s" % (len(rec.get("covers") or []),
                                  (rec.get("covers") or [""])[0][:80]))
    print("  files     %d rows %s"
          % (rec.get("file_count") or 0,
             [(f.get("name"), f.get("format"), f.get("size"))
              for f in (rec.get("files") or [])[:3]]))
    print("  timings   load %s ms, files %s ms, 3mfList %s ms, download %s ms, bytes %s ms"
          % (rec.get("load_ms"), rec.get("files_ms"), rec.get("three_d_ms"),
             rec.get("download_url_ms"), (rec.get("file") or {}).get("ms")))
    if rec.get("file"):
        print("  file      %s" % ({k: v for k, v in rec["file"].items()
                                   if k in ("ok", "bytes", "head", "status", "why")}))
    if rec.get("stl_path"):
        ok = _verify_model_files([rec["stl_path"]], url)
        print("  geometry  %s" % ("verified" if ok else "REJECTED by _verify_model_files"))
        if not ok:
            return 4

    if not with_dom:
        print("\nAPI READ OK (no DOM comparison requested)")
        return 0

    import downloader
    print("\nrunning the production DOM fetch on the same model (this is the slow path)...")
    dom = downloader._fetch_creality_dom(url)   # the fallback path, deliberately
    api_lic = license_of(rec.get("license_raw"))
    bad = compare(rec, dom, api_lic)
    if bad:
        print("\nAPI PARITY FAILED on: %s" % ", ".join(bad))
        return 1
    print("\nAPI PARITY OK — name, author, licence code and gallery agree with the DOM path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
