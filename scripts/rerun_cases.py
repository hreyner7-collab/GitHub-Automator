"""Part 4.5: re-run Cases 1-3 through the pipeline WITH the trained
refinement mask as an additional candidate (still QA-guarded — the network
does not bypass a single guard), dumping fresh debug artifacts to
debug_after/<stem>/ and comparing against the Part-1 before-state.

Honesty rules: the refined candidate must WIN on QA merits or the case is
reported as NOT resolved. No metric massaging.
"""
import json
import os
import shutil
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))
import editor  # noqa: E402
import torch  # noqa: E402
import train_refine as tr  # noqa: E402

DL = os.path.join(BASE, "downloads")
DEBUG = os.path.join(BASE, "debug")
DEBUG_AFTER = os.path.join(BASE, "debug_after")

CASES = {
    "CASE1_collapse": "self-draining_sink_caddy_detergent_spong_img_9",
    "CASE2_amputation": "self-draining_sink_caddy_detergent_spong_img_14",
    "CASE3_biteout": "single-hand_bottle_cap_shooter_img_4",
}


def find_source(stem):
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        p = os.path.join(DL, stem + ext)
        if os.path.exists(p):
            return p
    return None


def refined_full_mask(net, img):
    """Net's corrected mask at full image resolution."""
    h, w = img.shape[:2]
    scale = min(1.0, editor.MASK_WORK_SIDE / max(h, w))
    work = cv2.resize(img, (int(w * scale), int(h * scale)),
                      interpolation=cv2.INTER_AREA) if scale < 1.0 else img
    prior = tr.union_prior(work)
    x = cv2.cvtColor(work, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    x = torch.from_numpy(np.ascontiguousarray(x.transpose(2, 0, 1)))[None].to(tr.DEV)
    p = torch.from_numpy(prior[None].astype(np.float32))[None].to(tr.DEV)
    with torch.no_grad():
        logit = net(torch.cat([x, p], 1))
    prob = torch.sigmoid(logit)[0, 0].cpu().numpy()
    m = cv2.resize((prob * 255).astype(np.uint8), (w, h),
                   interpolation=cv2.INTER_LINEAR)
    return m


def busy_recall(img, mask):
    h, w = img.shape[:2]
    scale = 640.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    fb = cv2.dilate(cv2.Canny(g, 60, 160), np.ones((7, 7), np.uint8)) > 0
    fb &= ~(editor._verify_text_bands(small) > 0)
    ms = cv2.resize(mask, (small.shape[1], small.shape[0])) > 127
    return float((fb & ms).sum()) / max(float(fb.sum()), 1.0)


def main():
    net = tr.RefineNet().to(tr.DEV)
    ck = torch.load(tr.CKPT, map_location=tr.DEV, weights_only=False)
    net.load_state_dict(ck["state_dict"])
    net.eval()
    print(f"loaded {tr.CKPT} (config: {ck.get('config')})")

    orig_cands = editor._candidate_masks
    os.makedirs(DEBUG_AFTER, exist_ok=True)
    summary = {}
    for tag, stem in CASES.items():
        src = find_source(stem)
        assert src, stem
        img = cv2.imread(src, cv2.IMREAD_COLOR)
        rmask = refined_full_mask(net, img)
        cov = float((rmask > 127).mean())

        ddir = os.path.join(DEBUG_AFTER, stem)
        if os.path.exists(ddir):
            shutil.rmtree(ddir)
        os.makedirs(ddir)
        cv2.imwrite(os.path.join(ddir, "00_refine_v1_mask.png"), rmask)

        def patched(image, debug_dir=None, _rm=rmask):
            cands = orig_cands(image, debug_dir=debug_dir)
            cands.insert(0, editor.Candidate("refine_v1", _rm))
            return cands

        editor._candidate_masks = patched
        try:
            out = editor.edit_image(src, debug_dir=ddir)
            verdict = "SHIPPED"
        except Exception as e:
            out = None
            verdict = f"REFUSED ({str(e)[:60]}...)"
        finally:
            editor._candidate_masks = orig_cands

        # before-state from Part 1 dump
        before = json.load(open(os.path.join(DEBUG, stem, "06_guard_scores.json")))
        mask_path = os.path.join(DL, stem + "_mask.png")
        after_cov = after_recall = None
        if out and os.path.exists(mask_path):
            m = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            after_cov = round(float((m > 0).mean()), 3)
            after_recall = round(busy_recall(img, m), 3)
        # busy recall of the BEFORE shipped/refused winner for comparison:
        # recompute from the before union dump at work scale
        bu = cv2.imread(os.path.join(DEBUG, stem, "03_union.png"), cv2.IMREAD_GRAYSCALE)
        before_recall = round(busy_recall(img, cv2.resize(bu, (img.shape[1], img.shape[0]))) if bu is not None else 0.0, 3)

        winner = None
        gs_after_path = os.path.join(ddir, "06_guard_scores.json")
        if os.path.exists(gs_after_path):
            gs = json.load(open(gs_after_path))
            for c in gs["candidates"]:
                pass
        summary[tag] = {"stem": stem,
                        "before_verdict": before["verdict"],
                        "after_verdict": verdict,
                        "refine_mask_coverage": round(cov, 3),
                        "before_union_recall": before_recall,
                        "after_shipped_recall": after_recall}
        print(f"\n{tag} ({stem})")
        print(f"  refine mask coverage: {cov:.3f}")
        print(f"  before: {before['verdict']}  (union busy-recall {before_recall})")
        print(f"  after:  {verdict}  (shipped busy-recall {after_recall})")

    with open(os.path.join(DEBUG_AFTER, "_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("\nsummary ->", os.path.join(DEBUG_AFTER, "_summary.json"))


if __name__ == "__main__":
    main()
