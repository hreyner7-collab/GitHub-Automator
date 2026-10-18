import urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:4010/v1/models", timeout=10) as r:
        r.read()
        code = r.status
except Exception as e:
    print(f"shim unreachable: {e}")
    raise SystemExit(1)
if code != 200:
    print(f"unexpected status {code}")
    raise SystemExit(1)
print("SHIM HEALTH OK")
