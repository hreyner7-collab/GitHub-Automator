"""Part 4: train a lightweight mask-refinement network.

Input: 4ch = RGB (1600->512 train crop, full 512 eval) + the pipeline's raw
union mask (before binarize) as a prior channel. Output: corrected binary
mask. Loss: BCE + soft-Dice. Metrics per epoch on val, SPLIT BY TAG
(thin_wire_present / multi_object_scene / low_contrast_background) so the
failing categories can't hide in an aggregate.

Baseline for the before/after table: the SAME architecture input pipeline but
with the prior channel passed through (identity refinement) — i.e. what the
current production union mask achieves. The network must beat that to count.

Usage:
  python scripts/train_refine.py train [--epochs 40]
  python scripts/train_refine.py eval          # before/after table on test
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
ROOT = os.path.join(BASE, "dataset")
CKPT_DIR = os.path.join(BASE, "checkpoints")
CKPT = os.path.join(CKPT_DIR, "segrefine_v1.pt")
TRAIN_SIDE = 512
DEV = "cuda" if torch.cuda.is_available() else "cpu"


# ── data ──────────────────────────────────────────────────────────────────────

def union_prior(img_small):
    """The production pipeline's raw prior: union of isnet+u2net soft masks.
    Uses editor's cached sessions; returns float [0,1] at img_small size."""
    import editor
    from rembg import remove
    from PIL import Image
    rgb = cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    out = None
    for model in ("isnet-general-use", "u2net"):
        try:
            if editor._rembg_sessions.get(model) is None:
                from rembg import new_session
                editor._rembg_sessions[model] = new_session(model)
            m = np.array(remove(pil, session=editor._rembg_sessions[model], only_mask=True))
            if m.ndim == 3:
                m = m[:, :, 0]
            if m.shape[:2] != img_small.shape[:2]:
                m = cv2.resize(m, (img_small.shape[1], img_small.shape[0]))
            out = m if out is None else np.maximum(out, m)
        except Exception:
            pass
    return (out.astype(np.float32) / 255.0) if out is not None else np.zeros(img_small.shape[:2], np.float32)


