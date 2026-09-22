"""Stage contracts. Each pipeline stage from the POC scope (section 4.2) is a
Protocol, so a model or algorithm can be replaced without touching the runner:
YOLO -> RT-DETR, IoU tracker -> ByteTrack, HTTP sink -> NATS, and so on.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol

import numpy as np

from ivaas_pipeline.stages.layers import LayerEstimate
from ivaas_pipeline.types import Box, Crossing, Detection, Frame, PlateCandidate, PlateEvent, Track


class FrameSource(Protocol):
    def frames(self) -> Iterator[Frame]: ...


class Preprocessor(Protocol):
    def process(self, frame: Frame) -> Frame: ...


class Detector(Protocol):
    def detect(self, frame: Frame) -> Sequence[Detection]: ...


class Tracker(Protocol):
    def update(self, detections: Sequence[Detection]) -> Sequence[Track]: ...


class CrossingCounter(Protocol):
    def update(self, frame: Frame, tracks: Sequence[Track]) -> Sequence[Crossing]: ...


class LayerCounter(Protocol):
    """How many crates are in this stack? Swappable: periodicity today, a learned
    counter on stack crops later, without touching the crossing logic."""

    def estimate(self, image: np.ndarray, box: Box) -> LayerEstimate | None: ...


class CrossingFuser(Protocol):
    """Merges crossings from redundant cameras into one de-duplicated stream."""

    def fuse(self, crossings: Sequence[Crossing]) -> Sequence[Crossing]: ...


class CrossingSink(Protocol):
    def emit(self, crossing: Crossing) -> None: ...


class PlateReader(Protocol):
    def read(self, frame: Frame) -> Sequence[PlateCandidate]: ...


class PlateSink(Protocol):
    def emit(self, event: PlateEvent) -> None: ...
