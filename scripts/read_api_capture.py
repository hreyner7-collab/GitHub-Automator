"""Read a probe capture and say, in one screen, which endpoint is worth building on.

The probe records every /api/ call a model page makes — usually 20-40 of them,
most of which are comments, ads and unread-count polling. This ranks them by the
only thing that matters: does ONE answer carry the fields a publish is decided on
(name, author, licence, gallery, file), and can it be asked by model id alone?

  .venv/Scripts/python.exe -X utf8 scripts/read_api_capture.py debug/api_capture_X.json
  .venv/Scripts/python.exe -X utf8 scripts/read_api_capture.py            # newest capture
"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the fields that decide whether a model can be published at all
NEEDLES = {
    "name": re.compile(r'"(?:groupName|name|title)"\s*:'),
    "author": re.compile(r'"(?:nickName|userName|author)"\s*:'),
    "licence": re.compile(r'license|licence|CC-BY|CXY-SL|attribution', re.I),
    "gallery": re.compile(r'"(?:covers|images|imageList|modelImages)"\s*:\s*\['),
    "description": re.compile(r'"(?:description|content|summary)"\s*:'),
    "file": re.compile(r'"(?:fileList|files|threeDUrl|filePath|modelFileUrl|url)"\s*:'),
    "size": re.compile(r'"totalFileSize"\s*:'),
    "id": re.compile(r'"(?:id|shareId|modelId)"\s*:\s*"?[0-9a-f]{8,}'),
}
SEARCH_PARAM_NEEDLES = {
    "keyword": re.compile(r'"(?:keyword|q|query|searchWord)"\s*:', re.I),
    "sort": re.compile(r'"(?:sort|order|sortType|orderBy)"\s*:'),
    "licence_filter": re.compile(r'"(?:license|licence|copyright)[A-Za-z]*"\s*:'),
    "page": re.compile(r'"(?:pageNo|pageNum|pageSize|offset|limit)"\s*:'),
}


def _path(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("?")[0]


def _bodies(entry: dict) -> str:
    return (entry.get("body") or "") + (entry.get("req_body") or "")


def rank(capture: dict) -> list:
    """One row per endpoint path, with the fields it demonstrably carries."""
    seen: dict = {}
    for model in capture.get("models") or []:
        for c in model.get("calls") or []:
            p = _path(c.get("url", ""))
            row = seen.setdefault(p, {"path": p, "hits": 0, "models": set(),
                                      "methods": set(), "statuses": set(),
                                      "fields": set(), "params": set(),
                                      "max_body": 0, "sample": ""})
            row["hits"] += 1
            row["methods"].add(c.get("method"))
            row["statuses"].add(c.get("status"))
            blob = c.get("body") or ""
            if blob:
                row["models"].add(model.get("requested", "")[-40:])
                row["max_body"] = max(row["max_body"], len(blob))
                if not row["sample"]:
                    row["sample"] = blob[:220]
            for name, rx in NEEDLES.items():
                if rx.search(blob):
                    row["fields"].add(name)
            for name, rx in SEARCH_PARAM_NEEDLES.items():
                if rx.search(c.get("req_body") or ""):
                    row["params"].add(name)
    rows = list(seen.values())
    # widest field coverage first; an endpoint that answers nothing is noise
    rows.sort(key=lambda r: (-len(r["fields"]), -len(r["models"]), -r["max_body"]))
    return rows


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].endswith(".json"):
        path = sys.argv[1]
        if not os.path.isabs(path):
            path = os.path.join(BASE, path)
    else:
        files = sorted(glob.glob(os.path.join(BASE, "debug", "api_capture_*.json")),
                       key=os.path.getmtime)
        if not files:
            print("No capture yet — run scripts/probe_cc_api.py first.")
            return 2
        path = files[-1]
    capture = json.load(open(path, encoding="utf-8"))
    rows = rank(capture)
    print("capture: %s" % os.path.relpath(path, BASE))
    print("%d endpoints seen on %d model pages\n" % (
        len(rows), len(capture.get("models") or [])))

    decided = {"name", "author", "licence", "gallery", "file"}
    for r in rows:
        if not r["fields"] and not r["params"]:
            continue
        print("%-58s x%-3d models=%d %s" % (
            r["path"], r["hits"], len(r["models"]), sorted(m for m in r["methods"])))
        print("      fields : %s" % (",".join(sorted(r["fields"])) or "-"))
        if r["params"]:
            print("      params : %s" % ",".join(sorted(r["params"])))
        print("      body   : %d bytes  %s" % (r["max_body"], r["sample"][:150]))

    whole = set()
    for r in rows:
        whole |= r["fields"]
    missing = sorted(decided - whole)
    print("\nendpoints as a group cover: %s" % (",".join(sorted(whole)) or "nothing"))
    if missing:
        print("STILL MISSING from every captured answer: %s" % ",".join(missing))
    best = next((r for r in rows if decided <= r["fields"]), None)
    print("\nONE-CALL ANSWER: %s" % (best["path"] if best else
                                     "no single endpoint carries all of it — "
                                     "a model read will need the chain above"))

    print("\n--- in-tab replay: does a POST answer from a BACKGROUND tab, and fast? ---")
    core = {"licence_text", "name", "author", "covers"}
    complete = 0
    for m in capture.get("models") or []:
        rep = m.get("replay") or {}
        rows = rep.get("rows") or []
        print("%s" % (m.get("opened") or m.get("requested"))[:78])
        print("   ids: %s  html: %s" % (rep.get("ids"), m.get("ids_in_html")))
        th = rep.get("throttle") or {}
        if th.get("tried"):
            bg = th.get("background_ms") or []
            print("   throttle %s: background %s ms -> foreground %s ms (tab was %s)"
                  % (th.get("endpoint"), bg, th.get("foreground_ms"), th.get("visibility")))
        got, keys, urls = set(), [], []
        for row in rows:
            got |= {k[4:] for k, v in row.items() if v is True and k.startswith("has_")}
            if row.get("file_key"):
                keys.append(row["file_key"])
            if row.get("has_file_url"):
                urls.append(row["endpoint"])
            verdict = ("accepted" if row.get("ok") else
                       "accepted WITH TOKEN" if row.get("needs_token") else
                       "refused (%s)" % row.get("status"))
            print("   %-44s %s %-22s %5s ms  %s" % (
                row["endpoint"], row.get("method"), verdict, row.get("first_ms"),
                ",".join(sorted(got)) or "nothing"))
            if row.get("body_head"):
                print("      %s" % row["body_head"][:170].replace("\n", " "))
        if core <= got:
            complete += 1
        print("   chain: %s | file URL: %s | file keys seen: %s" % (
            "COMPLETE (name+author+licence+gallery)" if core <= got
            else "incomplete, missing %s" % ",".join(sorted(core - got)),
            ",".join(urls) or "NONE", keys[:2] or "-"))
    print("\n%d of %d model pages can be read end to end from these answers alone."
          % (complete, len(capture.get("models") or [])))

    scan = (capture.get("models") or [{}])[0].get("download_endpoints") or {}
    print("\n--- the site's own code, searched for a file/download endpoint ---")
    if scan.get("error"):
        print("scan failed: %s" % scan["error"])
    else:
        print("%d bundles mention these paths:" % scan.get("scripts", 0))
        for e in (scan.get("endpoints") or [])[:20]:
            print("   %s (x%d)" % (e, scan["counts"].get(e, 1)))
        if not scan.get("endpoints"):
            print("   nothing — no download/file API path named in the loaded code")
    s = capture.get("search") or {}
    print("\nsearch page %s -> %d calls" % (s.get("opened", "?"), len(s.get("calls") or [])))
    for c in s.get("calls") or []:
        params = sorted(k for k, rx in SEARCH_PARAM_NEEDLES.items()
                        if rx.search(c.get("req_body") or ""))
        fields = sorted(k for k, rx in NEEDLES.items() if rx.search(c.get("body") or ""))
        if params or fields:
            print("   %s %s params=[%s] fields=[%s]" % (
                c.get("method"), _path(c.get("url", "")), ",".join(params), ",".join(fields)))
            print("      req: %s" % (c.get("req_body") or "")[:220])
    return 0


if __name__ == "__main__":
    sys.exit(main())
