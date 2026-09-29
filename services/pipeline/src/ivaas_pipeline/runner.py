"""Wires the stages into one per-camera pipeline. Knows ports, not adapters."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from ivaas_pipeline.ports import (
    CrossingCounter,
    CrossingFuser,
    CrossingSink,
    Detector,
    FrameSource,
    NullMetrics,
    PipelineMetrics,
    PlateReader,
    PlateSink,
    Preprocessor,
    Tracker,
)
from ivaas_pipeline.stages.plates import PlateVoter
from ivaas_pipeline.types import Crossing, Frame


def _close(frames: object) -> None:
    close = getattr(frames, "close", None)
    if close is not None:
        close()


def _report(metrics: PipelineMetrics, frame: Frame, started: float) -> None:
    """How long this frame took, and how far behind the camera its result is."""
    now = time.time()
    metrics.processed(
        frame.camera_id, time.perf_counter() - started, now - frame.captured_at.timestamp()
    )


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
    metrics: PipelineMetrics = field(default_factory=NullMetrics)

    def run(self, max_frames: int | None = None) -> int:
        emitted = 0
        frames = self.source.frames()
        try:
            for n, frame in enumerate(frames, start=1):
                started = time.perf_counter()
                frame = self.preprocessor.process(frame)
                tracks = self.tracker.update(self.detector.detect(frame))
                for crossing in self.counter.update(frame, tracks):
                    self.sink.emit(crossing)
                    emitted += 1
                _report(self.metrics, frame, started)
                if max_frames is not None and n >= max_frames:
                    break
        finally:
            _close(frames)  # a live source stops its reader thread
        return emitted


@dataclass
class LprPipeline:
    """The licence-plate camera: read every frame, vote, deliver confirmed plates."""

    source: FrameSource
    reader: PlateReader
    voter: PlateVoter
    sink: PlateSink
    metrics: PipelineMetrics = field(default_factory=NullMetrics)

    def run(self, max_frames: int | None = None) -> int:
        emitted = 0
        frames = self.source.frames()
        try:
            for n, frame in enumerate(frames, start=1):
                started = time.perf_counter()
                for event in self.voter.update(frame, self.reader.read(frame)):
                    self.sink.emit(event)
                    emitted += 1
                _report(self.metrics, frame, started)
                if max_frames is not None and n >= max_frames:
                    break
        finally:
            _close(frames)
        return emitted
