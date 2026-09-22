"""Stage 2: de-noise, undistort, and normalise contrast for night footage."""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from ivaas_pipeline.types import Frame


@dataclass(frozen=True)
class Calibration:
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray


class OpenCvPreprocessor:
    def __init__(self, calibration: Calibration | None = None, clahe: bool = True) -> None:
        self._cal = calibration
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if clahe else None

    def process(self, frame: Frame) -> Frame:
        img = frame.image
        if self._cal is not None:
            img = cv2.undistort(img, self._cal.camera_matrix, self._cal.dist_coeffs)
        if self._clahe is not None:
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            lab[..., 0] = self._clahe.apply(lab[..., 0])
            img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        return replace(frame, image=img)
