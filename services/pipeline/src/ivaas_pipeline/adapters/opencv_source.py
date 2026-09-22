"""Stage 1: frames from an RTSP URL (MediaMTX / NVR) or a recorded file."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import cv2

from ivaas_pipeline.types import Frame

log = logging.getLogger(__name__)


class OpenCvFrameSource:
    def __init__(
        self, camera_id: str, uri: str, *, stride: int = 1, reconnect_seconds: float = 3.0
    ) -> None:
        self._camera_id = camera_id
        self._uri = uri
        self._stride = max(1, stride)
        self._reconnect = reconnect_seconds
        self._live = "://" in uri

    def frames(self) -> Iterator[Frame]:
        while True:
            cap = cv2.VideoCapture(self._uri)
            if not cap.isOpened():
                log.warning("cannot open %s", self._uri)
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
            if not self._live:
                return  # a file ends; a camera reconnects
            log.warning("stream %s dropped, reconnecting in %.0fs", self._uri, self._reconnect)
            time.sleep(self._reconnect)
