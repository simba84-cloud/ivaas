"""Zero-shot pre-labelling with an open-vocabulary detector (Grounding DINO, Apache-2.0).

    uv run --extra prelabel python -m ivaas_ml.prelabel data/frames data/prelabels.json

Output is a *starting point for a human annotator*, never ground truth: the model has
never seen these crates. The boxes are imported into Label Studio as predictions, where
the annotator accepts, fixes or deletes them.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np

MODEL_ID = "IDEA-Research/grounding-dino-tiny"
PROMPT = "a stack of plastic crates."


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of xyxy boxes."""
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def clean(
    boxes: np.ndarray,
    scores: np.ndarray,
    width: int,
    height: int,
    *,
    max_area_frac: float = 0.25,
    min_side_px: float = 40.0,
    nms_iou: float = 0.5,
    contain_frac: float = 0.8,
    min_aspect: float = 1.3,
) -> list[int]:
    """Indices of boxes worth showing an annotator.

    A single-column stack is tall and narrow. Open-vocabulary detectors also return boxes
    around a *group* of stacks, the whole yard, or a truck; those are worse than nothing as
    pre-labels, so drop:
      - boxes that are not taller than wide by `min_aspect` (groups, trucks),
      - boxes covering a large fraction of the frame,
      - slivers,
      - duplicates (NMS),
      - "group" boxes that mostly contain two or more smaller kept boxes.
    """
    if len(boxes) == 0:
        return []
    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]
    ok = (
        (w * h <= max_area_frac * width * height)
        & (w >= min_side_px)
        & (h >= min_side_px)
        & (h >= min_aspect * w)
    )
    idx = [i for i in np.argsort(-scores) if ok[i]]

    kept: list[int] = []
    for i in idx:  # greedy NMS, highest score first
        if not kept or iou_matrix(boxes[[i]], boxes[kept]).max() < nms_iou:
            kept.append(i)

    def contained_in(inner: int, outer: int) -> bool:
        ix = max(0.0, min(boxes[inner, 2], boxes[outer, 2]) - max(boxes[inner, 0], boxes[outer, 0]))
        iy = max(0.0, min(boxes[inner, 3], boxes[outer, 3]) - max(boxes[inner, 1], boxes[outer, 1]))
        return ix * iy >= contain_frac * w[inner] * h[inner]

    return [i for i in kept if sum(1 for j in kept if j != i and contained_in(j, i)) < 2]


class GroundingDino:
    def __init__(self, threshold: float, device: str | None = None) -> None:
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self._torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.processor = AutoProcessor.from_pretrained(MODEL_ID)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID).to(self.device)
        self.model.eval()
        self.threshold = threshold

    def detect(self, image) -> tuple[np.ndarray, np.ndarray]:
        inputs = self.processor(images=image, text=PROMPT, return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            outputs = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=self.threshold,
            text_threshold=self.threshold,
            target_sizes=[image.size[::-1]],
        )[0]
        return result["boxes"].cpu().numpy(), result["scores"].cpu().numpy()


def select_rows(manifest: Path, cameras: str | None) -> list[dict]:
    wanted = {c.strip() for c in cameras.split(",") if c.strip()} if cameras else None
    with open(manifest) as fh:
        return [r for r in csv.DictReader(fh) if wanted is None or r["camera"] in wanted]


def main() -> None:
    from PIL import Image

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("frames", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--threshold", type=float, default=0.30)
    ap.add_argument("--cameras", help="comma-separated, e.g. cc2,cc3,cc4")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    rows = select_rows(args.frames / "manifest.csv", args.cameras)
    rows = rows[: args.limit] if args.limit else rows

    model = GroundingDino(args.threshold)
    print(f"{MODEL_ID} on {model.device}; {len(rows)} frames")
    out: dict[str, dict] = {}
    started = time.time()
    for n, row in enumerate(rows, start=1):
        image = Image.open(args.frames / row["file"]).convert("RGB")
        boxes, scores = model.detect(image)
        keep = clean(boxes, scores, *image.size)
        out[row["file"]] = {
            "width": image.width,
            "height": image.height,
            "model": f"{MODEL_ID}@{args.threshold}",
            "boxes": [
                {
                    "label": "stack",
                    "x": float(boxes[i, 0]),
                    "y": float(boxes[i, 1]),
                    "w": float(boxes[i, 2] - boxes[i, 0]),
                    "h": float(boxes[i, 3] - boxes[i, 1]),
                    "score": round(float(scores[i]), 3),
                }
                for i in keep
            ],
        }
        if n % 20 == 0 or n == len(rows):
            rate = (time.time() - started) / n
            print(
                f"  {n}/{len(rows)}  {rate:.1f}s/frame  raw={len(boxes)} kept={len(keep)}",
                flush=True,
            )
    args.out.write_text(json.dumps(out))
    total = sum(len(v["boxes"]) for v in out.values())
    print(f"{total} boxes over {len(out)} frames -> {args.out}")


if __name__ == "__main__":
    main()
