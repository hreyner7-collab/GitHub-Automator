"""Build a simple vertical gallery of the Nemotron timing-run previews."""
import base64
from pathlib import Path

d = Path(__file__).parent
imgs = sorted(d.glob("*.png"))
cards = []
for p in imgs:
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    run = "run 1 (also-cut tunnel spec)" if "151849" in p.stem else "run 2 (comb spec)"
    view = p.stem.split("_")[-1]
    cards.append(
        "<figure style='margin:0 0 24px 0'>"
        "<img style='width:100%;image-rendering:auto;background:#000' "
        "src='data:image/png;base64," + b64 + "'>"
        "<figcaption style='color:#eee;font-family:system-ui;padding:6px 4px'>"
        + run + " &mdash; " + view + " view</figcaption></figure>"
    )

html = (
    "<html><body style='background:#141414;margin:0;padding:16px'>"
    "<h2 style='color:#eee;font-family:system-ui'>Nemotron 3 Ultra &mdash; "
    "5-Slot USB Cable Dock, two independent design runs</h2>"
    + "".join(cards) + "</body></html>"
)
(d / "gallery.html").write_text(html, encoding="utf-8")
print("written", len(cards), "images")
