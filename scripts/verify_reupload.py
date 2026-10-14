"""Oracle for GATES-REUPLOAD.md: R1, R2, R4, R5 (R3 is pytest).

Usage: .venv/Scripts/python.exe scripts/verify_reupload.py R1|R2|R4|R5
R1/R2 use the live debug Chrome + Creality Cloud. R4 is a real form fill
that stops before submit (CREALITY_NO_SUBMIT=1). R5 is the real upload.
"""
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

TEST_URL = ("https://www.crealitycloud.com/model-detail/"
            "under-sink-door-organiser-kitchen-cabinet")
R1_CACHE = BASE / "debug" / "r1_result.json"


def die(msg):
    print("GATE-FAIL: " + str(msg)[:300])
    sys.exit(1)


def ok(gid, detail):
    print(f"GATE-PASS {gid} :: {detail}")
    sys.exit(0)


def r1():
    from downloader import _fetch_creality
    res = _fetch_creality(TEST_URL)
    R1_CACHE.parent.mkdir(exist_ok=True)
    R1_CACHE.write_text(json.dumps(
        {k: res.get(k) for k in ("name", "author", "license", "source_url",
                                 "stl_path", "image_paths")}, indent=2),
        encoding="utf-8")
    if not res.get("name") or "organiser" not in res["name"].lower():
        die(f"name not scraped: {res.get('name')!r}")
    if not res.get("license", {}).get("raw"):
        die("license text not extracted from the page")
    if not res.get("image_paths"):
        die("no original photos downloaded")
    ok("R1", f"{res['name']!r} by {res.get('author') or '?'} · license "
             f"{res['license'].get('raw','')[:60]!r} · {len(res['image_paths'])} photo(s)")


def r2():
    if not R1_CACHE.exists():
        die("run R1 first (downloads the file)")
    res = json.loads(R1_CACHE.read_text(encoding="utf-8"))
    stl = res.get("stl_path")
    if not stl or not Path(stl).is_file():
        die(f"no downloaded model file at {stl!r}")
    from uploader import _verify_model_files
    if not _verify_model_files([stl], TEST_URL):
        die("downloaded file failed the 3MF/STL geometry audit")
    v = dict(getattr(__import__("uploader"), "UPLOAD_VERIFY"))
    f0 = (v.get("files") or [{}])[0]
    ok("R2", f"{Path(stl).name} {f0.get('size_bytes', 0)} bytes · "
             f"parts={f0.get('parts_count')} geometry_ok=True")


def _upload_run(submit: bool):
    if not R1_CACHE.exists():
        die("run R1 first")
    res = json.loads(R1_CACHE.read_text(encoding="utf-8"))
    os.environ["CREALITY_NO_SUBMIT"] = "0" if submit else "1"
    import uploader
    from downloader import classify_license
    lic = res.get("license") or {}
    if not lic.get("ok_for_reupload", True):
        die(f"license refuses re-upload: {lic.get('raw','')[:80]}")
    desc = res.get("description") or ""
    # main.py's credit rule, replicated so the gate matches the app path
    if TEST_URL not in desc:
        desc = (desc.rstrip() + "\n\nOriginal design: " + TEST_URL).strip()
    import asyncio
    return asyncio.run(uploader.upload(
        res["name"], desc, res["stl_path"], res["image_paths"],
        source_url=TEST_URL, dry_run=False,
        license_raw=(res.get("license") or {}).get("raw", "")))


def r4():
    res = _upload_run(submit=False)
    if not res.get("success"):
        die("form fill failed: " + str(res.get("error"))[:200])
    uv = getattr(__import__("uploader"), "UPLOAD_VERIFY", {})
    if not uv.get("remix_source_declared"):
        die("Remix Models source card did not register the URL: "
            + str(uv.get("remix_source_error", ""))[:150])
    # license answers must be clicked AND verified from the real radios
    la = uv.get("license_answers") or {}
    verified = la.get("radio_index_verified")
    raw = (json.loads(R1_CACHE.read_text(encoding="utf-8"))
           .get("license") or {}).get("raw", "") if R1_CACHE.exists() else ""
    from uploader import license_answer_indexes, expected_badge_token
    code, expected = license_answer_indexes(raw)
    if not verified or len(verified) != 3 or -1 in verified:
        die(f"license answers not verifiable on the form: {verified!r} "
            f"(clicked={la.get('clicked')!r})")
    if verified != expected:
        die(f"license answers {verified!r} != expected {expected!r} for "
            f"source license {code or 'unknown'}")
    # the visible proof: the CC badge the form renders under those answers
    badge = la.get("badge_license") or ""
    want_badge = expected_badge_token(code)
    if badge and la.get("badge_ok") is False:
        die("the form's CC badge reads %r but the source license needs %r — "
            "the listing would inherit the wrong terms (badge text: %r)"
            % (badge, want_badge, (la.get("badge") or "")[:200]))
    note = ("badge '%s' confirmed on the form" % badge) if badge else \
           "badge text not captured (radio answers verified instead)"
    ok("R4", "Remix source registered + license answers %s == %s for CC %s · %s "
             "(no-submit dry run)" % (verified, expected, code or "?", note))


def r5():
    res = _upload_run(submit=True)
    uv = getattr(__import__("uploader"), "UPLOAD_VERIFY", {})
    detail = f"remix_declared={uv.get('remix_source_declared')}"
    if res.get("success") and res.get("url"):
        ok("R5", f"published: {res.get('url')} · {detail}")
    if res.get("duplicate"):
        die("Creality's duplicate/originality check blocked the publish: "
            + str(res.get("error"))[:150])
    die("upload did not confirm success: " + str(res.get("error") or res.get("message"))[:200])


if __name__ == "__main__":
    gid = (sys.argv[1] if len(sys.argv) > 1 else "").upper()
    {"R1": r1, "R2": r2, "R4": r4, "R5": r5}.get(gid, lambda: die(
        "usage: verify_reupload.py R1|R2|R4|R5"))()
