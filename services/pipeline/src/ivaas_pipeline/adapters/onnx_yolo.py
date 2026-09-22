"""Stage 3: YOLO-family detector on ONNX Runtime.

ONNX keeps the runtime vendor-neutral: the same exported model runs on the
edge node's NVIDIA GPU (CUDA/TensorRT providers), on CPU, or on Apple CoreML.
Expects the standard Ultralytics export layout: output (1, 4 + classes, N).
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np
import onnxruntime as ort

from ivaas_pipeline.types import Box, Detection, Frame


class OnnxYoloDetector:
    def __init__(
        self,
        model_path: str,
        labels: Sequence[str],
        *,
        confidence: float = 0.35,
        nms_iou: float = 0.5,
        providers: Sequence[str] | None = None,
    ) -> None:
        available = ort.get_available_providers()
        wanted = providers or [
            "TensorrtExecutionProvider",
            "CUDAExecutionProvider",
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ]
        self._session = ort.InferenceSession(
            model_path, providers=[p for p in wanted if p in available]
        )
        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        self._size = int(inp.shape[2]) if isinstance(inp.shape[2], int) else 640
        self._labels = list(labels)
        self._conf = confidence
        self._nms_iou = nms_iou

    def detect(self, frame: Frame) -> list[Detection]:
        blob, scale, pad = self._letterbox(frame.image)
        (out,) = self._session.run(None, {self._input_name: blob})
        preds = out[0].T  # (N, 4 + classes)
        scores = preds[:, 4:]
        class_ids = scores.argmax(axis=1)
        confs = scores[np.arange(len(scores)), class_ids]
        keep = confs >= self._conf
        preds, class_ids, confs = preds[keep], class_ids[keep], confs[keep]
        if len(preds) == 0:
            return []

        cx, cy, w, h = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
        x1 = (cx - w / 2 - pad[0]) / scale
        y1 = (cy - h / 2 - pad[1]) / scale
        bw, bh = w / scale, h / scale
        boxes = np.stack([x1, y1, bw, bh], axis=1)
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), confs.tolist(), self._conf, self._nms_iou)
        return [
            Detection(
                Box(
                    float(x1[i]),
                    float(y1[i]),
                    float(x1[i] + bw[i]),
                    float(y1[i] + bh[i]),
                ),
                self._labels[int(class_ids[i])],
                float(confs[i]),
            )
            for i in np.array(idx).flatten()
        ]

    def _letterbox(self, image: np.ndarray) -> tuple[np.ndarray, float, tuple[float, float]]:
        h, w = image.shape[:2]
        scale = self._size / max(h, w)
        nh, nw = round(h * scale), round(w * scale)
        resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self._size, self._size, 3), 114, dtype=np.uint8)
        top, left = (self._size - nh) // 2, (self._size - nw) // 2
        canvas[top : top + nh, left : left + nw] = resized
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        return np.ascontiguousarray(blob), scale, (float(left), float(top))
