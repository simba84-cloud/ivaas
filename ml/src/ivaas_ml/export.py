"""Export a fine-tuned RT-DETR checkpoint to ONNX and prove the ONNX graph agrees with torch.

    uv run --extra train python -m ivaas_ml.export runs/stacks/best models/stacks.onnx

The checkpoint may also be a Hugging Face model id. The security pipeline's person
detector is the pretrained COCO RT-DETR (Apache-2.0), exported unchanged:

    uv run --extra train python -m ivaas_ml.export PekingU/rtdetr_r18vd_coco_o365 \
        ../models/people-coco.onnx

The ONNX file takes `pixel_values` (N,3,H,W) in [0,1] and returns `logits` (N,Q,C) and
`pred_boxes` (N,Q,4) as normalised cx,cy,w,h: exactly what the pipeline's detector
adapter expects (services/pipeline/.../adapters/onnx_rtdetr.py). Class names and the
input size are written next to it as <name>.json so the adapter needs no other config.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch


class _Wrapper(torch.nn.Module):
    """Only the two tensors the adapter needs, in a fixed order."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor):
        out = self.model(pixel_values=pixel_values)
        return out.logits, out.pred_boxes


def detection_error(ref_logits, ref_boxes, logits, boxes, *, top: int = 100) -> float:
    """How far apart the two runs' *detections* are: each run's queries ranked by
    score, the best `top` compared as probabilities and boxes.

    Raw per-query tensors are the wrong thing to compare. RT-DETR picks its queries by
    top-k, so a difference in the fifth decimal reorders near-tied low-score queries,
    and a many-class model (COCO) then shows raw differences near 1 while producing
    exactly the same detections. Ranking first compares like with like; a genuinely
    broken export still fails, because its best detections move.
    """

    def ranked(lg, bx):
        p = 1 / (1 + np.exp(-lg[0]))
        order = np.argsort(-p.max(-1), kind="stable")[:top]
        return p[order], bx[0][order]

    p1, b1 = ranked(ref_logits, ref_boxes)
    p2, b2 = ranked(logits, boxes)
    return max(float(np.abs(p1 - p2).max()), float(np.abs(b1 - b2).max()))


def export(checkpoint: str | Path, out: Path, *, opset: int = 17) -> dict:
    checkpoint = str(checkpoint)  # a local directory or a Hugging Face model id
    from transformers import AutoImageProcessor, RTDetrForObjectDetection

    processor = AutoImageProcessor.from_pretrained(checkpoint)
    model = RTDetrForObjectDetection.from_pretrained(checkpoint).eval()
    size = processor.size
    h, w = int(size["height"]), int(size["width"])
    dummy = torch.rand(1, 3, h, w)
    wrapper = _Wrapper(model).eval()
    # Reference BEFORE export: the exporter restores the wrapper's mode on exit, which
    # would put BatchNorm/dropout back into training mode for a later forward pass.
    with torch.no_grad():
        ref_logits, ref_boxes = wrapper(dummy)

    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        wrapper,
        (dummy,),
        str(out),
        input_names=["pixel_values"],
        output_names=["logits", "pred_boxes"],
        dynamic_axes={
            "pixel_values": {0: "batch"},
            "logits": {0: "batch"},
            "pred_boxes": {0: "batch"},
        },
        opset_version=opset,
        dynamo=False,
    )

    import onnxruntime as ort

    sess = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    logits, boxes = sess.run(None, {"pixel_values": dummy.numpy()})
    max_err = detection_error(ref_logits.numpy(), ref_boxes.numpy(), logits, boxes)
    if max_err > 1e-3:
        raise RuntimeError(f"ONNX detections differ from torch by {max_err:.4f}")

    meta = {
        "labels": [model.config.id2label[i] for i in range(model.config.num_labels)],
        "input_height": h,
        "input_width": w,
        "source": str(checkpoint),
        "max_abs_error_vs_torch": max_err,
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("checkpoint", help="checkpoint directory, or a Hugging Face model id")
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    meta = export(args.checkpoint, args.out)
    print(f"{args.out} ({args.out.stat().st_size / 1e6:.1f} MB)  {meta}")


if __name__ == "__main__":
    main()
