"""An image classifier exported to ONNX, for crops: "is this person in uniform / PPE?".

Takes `pixel_values` (N,3,H,W) in [0,1] and returns `logits` (N,C). Class names and
the input size sit next to the model as <name>.json, like the detectors.
No pretrained model ships for this: what counts as uniform is particular to a site,
so it is trained on that site's staff (see ml/README.md, "PPE / uniform").
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from ivaas_pipeline.adapters.onnx_rtdetr import PROVIDER_PREFERENCE


class OnnxImageClassifier:
    def __init__(self, model_path: str) -> None:
        meta = json.loads(Path(model_path).with_suffix(".json").read_text())
        self.labels: list[str] = meta["labels"]
        self._size = (int(meta["input_width"]), int(meta["input_height"]))
        available = ort.get_available_providers()
        self._session = ort.InferenceSession(
            model_path, providers=[p for p in PROVIDER_PREFERENCE if p in available]
        )

    def classify(self, crop: np.ndarray) -> tuple[str, float]:
        resized = cv2.resize(crop, self._size, interpolation=cv2.INTER_LINEAR)
        blob = resized[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        (logits,) = self._session.run(None, {"pixel_values": np.ascontiguousarray(blob)})
        z = logits[0] - logits[0].max()
        p = np.exp(z) / np.exp(z).sum()
        i = int(p.argmax())
        return self.labels[i], float(p[i])
