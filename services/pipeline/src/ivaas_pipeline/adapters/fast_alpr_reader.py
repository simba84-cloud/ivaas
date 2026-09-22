"""PlateReader backed by fast-alpr (MIT): YOLO plate detector + ViT plate OCR, ONNX."""

from __future__ import annotations

import numpy as np

from ivaas_pipeline.types import Box, Frame, PlateCandidate

# CoreML cannot run the OCR graph (batch-dim range error on Apple silicon); CUDA or CPU.
PROVIDERS = ["CUDAExecutionProvider", "CPUExecutionProvider"]


class FastAlprPlateReader:
    def __init__(
        self,
        detector_model: str = "yolo-v9-t-384-license-plate-end2end",
        ocr_model: str = "global-plates-mobile-vit-v2-model",
    ) -> None:
        import onnxruntime as ort
        from fast_alpr import ALPR

        providers = [p for p in PROVIDERS if p in ort.get_available_providers()]
        self._alpr = ALPR(
            detector_model=detector_model,
            ocr_model=ocr_model,
            detector_providers=providers,
            ocr_providers=providers,
        )

    def read(self, frame: Frame) -> list[PlateCandidate]:
        out = []
        for r in self._alpr.predict(frame.image):
            if not r.ocr or not r.ocr.text:
                continue
            conf = r.ocr.confidence
            # per-character confidences: the weakest character decides whether we trust it
            conf = (
                float(np.min(conf)) if isinstance(conf, list | tuple | np.ndarray) else float(conf)
            )
            b = r.detection.bounding_box
            out.append(PlateCandidate(r.ocr.text, conf, Box(b.x1, b.y1, b.x2, b.y2)))
        return out