class DS(torch.utils.data.Dataset):
    def __init__(self, split, cache_dir=None):
        self.items = sorted(glob.glob(os.path.join(ROOT, split, "*_image.png")))
        self.priors = {}
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        self.cache_dir = cache_dir

    def __len__(self):
        return len(self.items)

    def _prior_path(self, img_path):
        return os.path.join(self.cache_dir, os.path.basename(img_path).replace("_image.png", "_prior.npy")) \
            if self.cache_dir else None

    def __getitem__(self, i):
        img_path = self.items[i]
        stem = os.path.basename(img_path)[:-len("_image.png")]
        img = cv2.imread(img_path)
        mask = cv2.imread(img_path.replace("_image.png", "_mask_gt.png"), 0)
        pp = self._prior_path(img_path)
        if pp and os.path.exists(pp):
            prior = np.load(pp)
        else:
            prior = union_prior(cv2.resize(img, (512, 512), interpolation=cv2.INTER_AREA))
            if pp:
                np.save(pp, prior)
        tags = json.load(open(img_path.replace("_image.png", "_category_tags.json")))

        # fixed eval geometry; RANDOM crop+flip per draw for train (the old
        # per-index seeding meant the same 8% empty crops every epoch and no
        # augmentation diversity)
        if "train" in img_path and os.environ.get("REFINE_EVAL_GEOM") != "1":
            rng = np.random.default_rng()               # fresh each draw
            y = rng.integers(0, 1600 - TRAIN_SIDE + 1)
            x = rng.integers(0, 1600 - TRAIN_SIDE + 1)
            sl = (slice(int(y), int(y + TRAIN_SIDE)), slice(int(x), int(x + TRAIN_SIDE)))
            img_s = img[sl]
            mask_s = mask[sl]
            prior_s = cv2.resize(prior, (1600, 1600))[sl]
            if rng.random() < 0.5:
                img_s, mask_s, prior_s = (np.ascontiguousarray(v[:, ::-1]) for v in (img_s, mask_s, prior_s))
        else:
            img_s = cv2.resize(img, (TRAIN_SIDE, TRAIN_SIDE))
            mask_s = cv2.resize(mask, (TRAIN_SIDE, TRAIN_SIDE), interpolation=cv2.INTER_NEAREST)
            prior_s = cv2.resize(prior, (TRAIN_SIDE, TRAIN_SIDE))

        x_img = cv2.cvtColor(img_s, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        prior_s = np.clip(prior_s.astype(np.float32), 0, 1)
        tmask = (mask_s > 127).astype(np.float32)
        ttags = torch.tensor([float(tags.get("thin_wire_present", False)),
                              float(tags.get("multi_object_scene", False)),
                              float(tags.get("low_contrast_background", False))])
        return (torch.from_numpy(np.ascontiguousarray(x_img.transpose(2, 0, 1))),
                torch.from_numpy(prior_s[None]),
                torch.from_numpy(tmask[None]),
                ttags, stem)


# ── model ─────────────────────────────────────────────────────────────────────

class RefineNet(nn.Module):
    """Small U-Net: in 4ch (RGB + prior) -> out 1ch logit. ~0.6M params."""
    def __init__(self):
        super().__init__()

        def blk(ci, co):
            return nn.Sequential(nn.Conv2d(ci, co, 3, padding=1), nn.ReLU(inplace=True),
                                 nn.Conv2d(co, co, 3, padding=1), nn.ReLU(inplace=True))
        self.d1 = blk(4, 32)
        self.d2 = blk(32, 64)
        self.d3 = blk(64, 128)
        self.u2 = blk(128 + 64, 64)
        self.u1 = blk(64 + 32, 32)
        self.out = nn.Conv2d(32, 1, 1)
        self.pool = nn.MaxPool2d(2)

    def forward(self, x):
        x1 = self.d1(x)
        x2 = self.d2(self.pool(x1))
        x3 = self.d3(self.pool(x2))
        y2 = F.interpolate(self.u2(torch.cat([F.interpolate(x3, scale_factor=2, mode="bilinear", align_corners=False), x2], 1)),
                           scale_factor=2, mode="bilinear", align_corners=False)
        y1 = self.u1(torch.cat([y2, x1], 1))
        return self.out(y1)


# ── metrics ───────────────────────────────────────────────────────────────────

def iou_t(logits, target):
    pred = (torch.sigmoid(logits) > 0.5)
    inter = (pred & (target > 0.5)).sum(dim=(1, 2, 3)).float()
    union = (pred | (target > 0.5)).sum(dim=(1, 2, 3)).float()
    return (inter / union.clamp(min=1))


@torch.no_grad()
def evaluate(net, ds, tag_i=None, max_n=None):
    """Mean IoU over the split; tag_i selects examples WITH that tag."""
    net.eval()
    vals = []
    items = ds.items if max_n is None else ds.items[:max_n]
    for i in range(len(items)):
        img_t, prior_t, mask_t, tags, _stem = ds[i]
        if tag_i is not None and tags[tag_i] == 0:
            continue
        with torch.amp.autocast(DEV, enabled=(DEV == "cuda")):
            logit = net(torch.cat([img_t[None].to(DEV), prior_t[None].to(DEV)], 1))
        vals.append(float(iou_t(logit.float(), mask_t[None].to(DEV))))
    return float(np.mean(vals)) if vals else float("nan")


# ── train / eval loops ────────────────────────────────────────────────────────

def dice_loss(logits, target, eps=1.0):
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    card = p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3))
    return 1 - ((2 * inter + eps) / (card + eps)).mean()


