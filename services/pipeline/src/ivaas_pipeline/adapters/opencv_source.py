"""Stage 1: frames from an RTSP URL (MediaMTX / NVR) or a recorded file.

Two ways to read, because files and cameras want opposite things:

- `OpenCvFrameSource` hands over every frame in order. Right for a recorded file:
  nothing is waiting, and the count must not depend on how fast the machine is.
- `LatestFrameSource` is for live cameras. A reader thread drains the stream as fast
  as it arrives and keeps only the newest frame; the pipeline always takes the newest.
  If processing is slower than the camera, frames are skipped rather than queued, so
  the count stays seconds behind reality instead of drifting further behind until
  the stream breaks. Skipped frames are reported, because they are frames the
  tracker never saw.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import cv2

from ivaas_pipeline.ports import NullMetrics, PipelineMetrics
from ivaas_pipeline.types import Frame

log = logging.getLogger(__name__)


class OpenCvFrameSource:
    def __init__(
        self,
        camera_id: str,
        uri: str,
        *,
        stride: int = 1,
        reconnect_seconds: float = 3.0,
        metrics: PipelineMetrics | None = None,
    ) -> None:
        self._camera_id = camera_id
        self._uri = uri
        self._stride = max(1, stride)
        self._reconnect = reconnect_seconds
        self._live = "://" in uri
        self._metrics = metrics or NullMetrics()

    def frames(self) -> Iterator[Frame]:
        while True:
            cap = cv2.VideoCapture(self._uri)
            if not cap.isOpened():
                log.warning("cannot open %s", self._uri)
            else:
                self._metrics.connected(self._camera_id, True)
            index = 0
            while cap.isOpened():
                ok, image = cap.read()
                if not ok:
                    break
                index += 1
                if index % self._stride:
                    continue
                yield Frame(self._camera_id, image, datetime.now(UTC))
            cap.release()
            self._metrics.connected(self._camera_id, False)
            if not self._live:
                return  # a file ends; a camera reconnects
            log.warning("stream %s dropped, reconnecting in %.0fs", self._uri, self._reconnect)
            time.sleep(self._reconnect)


class _Slot:
    """The one frame waiting to be processed: the newest, with its sequence number."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.seq = 0
        self.image: Any = None
        self.at: datetime | None = None


class LatestFrameSource:
    """A live camera, newest frame first.

    `stride` keeps its meaning as a ceiling: at most every Nth frame of the stream is
    processed (the plate camera needs a few frames a second, not twenty-five). When
    processing is slower than that, the newest frame is taken and the rest counted
    as dropped.
    """

    def __init__(
        self,
        camera_id: str,
        uri: str,
        *,
        stride: int = 1,
        reconnect_seconds: float = 3.0,
        metrics: PipelineMetrics | None = None,
        capture: Callable[[str], Any] = cv2.VideoCapture,
    ) -> None:
        self._camera_id = camera_id
        self._uri = uri
        self._stride = max(1, stride)
        self._reconnect = reconnect_seconds
        self._metrics = metrics or NullMetrics()
        self._capture = capture

    def frames(self) -> Iterator[Frame]:
        slot = _Slot()
        stop = threading.Event()
        reader = threading.Thread(
            target=self._read, args=(slot, stop), name=f"{self._camera_id}-reader", daemon=True
        )
        reader.start()
        last = 0
        try:
            while True:
                wanted = last + self._stride
                with slot.cond:
                    slot.cond.wait_for(lambda wanted=wanted: slot.seq >= wanted)
                    seq, image, at = slot.seq, slot.image, slot.at
                if last:
                    # beyond the stride's own skip, these were lost to a busy pipeline
                    self._metrics.dropped(self._camera_id, seq - last - self._stride)
                last = seq
                assert at is not None
                yield Frame(self._camera_id, image, at)
        finally:
            stop.set()

    def _read(self, slot: _Slot, stop: threading.Event) -> None:
        while not stop.is_set():
            cap = self._capture(self._uri)
            if not cap.isOpened():
                self._metrics.connected(self._camera_id, False)
                log.warning("cannot open %s, retrying in %.0fs", self._uri, self._reconnect)
                stop.wait(self._reconnect)
                continue
            self._metrics.connected(self._camera_id, True)
            try:
                while not stop.is_set():
                    ok, image = cap.read()
                    if not ok:
                        break
                    with slot.cond:
                        slot.seq += 1
                        slot.image = image
                        slot.at = datetime.now(UTC)  # when it reached us, not the sensor
                        slot.cond.notify_all()
            except Exception:
                # this thread dying would stall the camera for good: treat it as a drop
                log.exception("reading %s failed", self._uri)
            cap.release()
            self._metrics.connected(self._camera_id, False)
            if not stop.is_set():
                log.warning("stream %s dropped, reconnecting in %.0fs", self._uri, self._reconnect)
                stop.wait(self._reconnect)
