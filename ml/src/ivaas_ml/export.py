"""Export a fine-tuned RT-DETR or RF-DETR checkpoint to ONNX and prove the ONNX graph agrees
with torch.

    uv run --extra train python -m ivaas_ml.export runs/stacks/best models/stacks.onnx

The checkpoint may also be a Hugging Face model id. The security pipeline's person
detector is the pretrained COCO RT-DETR (Apache-2.0), exported unchanged:

    uv run --extra train python -m ivaas_ml.export PekingU/rtdetr_r18vd_coco_o365 \
        ../models/people-coco.onnx

RF-DETR Base (COCO) the same way; its processor's 560x560 size is pinned at export:

    uv run --extra train python -m ivaas_ml.export Roboflow/rf-detr-base \
        ../models/people-rfdetr.onnx

The ONNX file takes `pixel_values` (N,3,H,W) in [0,1] and returns `logits` (N,Q,C) and
`pred_boxes` (N,Q,4) as normalised cx,cy,w,h: exactly what the pipeline's detector
adapter expects (services/pipeline/.../adapters/onnx_rtdetr.py). Class names and the
input size are written next to it as <name>.json so the adapter needs no other config.

RF-DETR's processor also normalises with ImageNet mean/std; RT-DETR's does not. Whatever
the processor does after rescaling is baked into the graph, so every export takes the
same raw [0,1] RGB and the adapter needs no per-architecture preprocessing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ARCH_BY_MODEL_TYPE = {"rt_detr": "rtdetr", "rf_detr": "rfdetr"}


def sidecar_labels(id2label: dict, num_labels: int) -> list[str]:
    """Class names in logit-index order, one per logit column.

    COCO checkpoints number their classes with gaps (RF-DETR's id2label has 91 entries,
    some "N/A", and "person" at 1, not 0). The adapter takes argmax over the logit columns
    and indexes this list, so it must keep every position: a missing id becomes "N/A"
    rather than shifting the names after it.
    """
    by_index = {int(k): v for k, v in id2label.items()}
    return [by_index.get(i, "N/A") for i in range(num_labels)]


def processor_normalisation(processor) -> tuple[list[float], list[float]] | None:
    """The mean/std the processor applies after rescaling to [0,1], or None if it does not."""
    if not getattr(processor, "do_normalize", False):
        return None
    return list(processor.image_mean), list(processor.image_std)


def _wrap(model, normalisation: tuple[list[float], list[float]] | None = None):
    """Only the two tensors the adapter needs, in a fixed order, from raw [0,1] RGB.

    torch is imported here, not at module level: it is the `train` extra, and
    `detection_error` below must stay importable (and testable) without it.
    """
    import torch

    class _Wrapper(torch.nn.Module):
        def __init__(self, model: torch.nn.Module) -> None:
            super().__init__()
            self.model = model
            self.normalise = normalisation is not None
            if self.normalise:
                mean, std = normalisation
                self.register_buffer("mean", torch.tensor(mean).view(1, 3, 1, 1))
                self.register_buffer("std", torch.tensor(std).view(1, 3, 1, 1))

        def forward(self, pixel_values: torch.Tensor):
            if self.normalise:
                pixel_values = (pixel_values - self.mean) / self.std
            out = self.model(pixel_values=pixel_values)
            return out.logits, out.pred_boxes

    return _Wrapper(model)


def freeze_position_embeddings(model, height: int, width: int) -> int:
    """Precompute any ViT position-embedding resize for the one export size.

    RF-DETR's DINOv2 backbone resizes its position embeddings to the input grid with an
    antialiased bicubic interpolation, which no ONNX opset can express. The input shape
    is fixed at export, so the resized table is a constant: compute it once in torch and
    let the graph carry the result. Returns how many embedding modules were frozen.
    """
    import torch

    frozen = 0
    for module in model.modules():
        if not (hasattr(module, "interpolate_pos_encoding") and hasattr(module, "patch_size")):
            continue
        patch = int(module.patch_size)
        tokens = (height // patch) * (width // patch) + 1
        probe = torch.zeros(1, tokens, module.position_embeddings.shape[-1])
        with torch.no_grad():
            table = module.interpolate_pos_encoding(probe, height, width).detach().clone()

        def fixed(embeddings, h, w, *, _table=table):
            if (int(h), int(w)) != (height, width):
                raise ValueError(f"exported for {height}x{width}, got {h}x{w}")
            return _table.to(embeddings.dtype)

        module.interpolate_pos_encoding = fixed
        frozen += 1
    return frozen


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
    import torch
    from transformers import AutoConfig, AutoImageProcessor, AutoModelForObjectDetection

    from ivaas_ml.train import processor_overrides

    model_type = AutoConfig.from_pretrained(checkpoint).model_type
    if model_type not in ARCH_BY_MODEL_TYPE:
        raise ValueError(f"{checkpoint}: unsupported model type {model_type!r}")
    arch = ARCH_BY_MODEL_TYPE[model_type]
    processor = AutoImageProcessor.from_pretrained(checkpoint, **processor_overrides(arch))
    model = AutoModelForObjectDetection.from_pretrained(checkpoint).eval()
    size = processor.size
    h, w = int(size["height"]), int(size["width"])
    dummy = torch.rand(1, 3, h, w)
    freeze_position_embeddings(model, h, w)
    wrapper = _wrap(model, processor_normalisation(processor)).eval()
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
        "labels": sidecar_labels(model.config.id2label, model.config.num_labels),
        "arch": arch,
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
    ap.add_argument("--opset", type=int, default=17)
    args = ap.parse_args()
    meta = export(args.checkpoint, args.out, opset=args.opset)
    print(f"{args.out} ({args.out.stat().st_size / 1e6:.1f} MB)  {meta}")


if __name__ == "__main__":
    main()
