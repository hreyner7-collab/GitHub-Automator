"""GATE ORACLE (photo fetch): one listing's photos are fetched concurrently.

_fetch_creality used to download a listing's photos one blocking HTTP request
at a time: eight photos at 300 ms of CDN latency each cost the run 2.4 s of
nothing but waiting, on every single model.

This starts a LOCAL server on 127.0.0.1 that answers every request 0.4 s late
(a stand-in for CDN latency, no network, no account), points downloader at it,
and measures downloader._download_images twice: once with the pool as shipped
and once forced to a single worker. It asserts

  · the server really was asked for every photo (no silent early exit);
  · the pooled run is at least 3x faster than the serial run — the control that
    proves the checker can fail, since a serial pool measures its own baseline;
  · both runs keep and drop the SAME photos in the SAME order, so the speed came
    from overlapping I/O and not from skipping the audit.

  .venv/Scripts/python.exe -X utf8 scripts/check_photo_parallel.py
"""
import http.server
import os
import re
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

N = 8                    # photos in the fake listing
DELAY = 0.4              # seconds each response is late


def _photo(index: int) -> bytes:
    """A real, decodable JPEG that the download audit KEEPS.

    The first version of this gate served a hand-made byte blob and every photo
    was dropped as "not a readable image" — the kept/dropped equivalence then
    proved nothing but two identical empty lists. A photo the pipeline actually
    keeps is the positive control this oracle needs.
    """
    import cv2
    import numpy as np

    rng = np.random.default_rng(1000 + index)
    a = rng.integers(0, 256, size=(600, 600, 3), dtype=np.uint8)
    a[:, :, 2] = np.clip(a[:, :, 2].astype(int) + 40 * index, 0, 255).astype(np.uint8)
    a[150:450, 150:450] = (30 + 9 * index, 90, 200)      # a hard-edged subject
    a[200:400, 220:380] = (210, 40, 40)
    ok, buf = cv2.imencode(".jpg", a, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise SystemExit("FAIL: could not encode a test photo — no cv2 in this venv")
    return buf.tobytes()


PHOTOS = {}


class Slow(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        type(self).hits.append(self.path)
        body = PHOTOS.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        time.sleep(DELAY)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):          # keep the gate output clean
        pass


def outcome(workers, tmp, urls):
    """Run _download_images with a given pool size and return (seconds, result).
    workers=None leaves the shipped pool alone; 1 forces one request at a time."""
    import downloader
    downloader.DOWNLOADS_DIR = tmp
    if workers is not None:
        downloader.IMAGE_FETCH_WORKERS = workers
    report = []
    t0 = time.time()
    kept = downloader._download_images(urls, "gatephotos", limit=N,
                                        strict=False, report=report)
    return time.time() - t0, kept, [d["url"] for d in report]


def main() -> int:
    if not re.search(r"IMAGE_FETCH_WORKERS\s*=\s*\d+",
                     open(os.path.join(ROOT, "downloader.py"),
                          encoding="utf-8").read()):
        print("FAIL: downloader has no image fetch pool at all")
        return 1

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    port = srv.server_address[1]
    for i in range(N):
        PHOTOS["/photo_%d.jpg" % i] = _photo(i)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    urls = ["http://127.0.0.1:%d/photo_%d.jpg" % (port, i) for i in range(N)]

    import downloader                              # noqa: F401 (imported for path)
    tmp = tempfile.mkdtemp(prefix="gate_photos_")
    try:
        p_parallel, kept_p, dropped_p = outcome(None, tmp, urls)     # as shipped
        Slow.hits.clear()
        p_serial, kept_s, dropped_s = outcome(1, tmp, urls)
        seen = len(Slow.hits)
    finally:
        srv.shutdown()

    print("pooled : %.2f s for %d photos" % (p_parallel, N))
    print("serial : %.2f s for %d photos (server saw %d requests)" % (p_serial, N, seen))
    if seen != N:
        print("FAIL: the server answered %d of %d requests" % (seen, N))
        return 1
    if p_serial < N * DELAY * 0.7:
        print("FAIL: the serial control ran in %.2f s, so this harness is not "
              "measuring latency-bound downloads" % p_serial)
        return 1
    if p_parallel <= 0 or p_serial / p_parallel < 3.0:
        print("FAIL: only %.1fx faster than one request at a time"
              % (p_serial / max(p_parallel, 1e-6)))
        return 1
    if len(kept_p) != N or len(kept_s) != N:
        print("FAIL: pooled kept %d and serial kept %d of %d photos — this harness "
              "is serving something the pipeline drops, so the comparison below "
              "would be comparing nothing" % (len(kept_p), len(kept_s), N))
        return 1
    if [os.path.basename(k) for k in kept_p] != [os.path.basename(k) for k in kept_s]:
        print("FAIL: the pool kept a different set of photos: %s vs %s"
              % (kept_p, kept_s))
        return 1
    if sorted(dropped_p) != sorted(dropped_s):
        print("FAIL: the pool dropped different photos: %s vs %s"
              % (dropped_p, dropped_s))
        return 1
    print("both runs kept %d and dropped %d, in the same order" % (len(kept_p), len(dropped_p)))
    print("photo parallelism verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
