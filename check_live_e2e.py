"""Oracle for gate G5: the live end-to-end evidence file must show an
overall PASS verdict for the composed tool loop (lion + dog + honesty)."""
import sys

try:
    with open("ftai_live_e2e_results.txt", encoding="utf-8") as f:
        text = f.read()
except FileNotFoundError:
    print("LIVE E2E PENDING: ftai_live_e2e_results.txt not written yet")
    raise SystemExit(1)

tail = text.split("RESULT:")[-1]
if "'overall': 'PASS'" in tail and "BLOCKED" not in tail:
    if "L2 lion" in text and "L3 dog" in text:
        print("LIVE E2E PASS")
        raise SystemExit(0)
print("LIVE E2E FAILED:", tail.strip()[:200])
raise SystemExit(1)
