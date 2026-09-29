"""Train and export the uniform / PPE classifier used by the security pipeline.

What counts as "in uniform" is particular to a site, so this is trained on crops of
this site's own people. Layout, one folder per class, split by *day or camera* so
near-identical crops never sit on both sides:

    data/uniform/train/ppe/*.jpg        data/uniform/val/ppe/*.jpg
    data/uniform/train/no_ppe/*.jpg     data/uniform/val/no_ppe/*.jpg

    uv run --extra train python -m ivaas_ml.train_classifier train data/uniform runs/uniform
    uv run --extra train python -m ivaas_ml.train_classifier export runs/uniform/best \\
        ../models/uniform.onnx

The compliant class must be named ppe, uniform or compliant: the pipeline treats every
other class as "not in uniform". Base model: MobileNetV2 (Apache-2.0), small enough to
run on every person crop. The ONNX graph takes pixels in [0,1] and normalises inside,
because the pipeline feeds raw pixels.

Report recall on the "not in uniform" class above all: a missed violation is invisible,
while a false one costs an operator a glance at the snapshot.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = "google/mobilenet_v2_1.0_224"
COMPLIANT = {"ppe", "uniform", "compliant"}


def train(data: Path, out: Path, *, epochs: int = 12, batch: int = 32, lr: float = 1e-4) -> None:
    import torch
    from torch.utils.data import DataLoader
    from torchvision import datasets
    from torchvision import transforms as T
    from transformers import AutoImageProcessor, AutoModelForImageClassification

    processor = AutoImageProcessor.from_pretrained(BASE)
    size = processor.crop_size if getattr(processor, "crop_size", None) else processor.size
    h, w = int(size["height"]), int(size["width"])
    mean, std = processor.image_mean, processor.image_std
    augment = T.Compose(
        [
            T.Resize((h, w)),
            T.RandomHorizontalFlip(),
            T.ColorJitter(0.3, 0.3, 0.2),  # night shifts, sodium lamps
            T.ToTensor(),
            T.Normalize(mean, std),
        ]
    )
    plain = T.Compose([T.Resize((h, w)), T.ToTensor(), T.Normalize(mean, std)])
    train_ds = datasets.ImageFolder(data / "train", augment)
    val_ds = datasets.ImageFolder(data / "val", plain)
    labels = train_ds.classes
    if not COMPLIANT & set(labels):
        raise SystemExit(f"no compliant class in {labels}: name it one of {sorted(COMPLIANT)}")
    if val_ds.classes != labels:
        raise SystemExit(f"train classes {labels} differ from val classes {val_ds.classes}")

    model = AutoModelForImageClassification.from_pretrained(
        BASE,
        num_labels=len(labels),
        id2label=dict(enumerate(labels)),
        label2id={name: i for i, name in enumerate(labels)},
        ignore_mismatched_sizes=True,
    )
    device = (
        "cuda"
        if torch.cuda.is_available()
        else "mps"
        if torch.backends.mps.is_available()
        else "cpu"
    )
    model.to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    loader = DataLoader(train_ds, batch_size=batch, shuffle=True)
    best = -1.0
    for epoch in range(1, epochs + 1):
        model.train()
        for x, y in loader:
            loss = model(pixel_values=x.to(device), labels=y.to(device)).loss
            opt.zero_grad()
            loss.backward()
            opt.step()
        recall = evaluate(model, val_ds, labels, device)
        worst = min(v for k, v in recall.items() if k not in COMPLIANT)
        print(f"epoch {epoch}: recall " + ", ".join(f"{k} {v:.2f}" for k, v in recall.items()))
        if worst > best:
            best = worst
            model.save_pretrained(out / "best")
            processor.save_pretrained(out / "best")
    print(f"best violation recall {best:.2f} -> {out / 'best'}")


def evaluate(model, ds, labels: list[str], device: str) -> dict[str, float]:
    import torch
    from torch.utils.data import DataLoader

    model.eval()
    hit = dict.fromkeys(labels, 0)
    seen = dict.fromkeys(labels, 0)
    with torch.no_grad():
        for x, y in DataLoader(ds, batch_size=64):
            pred = model(pixel_values=x.to(device)).logits.argmax(-1).cpu()
            for p, t in zip(pred.tolist(), y.tolist(), strict=True):
                seen[labels[t]] += 1
                hit[labels[t]] += int(p == t)
    return {k: hit[k] / seen[k] if seen[k] else 0.0 for k in labels}


def export(checkpoint: Path, out: Path, *, opset: int = 17) -> dict:
    import numpy as np
    import torch
    from transformers import AutoImageProcessor, AutoModelForImageClassification

    processor = AutoImageProcessor.from_pretrained(checkpoint)
    model = AutoModelForImageClassification.from_pretrained(checkpoint).eval()
    size = processor.crop_size if getattr(processor, "crop_size", None) else processor.size
    h, w = int(size["height"]), int(size["width"])
    mean = torch.tensor(processor.image_mean).view(1, 3, 1, 1)
    std = torch.tensor(processor.image_std).view(1, 3, 1, 1)

    class Wrapper(torch.nn.Module):
        """Raw pixels in [0,1] in, logits out: normalisation lives in the graph."""

        def __init__(self) -> None:
            super().__init__()
            self.model = model

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            return self.model(pixel_values=(pixel_values - mean) / std).logits

    wrapper = Wrapper().eval()
    dummy = torch.rand(1, 3, h, w)
    with torch.no_grad():
        ref = wrapper(dummy)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        wrapper,
        (dummy,),
        str(out),
        input_names=["pixel_values"],
        output_names=["logits"],
        dynamic_axes={"pixel_values": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=opset,
        dynamo=False,
    )
    import onnxruntime as ort

    (got,) = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"]).run(
        None, {"pixel_values": dummy.numpy()}
    )
    err = float(np.abs(got - ref.numpy()).max())
    if err > 1e-3:
        raise RuntimeError(f"ONNX output differs from torch by {err:.4f}")
    meta = {
        "labels": [model.config.id2label[i] for i in range(model.config.num_labels)],
        "input_height": h,
        "input_width": w,
        "source": str(checkpoint),
        "max_abs_error_vs_torch": err,
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("data", type=Path)
    t.add_argument("out", type=Path)
    t.add_argument("--epochs", type=int, default=12)
    e = sub.add_parser("export")
    e.add_argument("checkpoint", type=Path)
    e.add_argument("out", type=Path)
    args = ap.parse_args()
    if args.cmd == "train":
        train(args.data, args.out, epochs=args.epochs)
    else:
        print(export(args.checkpoint, args.out))


if __name__ == "__main__":
    main()
