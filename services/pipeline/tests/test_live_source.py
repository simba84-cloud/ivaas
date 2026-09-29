"""Live cameras: newest frame first, skipped frames reported, lag measured.

A pipeline slower than its camera used to queue frames and fall further behind
until the stream broke. Now it takes the newest frame, and says how many it had to
skip, because those are frames the tracker never saw.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import numpy as np
from prometheus_client import CollectorRegistry

from ivaas_pipeline.adapters.opencv_source import LatestFrameSource
from ivaas_pipeline.adapters.prometheus_metrics import PrometheusMetrics
from ivaas_pipeline.runner import CameraPipeline
from ivaas_pipeline.types import Frame


class Recorder:
    def __init__(self) -> None:
        self.drops = 0
        self.states: list[bool] = []
        self.samples: list[tuple[float, float]] = []

    def processed(self, camera_id: str, seconds: float, lag: float) -> None:
        self.samples.append((seconds, lag))

    def dropped(self, camera_id: str, frames: int) -> None:
        self.drops += frames

    def connected(self, camera_id: str, up: bool) -> None:
        self.states.append(up)


class FakeCapture:
    """Serves `n` numbered frames as fast as asked, then the stream ends.

    With `gate`, it pauses after `hold_after` frames until the gate opens, so a test
    knows exactly which frame the pipeline took first.
    """

    def __init__(
        self, n: int, opens: bool = True, gate: threading.Event | None = None, hold_after: int = 1
    ) -> None:
        self._n, self._i, self._opens = n, 0, opens
        self._gate, self._hold_after = gate, hold_after

    def isOpened(self) -> bool:
        return self._opens

    def read(self):
        if self._gate is not None and self._i == self._hold_after:
            self._gate.wait(2)
        if self._i >= self._n:
            time.sleep(0.01)
            return False, None
        self._i += 1
        return True, np.full((2, 2, 3), self._i, dtype=np.int32)

    def release(self) -> None:
        pass


def captures(*items):
    """A capture factory that hands out `items` in turn, then streams that never open."""
    queue = list(items)
    return lambda _uri: queue.pop(0) if queue else FakeCapture(0, opens=False)


def number(f: Frame) -> int:
    return int(f.image[0, 0, 0])


def wait_until(check, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_a_slow_pipeline_takes_the_newest_frame_and_counts_what_it_skipped():
    rec = Recorder()
    gate = threading.Event()
    source = LatestFrameSource(
        "cam",
        "rtsp://x",
        metrics=rec,
        capture=captures(FakeCapture(100, gate=gate)),
        reconnect_seconds=0.01,
    )
    frames = source.frames()
    assert number(next(frames)) == 1
    gate.set()
    time.sleep(0.2)  # "processing" frame 1: the camera delivers the rest meanwhile
    second = next(frames)
    frames.close()

    assert number(second) == 100  # the newest, not the next in the queue
    assert rec.drops == 98  # frames 2 to 99 were never seen


def test_stride_is_a_ceiling_and_is_not_counted_as_dropped():
    rec = Recorder()
    gate = threading.Event()
    source = LatestFrameSource(
        "cam",
        "rtsp://x",
        stride=5,
        metrics=rec,
        capture=captures(FakeCapture(100, gate=gate, hold_after=5)),
        reconnect_seconds=0.01,
    )
    frames = source.frames()
    assert number(next(frames)) == 5  # nothing before the fifth frame
    gate.set()
    time.sleep(0.2)
    second = number(next(frames))
    frames.close()

    assert second == 100
    assert rec.drops == 100 - 5 - 5  # the stride's own four skips are intended


def test_a_dropped_stream_reconnects_and_says_so():
    rec = Recorder()
    source = LatestFrameSource(
        "cam",
        "rtsp://x",
        metrics=rec,
        capture=captures(FakeCapture(3), FakeCapture(50)),
        reconnect_seconds=0.01,
    )
    frames = source.frames()
    next(frames)  # the reader is running; the first stream ends after three frames
    reopened = wait_until(lambda: rec.states[:3] == [True, False, True])
    frames.close()

    assert reopened, rec.states


def test_closing_the_source_stops_its_reader():
    source = LatestFrameSource(
        "cam-close", "rtsp://x", capture=captures(FakeCapture(10_000)), reconnect_seconds=0.01
    )
    frames = source.frames()
    next(frames)
    frames.close()
    assert wait_until(lambda: not any(t.name == "cam-close-reader" for t in threading.enumerate()))


def test_the_runner_reports_processing_time_and_lag():
    rec = Recorder()
    old = datetime.now(UTC) - timedelta(seconds=2)  # arrived two seconds ago

    class Source:
        def frames(self):
            return (Frame("cam", np.zeros((2, 2, 3), np.uint8), old) for _ in range(3))

    class Same:
        def process(self, f):
            return f

    class Nothing:
        def detect(self, f):
            return []

        def update(self, *a):
            return []

    CameraPipeline(Source(), Same(), Nothing(), Nothing(), Nothing(), sink=None, metrics=rec).run()

    assert len(rec.samples) == 3
    assert all(lag >= 2.0 and seconds >= 0 for seconds, lag in rec.samples)


def test_prometheus_exposes_lag_drops_and_reconnects():
    reg = CollectorRegistry()
    m = PrometheusMetrics(reg)
    m.connected("cam", True)
    m.processed("cam", 0.3, 0.45)
    m.dropped("cam", 7)
    m.dropped("cam", 0)
    m.connected("cam", False)
    m.connected("cam", True)

    value = lambda name: reg.get_sample_value(name, {"camera": "cam"})
    assert value("ivaas_pipeline_frame_lag_seconds") == 0.45
    assert value("ivaas_pipeline_frames_processed_total") == 1
    assert value("ivaas_pipeline_frames_dropped_total") == 7
    assert value("ivaas_pipeline_stream_up") == 1
    assert value("ivaas_pipeline_stream_reconnects_total") == 1  # the first open is not one


def test_a_failing_decoder_is_treated_as_a_dropped_stream():
    """The reader thread must never die quietly: that would stall the camera forever."""

    class Broken(FakeCapture):
        def read(self):
            raise RuntimeError("corrupt packet")

    rec = Recorder()
    source = LatestFrameSource(
        "cam",
        "rtsp://x",
        metrics=rec,
        capture=captures(Broken(5), FakeCapture(5)),
        reconnect_seconds=0.01,
    )
    frames = source.frames()
    assert number(next(frames)) >= 1  # frames still arrive, from the reopened stream
    frames.close()
    assert rec.states[:2] == [True, False]
