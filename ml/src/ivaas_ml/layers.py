"""Learned layer counter: stack crop -> number of crates.

Labelling is one number per crop (Label Studio <Number>), which is far faster than
drawing. Training: a small pretrained CNN with a single regression output, trained on
the labelled crops, validated on crops from held-out clips. Export: ONNX, consumed by
services/pipeline/.../adapters/onnx_layers.py behind the LayerCounter port.

    uv run python -m ivaas_ml.layers tasks data/crops data/crop_tasks.json
    uv run --extra train python -m ivaas_ml.layers train data/crop_export.json data/crops runs/layers-v1
    uv run --extra train python -m ivaas_ml.layers export runs/layers-v1/best.pt ../models/layers-v1.onnx
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

LABEL_CONFIG = """<View>
  <Header value="How many crates are in this stack? Count the layers. Enter 0 if this is not a single stack (two columns, loose crates, not a stack). Skip if you cannot tell."/>
  <Image name="image" value="$image" zoom="true" zoomControl="true"/>
  <Number name="layers" toName="image" min="0" max="40" required="true" hotkey="enter"/>
</View>"""

LOCAL_FILES_PREFIX = "/data/local-files/?d=crops/"
INPUT_SIZE = (128, 384)  # width, height: stacks are tall and narrow
MAX_LAYERS = 40


def build_tasks(crops_dir: Path) -> list[dict]:
    tasks = []
    with open(crops_dir / "manifest.csv") as fh:
        for row in csv.DictReader(fh):
            tasks.append(
                {
                    "data": {
                        "image": LOCAL_FILES_PREFIX + row["file"],
                        "file": row["file"],
                        "camera": row["camera"],
                        "clip": row["clip"],
                    }
                }
            )
    return tasks


def read_export(export: list[dict]) -> list[dict]:
    """-> [{file, clip, camera, layers}] for every task with a submitted count."""
    items = []
    for task in export:
        done = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
        if not done:
            continue
        latest = max(done, key=lambda a: a.get("updated_at") or "")
        value = None
        for r in latest.get("result", []):
            if r.get("type") == "number":
                value = r["value"].get("number")
        if value is None:
            continue
        d = task["data"]
        items.append(
            {
                "file": d["file"],
                "clip": d.get("clip", ""),
                "camera": d.get("camera", ""),
                "layers": int(value),
            }
        )
    return items


def split_by_clip(items: list[dict], val_fraction: float = 0.25) -> tuple[list, list]:
    clips = sorted({i["clip"] for i in items})
    random.Random(0).shuffle(clips)
    n_val = max(1, round(len(clips) * val_fraction)) if len(clips) > 1 else 0
    val_clips = set(clips[:n_val])
    return [i for i in items if i["clip"] not in val_clips], [
        i for i in items if i["clip"] in val_clips
    ]


# --- training ------------------------------------------------------------------


def _model():
    import torch
    import torchvision

    m = torchvision.models.mobilenet_v3_small(weights="IMAGENET1K_V1")
    m.classifier[-1] = torch.nn.Linear(m.classifier[-1].in_features, 1)
    return m


def _load(path: Path, augment: bool):
    import cv2
    import numpy as np

    img = cv2.imread(str(path))
    if augment and random.random() < 0.5:
        img = img[:, ::-1]
    img = cv2.resize(img, INPUT_SIZE, interpolation=cv2.INTER_AREA)
    x = img[:, :, ::-1].astype(np.float32) / 255.0
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    return np.ascontiguousarray(x.transpose(2, 0, 1), dtype=np.float32)


def train(export_path: Path, crops: Path, out: Path, *, epochs: int = 40, lr: float = 3e-4) -> dict:
    import numpy as np
    import torch

    items = [i for i in read_export(json.loads(export_path.read_text())) if i["layers"] > 0]
    tr, va = split_by_clip(items)
    print(
        f"{len(tr)} train / {len(va)} val crops, layers {min(i['layers'] for i in items)}..{max(i['layers'] for i in items)}"
    )
    device = (
        "mps"
        if torch.backends.mps.is_available()
        else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    model = _model().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    out.mkdir(parents=True, exist_ok=True)
    best, history = 1e9, []

    def batches(data, bs, augment):
        idx = list(range(len(data)))
        if augment:
            random.shuffle(idx)
        for i in range(0, len(idx), bs):
            chunk = [data[j] for j in idx[i : i + bs]]
            x = torch.tensor(np.stack([_load(crops / c["file"], augment) for c in chunk])).to(
                device
            )
            y = torch.tensor([[float(c["layers"])] for c in chunk]).to(device)
            yield x, y

    for epoch in range(1, epochs + 1):
        model.train()
        for x, y in batches(tr, 16, True):
            loss = torch.nn.functional.smooth_l1_loss(model(x), y)
            opt.zero_grad()
            loss.backward()
            opt.step()
        sched.step()
        model.eval()
        errs = []
        with torch.no_grad():
            for x, y in batches(va, 32, False):
                errs += (model(x) - y).abs().flatten().tolist()
        mae = float(np.mean(errs)) if errs else float("nan")
        within1 = float(np.mean([e <= 1.0 for e in errs])) if errs else float("nan")
        history.append({"epoch": epoch, "val_mae": mae, "val_within_1": within1})
        print(f"epoch {epoch:3d}  val MAE {mae:.2f}  within ±1: {within1:.0%}", flush=True)
        if errs and mae < best:
            best = mae
            torch.save(model.state_dict(), out / "best.pt")
    (out / "history.json").write_text(json.dumps(history, indent=1))
    return {"best_val_mae": best, "train": len(tr), "val": len(va)}


def export(weights: Path, out: Path) -> dict:
    import numpy as np
    import onnxruntime as ort
    import torch

    model = _model()
    model.load_state_dict(torch.load(weights, map_location="cpu"))
    model.eval()
    w, h = INPUT_SIZE
    dummy = torch.rand(1, 3, h, w)
    with torch.no_grad():
        ref = model(dummy).numpy()
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        (dummy,),
        str(out),
        input_names=["crop"],
        output_names=["layers"],
        dynamic_axes={"crop": {0: "batch"}, "layers": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    got = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"]).run(
        None, {"crop": dummy.numpy()}
    )[0]
    err = float(np.abs(got - ref).max())
    if err > 1e-3:
        raise RuntimeError(f"ONNX differs from torch by {err}")
    meta = {
        "input_width": w,
        "input_height": h,
        "max_layers": MAX_LAYERS,
        "max_abs_error_vs_torch": err,
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config")
    t = sub.add_parser("tasks")
    t.add_argument("crops", type=Path)
    t.add_argument("out", type=Path)
    tr = sub.add_parser("train")
    tr.add_argument("export", type=Path)
    tr.add_argument("crops", type=Path)
    tr.add_argument("out", type=Path)
    tr.add_argument("--epochs", type=int, default=40)
    ex = sub.add_parser("export")
    ex.add_argument("weights", type=Path)
    ex.add_argument("out", type=Path)
    a = ap.parse_args()
    if a.cmd == "config":
        print(LABEL_CONFIG)
    elif a.cmd == "tasks":
        tasks = build_tasks(a.crops)
        a.out.write_text(json.dumps(tasks))
        print(f"{len(tasks)} tasks -> {a.out}")
    elif a.cmd == "train":
        print(train(a.export, a.crops, a.out, epochs=a.epochs))
    elif a.cmd == "export":
        print(export(a.weights, a.out))


if __name__ == "__main__":
    main()
