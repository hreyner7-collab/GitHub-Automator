"""Close every Chrome tab except the create-model form tab(s) — the cleanup for
'it opened random links to older models'. Takes an optional keep-regex (used to
keep e.g. the lotus-bowl source page). Never opens anything: closes only."""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CDP = "http://127.0.0.1:9222"

keep = sys.argv[1] if len(sys.argv) > 1 else ""
tabs = json.loads(urllib.request.urlopen(f"{CDP}/json", timeout=5).read())
kept, closed = [], 0
for t in tabs:
    if t.get("type") != "page":
        continue
    url = t.get("url", "")
    if (keep and keep in url):
        kept.append(url[:90])
        continue
    try:
        urllib.request.urlopen(f"{CDP}/json/close/{t['id']}", timeout=5).read()
        closed += 1
    except Exception:
        pass
print("closed:", closed)
for u in kept:
    print("kept:", u)
