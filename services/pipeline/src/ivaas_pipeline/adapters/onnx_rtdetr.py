"""Stage 3: RT-DETR detector on ONNX Runtime.

Consumes the file written by `ml/src/ivaas_ml/export.py` plus its sidecar
<name>.json (labels, input size). RT-DETR is a set predictor: no anchors, no NMS,
one score per query. Input is a plain resize to the training size (no letterbox),
scaled to [0, 1] with no mean/std normalisation, matching RTDetrImageProcessor.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from ivaas_pipeline.types import Box, Detection, Frame

# No CoreML: it can run only ~40% of this transformer graph and crashes on a partition
# boundary (seen on Apple silicon). CPU is fine for development; the edge node has CUDA.
PROVIDER_PREFERENCE = [
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "CPUExecutionProvider",
]


def decode(
    logits: np.ndarray,
    pred_boxes: np.ndarray,
    labels: Sequence[str],
    width: int,
    height: int,
    threshold: float,
) -> list[Detection]:
    """(Q,C) logits + (Q,4) normalised cxcywh -> pixel-space detections above threshold."""
    probs = 1.0 / (1.0 + np.exp(-logits))
    class_ids = probs.argmax(axis=1)
    scores = probs[np.arange(len(probs)), class_ids]
    out = []
    for q in np.flatnonzero(scores >= threshold):
        cx, cy, w, h = pred_boxes[q]
        out.append(
            Detection(
                Box(
                    float(np.clip((cx - w / 2) * width, 0, width)),
                    float(np.clip((cy - h / 2) * height, 0, height)),
                    float(np.clip((cx + w / 2) * width, 0, width)),
                    float(np.clip((cy + h / 2) * height, 0, height)),
                ),
                labels[int(class_ids[q])],
                float(scores[q]),
            )
        )
    return out


class OnnxRtDetrDetector:
    def __init__(
        self,
        model_path: str,
        *,
        threshold: float = 0.5,
        providers: Sequence[str] | None = None,
    ) -> None:
        meta = json.loads(Path(model_path).with_suffix(".json").read_text())
        self._labels: list[str] = meta["labels"]
        self._size = (int(meta["input_width"]), int(meta["input_height"]))
        self._threshold = threshold
        available = ort.get_available_providers()
        wanted = providers or PROVIDER_PREFERENCE
        self._session = ort.InferenceSession(
            model_path, providers=[p for p in wanted if p in available]
        )

    @property
    def labels(self) -> list[str]:
        return list(self._labels)

    def detect(self, frame: Frame) -> list[Detection]:
        h, w = frame.image.shape[:2]
        resized = cv2.resize(frame.image, self._size, interpolation=cv2.INTER_LINEAR)
        blob = resized[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        logits, boxes = self._session.run(None, {"pixel_values": np.ascontiguousarray(blob)})
        return decode(logits[0], boxes[0], self._labels, w, h, self._threshold)
