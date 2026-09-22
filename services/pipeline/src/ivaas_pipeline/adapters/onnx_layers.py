"""LayerCounter backed by a learned regressor (ml/src/ivaas_ml/layers.py) on ONNX.

Drop-in for PeriodicityLayerCounter behind the same port. Confidence is not a
probability here; it is 1 for any in-range prediction, so the median-over-track
logic in the counters treats every frame equally.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from ivaas_pipeline.adapters.onnx_rtdetr import PROVIDER_PREFERENCE
from ivaas_pipeline.stages.layers import LayerEstimate
from ivaas_pipeline.types import Box

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class OnnxLayerCounter:
    def __init__(self, model_path: str, *, margin: float = 0.08) -> None:
        meta = json.loads(Path(model_path).with_suffix(".json").read_text())
        self._size = (int(meta["input_width"]), int(meta["input_height"]))
        self._max = int(meta.get("max_layers", 40))
        self._margin = margin  # the training crops carried this much context around the box
        available = ort.get_available_providers()
        self._session = ort.InferenceSession(
            model_path, providers=[p for p in PROVIDER_PREFERENCE if p in available]
        )

    def estimate(self, image: np.ndarray, box: Box) -> LayerEstimate | None:
        h_img, w_img = image.shape[:2]
        w, h = box.x2 - box.x1, box.y2 - box.y1
        x1, y1 = max(0, int(box.x1 - self._margin * w)), max(0, int(box.y1 - self._margin * h))
        x2, y2 = (
            min(w_img, int(box.x2 + self._margin * w)),
            min(h_img, int(box.y2 + self._margin * h)),
        )
        if x2 - x1 < 8 or y2 - y1 < 16:
            return None
        crop = cv2.resize(image[y1:y2, x1:x2], self._size, interpolation=cv2.INTER_AREA)
        x = (crop[:, :, ::-1].astype(np.float32) / 255.0 - MEAN) / STD
        blob = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
        (out,) = self._session.run(None, {"crop": blob})
        layers = float(out[0, 0])
        if not (0.5 <= layers <= self._max):
            return None
        return LayerEstimate(layers, 0, 1.0)
