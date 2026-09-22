"""Fine-tune RT-DETR (Apache-2.0) on the stack dataset, then report AP@0.5 on validation.

    uv run --extra train python -m ivaas_ml.train data/dataset runs/stacks --epochs 20

The dataset is the output of `ivaas_ml.dataset` (COCO json per split). Validation is
split by clip, so the number reported here is what to expect on unseen footage from the
same cameras, not on near-duplicates of the training frames.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from ivaas_ml.labelstudio import LABELS
from ivaas_ml.metrics import Detections, evaluate

DEFAULT_CHECKPOINT = "PekingU/rtdetr_r18vd"  # smallest RT-DETR; r50vd for the edge GPU


def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class CocoSplit(torch.utils.data.Dataset):
    def __init__(self, split_dir: Path, *, augment: bool) -> None:
        coco = json.loads((split_dir / "_annotations.coco.json").read_text())
        self.dir = split_dir
        self.images = coco["images"]
        self.anns: dict[int, list[dict]] = {}
        for a in coco["annotations"]:
            self.anns.setdefault(a["image_id"], []).append(a)
        self.augment = augment

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, i: int) -> tuple[Image.Image, dict]:
        info = self.images[i]
        image = Image.open(self.dir / info["file_name"]).convert("RGB")
        anns = [dict(a) for a in self.anns.get(info["id"], [])]
        if self.augment and random.random() < 0.5:  # horizontal flip: stacks are symmetric
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            for a in anns:
                x, y, w, h = a["bbox"]
                a["bbox"] = [info["width"] - x - w, y, w, h]
        return image, {"image_id": info["id"], "annotations": anns}


def load_model(checkpoint: str, device: str):
    from transformers import AutoImageProcessor, RTDetrForObjectDetection

    processor = AutoImageProcessor.from_pretrained(checkpoint)
    model = RTDetrForObjectDetection.from_pretrained(
        checkpoint,
        num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)),
        label2id={n: i for i, n in enumerate(LABELS)},
        ignore_mismatched_sizes=True,  # new class head
    )
    return processor, model.to(device)


def collate(processor, batch, device):
    images, targets = zip(*batch, strict=True)
    enc = processor(images=list(images), annotations=list(targets), return_tensors="pt")
    labels = [{k: v.to(device) for k, v in t.items()} for t in enc["labels"]]
    return enc["pixel_values"].to(device), labels


@torch.no_grad()
def predict(processor, model, images: list[Image.Image], device: str, threshold: float):
    enc = processor(images=images, return_tensors="pt").to(device)
    out = model(**enc)
    sizes = torch.tensor([im.size[::-1] for im in images])
    results = processor.post_process_object_detection(out, threshold=threshold, target_sizes=sizes)
    return [
        Detections(r["boxes"].cpu().numpy().reshape(-1, 4), r["scores"].cpu().numpy())
        for r in results
    ]


def run_eval(processor, model, split: CocoSplit, device: str, *, threshold: float = 0.5):
    model.eval()
    preds, truths = [], []
    for i in range(0, len(split), 8):
        items = [split[j] for j in range(i, min(i + 8, len(split)))]
        preds += predict(processor, model, [im for im, _ in items], device, threshold=0.01)
        for _, t in items:
            xyxy = [[x, y, x + w, y + h] for x, y, w, h in (a["bbox"] for a in t["annotations"])]
            truths.append(np.array(xyxy, dtype=float).reshape(-1, 4))
    return evaluate(preds, truths, threshold=threshold)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--device", default=pick_device())
    args = ap.parse_args()

    random.seed(0)
    torch.manual_seed(0)
    processor, model = load_model(args.checkpoint, args.device)
    train = CocoSplit(args.dataset / "train", augment=True)
    val = CocoSplit(args.dataset / "val", augment=False)
    print(f"{args.checkpoint} on {args.device}: {len(train)} train / {len(val)} val images")

    loader = torch.utils.data.DataLoader(
        train,
        batch_size=args.batch,
        shuffle=True,
        collate_fn=lambda b: collate(processor, b, args.device),
    )
    optim = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(optim, T_max=args.epochs * len(loader))

    args.out.mkdir(parents=True, exist_ok=True)
    best, history = -1.0, []
    for epoch in range(1, args.epochs + 1):
        model.train()
        started, total = time.time(), 0.0
        for pixel_values, labels in loader:
            loss = model(pixel_values=pixel_values, labels=labels).loss
            optim.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.1)
            optim.step()
            sched.step()
            total += float(loss)
        ev = run_eval(processor, model, val, args.device)
        history.append({"epoch": epoch, "loss": total / len(loader), **ev.__dict__})
        print(
            f"epoch {epoch:3d}  loss {total / len(loader):.3f}  AP50 {ev.ap50:.3f}  "
            f"P {ev.precision:.2f} R {ev.recall:.2f} @{ev.threshold}  ({time.time() - started:.0f}s)",
            flush=True,
        )
        if ev.ap50 > best:
            best = ev.ap50
            model.save_pretrained(args.out / "best")
            processor.save_pretrained(args.out / "best")
    (args.out / "history.json").write_text(json.dumps(history, indent=1))
    print(f"best AP50 {best:.3f} -> {args.out / 'best'}")


if __name__ == "__main__":
    main()
