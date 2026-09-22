"""Value types passed between pipeline stages. numpy is the only dependency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np


@dataclass(frozen=True)
class Frame:
    camera_id: str
    image: np.ndarray  # HxWx3 BGR
    captured_at: datetime


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    def iou(self, other: Box) -> float:
        ix = max(0.0, min(self.x2, other.x2) - max(self.x1, other.x1))
        iy = max(0.0, min(self.y2, other.y2) - max(self.y1, other.y1))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


@dataclass(frozen=True)
class Detection:
    box: Box
    label: str
    confidence: float


@dataclass(frozen=True)
class Track:
    track_id: int
    box: Box
    label: str
    confidence: float


class CrossDirection(StrEnum):
    FORWARD = "forward"  # a -> b side of the line
    BACKWARD = "backward"


@dataclass(frozen=True)
class Crossing:
    camera_id: str
    track_id: int
    direction: CrossDirection
    confidence: float
    at: datetime
    crates: int = 1  # a stack crossing carries its layer count


@dataclass(frozen=True)
class PlateCandidate:
    """One OCR read of one plate in one frame. Raw: not yet validated or voted."""

    text: str
    confidence: float
    box: Box


@dataclass(frozen=True)
class PlateEvent:
    """A plate confirmed across several frames: safe to attach to a truck session."""

    camera_id: str
    plate: str  # normalised, e.g. "ABC 1234"
    confidence: float
    reads: int
    at: datetime
