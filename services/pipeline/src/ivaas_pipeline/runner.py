"""Wires the stages into one per-camera pipeline. Knows ports, not adapters."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ivaas_pipeline.ports import (
    CrossingCounter,
    CrossingFuser,
    CrossingSink,
    Detector,
    FrameSource,
    PlateReader,
    PlateSink,
    Preprocessor,
    Tracker,
)
from ivaas_pipeline.stages.plates import PlateVoter
from ivaas_pipeline.types import Crossing


@dataclass
class FusingSink:
    """Sink decorator shared by every camera pipeline at a chokepoint.

    Fusion has to see crossings from *all* redundant cameras to de-duplicate
    them, so it lives here rather than inside the per-camera pipeline.
    """

    fuser: CrossingFuser
    downstream: CrossingSink
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, crossing: Crossing) -> None:
        with self._lock:
            for fused in self.fuser.fuse([crossing]):
                self.downstream.emit(fused)


@dataclass
class CameraPipeline:
    source: FrameSource
    preprocessor: Preprocessor
    detector: Detector
    tracker: Tracker
    counter: CrossingCounter
    sink: CrossingSink

    def run(self, max_frames: int | None = None) -> int:
        emitted = 0
        for n, frame in enumerate(self.source.frames(), start=1):
            frame = self.preprocessor.process(frame)
            tracks = self.tracker.update(self.detector.detect(frame))
            for crossing in self.counter.update(frame, tracks):
                self.sink.emit(crossing)
                emitted += 1
            if max_frames is not None and n >= max_frames:
                break
        return emitted


@dataclass
class LprPipeline:
    """The licence-plate camera: read every frame, vote, deliver confirmed plates."""

    source: FrameSource
    reader: PlateReader
    voter: PlateVoter
    sink: PlateSink

    def run(self, max_frames: int | None = None) -> int:
        emitted = 0
        for n, frame in enumerate(self.source.frames(), start=1):
            for event in self.voter.update(frame, self.reader.read(frame)):
                self.sink.emit(event)
                emitted += 1
            if max_frames is not None and n >= max_frames:
                break
        return emitted