def run_train(epochs=40):
    os.makedirs(CKPT_DIR, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "prior_cache"), exist_ok=True)
    train_ds = DS("train", cache_dir=os.path.join(ROOT, "prior_cache"))
    val_ds = DS("val", cache_dir=os.path.join(ROOT, "prior_cache"))
    net = RefineNet().to(DEV)
    opt = torch.optim.AdamW(net.parameters(), lr=3e-4, weight_decay=1e-4)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    scaler = torch.amp.GradScaler(enabled=(DEV == "cuda"))
    # resume support: checkpoint EVERY epoch so an app restart costs one
    # epoch, not the whole run
    last_path = os.path.join(CKPT_DIR, "last.pt")
    log = []
    start_ep = 0
    if os.path.exists(last_path):
        try:
            last = torch.load(last_path, map_location=DEV, weights_only=False)
            net.load_state_dict(last["state_dict"])
            opt.load_state_dict(last["opt"])
            sch.load_state_dict(last["sch"])
            scaler.load_state_dict(last["scaler"])
            start_ep = last["epoch"]
            with open(os.path.join(CKPT_DIR, "train_log.json")) as f:
                log = json.load(f)[:start_ep]
            print(f"resumed from epoch {start_ep}", flush=True)
        except Exception as e:
            print(f"resume failed ({e}) — starting fresh", flush=True)
    # ~7% of pixels are product: plain BCE is dominated by the background.
    # pos_weight lifts the positive term so the net can't win by saying "no".
    pos_w = torch.tensor([14.0], device=DEV)
    bs = 8
    print(f"training on {DEV}: {len(train_ds)} train / {len(val_ds)} val, "
          f"{sum(p.numel() for p in net.parameters())/1e6:.2f}M params", flush=True)
    for ep in range(start_ep, epochs):
        net.train()
        perm = torch.randperm(len(train_ds)).tolist()
        tot_loss = 0.0
        for b0 in range(0, len(perm), bs):
            idx = perm[b0:b0 + bs]
            batch = [train_ds[i] for i in idx]
            x = torch.stack([b[0] for b in batch]).to(DEV)
            pr = torch.stack([b[1] for b in batch]).to(DEV)
            y = torch.stack([b[2] for b in batch]).to(DEV)
            with torch.amp.autocast(DEV, enabled=(DEV == "cuda")):
                logit = net(torch.cat([x, pr], 1))
                bce = F.binary_cross_entropy_with_logits(logit, y, pos_weight=pos_w)
                loss = bce + 2.0 * dice_loss(logit, y)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            tot_loss += float(loss) * len(idx)
        sch.step()
        RE = os.environ.copy()
        RE["REFINE_EVAL_GEOM"] = "1"
        row = {"epoch": ep + 1, "loss": round(tot_loss / len(train_ds), 4),
               "val_all": round(evaluate(net, val_ds), 4),
               "val_thin_wire": round(evaluate(net, val_ds, tag_i=0), 4),
               "val_multi_object": round(evaluate(net, val_ds, tag_i=1), 4),
               "val_low_contrast": round(evaluate(net, val_ds, tag_i=2), 4)}
        log.append(row)
        print(row, flush=True)
        with open(os.path.join(CKPT_DIR, "train_log.json"), "w") as f:
            json.dump(log, f, indent=1)
        torch.save({"epoch": ep + 1, "state_dict": net.state_dict(),
                    "opt": opt.state_dict(), "sch": sch.state_dict(),
                    "scaler": scaler.state_dict()}, last_path)
    torch.save({"state_dict": net.state_dict(), "config": {
        "arch": "refinenet_v1", "train_side": TRAIN_SIDE,
        "in_channels": 4, "epochs": epochs, "loss": "bce(pos_w=14)+2*dice",
        "torch": torch.__version__}},
        CKPT)
    print(f"checkpoint -> {CKPT}", flush=True)


@torch.no_grad()
def run_eval():
    """Before/after table on held-out test, per tag + per reference case."""
    net = RefineNet().to(DEV)
    ck = torch.load(CKPT, map_location=DEV, weights_only=False)
    net.load_state_dict(ck["state_dict"])
    net.eval()
    test_ds = DS("test", cache_dir=os.path.join(ROOT, "prior_cache"))

    def iou_baseline(prior, mask):
        p = prior > 0.5
        t = mask > 0.5
        u = (p | t).sum()
        return float((p & t).sum() / u) if u else 0.0

    rows = []
    per_tag = {0: [], 1: [], 2: []}
    for i in range(len(test_ds)):
        img_t, prior_t, mask_t, tags, stem = test_ds[i]
        logit = net(torch.cat([img_t[None].to(DEV), prior_t[None].to(DEV)], 1))
        after = float(iou_t(logit.float(), mask_t[None].to(DEV)))
        prior_full = cv2.resize(prior_t[0].numpy(), (TRAIN_SIDE, TRAIN_SIDE))
        before = iou_baseline(prior_full, mask_t[0].numpy())
        rows.append((stem, before, after))
        for t in per_tag:
            if tags[t] > 0:
                per_tag[t].append((before, after))
    print(f"\n{'example':<26s} {'IoU before':>10s} {'IoU after':>10s} {'delta':>8s}")
    for stem, b, a in rows:
        print(f"{stem:<26s} {b:>10.3f} {a:>10.3f} {a-b:>+8.3f}")
    print("\nper-tag means (test):")
    print(f"{'tag':<24s} {'before':>8s} {'after':>8s} {'delta':>8s}")
    names = {0: "thin_wire_present", 1: "multi_object_scene", 2: "low_contrast_background"}
    for t, name in names.items():
        bs = [b for b, _ in per_tag[t]]
        as_ = [a for _, a in per_tag[t]]
        if bs:
            print(f"{name:<24s} {np.mean(bs):>8.3f} {np.mean(as_):>8.3f} {np.mean(as_)-np.mean(bs):>+8.3f}")
    allb = [b for _, b, _ in rows]
    alla = [a for _, _, a in rows]
    print(f"{'ALL test':<24s} {np.mean(allb):>8.3f} {np.mean(alla):>8.3f} {np.mean(alla)-np.mean(allb):>+8.3f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    if cmd == "train":
        ep = int(sys.argv[sys.argv.index("--epochs") + 1]) if "--epochs" in sys.argv else 40
        run_train(ep)
    elif cmd == "eval":
        run_eval()
