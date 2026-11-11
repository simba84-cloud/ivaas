"""Stage 3, remote: a detector served over HTTP (RF-DETR on the cluster GPU).

Uploaded-video analysis sends each frame to the RF-DETR service instead of running
the model on the worker's CPU. See docs/adr/0003-remote-gpu-analysis.md for why frames
leave the site for this path and only this path; live cameras still run locally.

Contract: `POST <url>` multipart `image` (JPEG) and `threshold`, header
`Authorization: Bearer <key>`. The service wraps the Hugging Face object-detection
pipeline, so it may answer in either shape; both carry pixel xyxy boxes in the
coordinates of the image it was sent:

    [{"score": 0.9, "label": "stack", "box": {"xmin": .., "ymin": .., "xmax": .., "ymax": ..}}]
    {"boxes": [[x1, y1, x2, y2]], "labels": ["stack"], "scores": [0.9]}

(Either may also be wrapped as {"detections": [...]}).

A frame the service cannot answer raises; it never comes back as "no detections".
An empty frame counts as zero crates, so a quiet failure would lower the count this
system exists to report. The analysis job fails visibly instead and can be retried.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import cv2
import httpx
import numpy as np

from ivaas_pipeline.types import Box, Detection, Frame

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class DetectorUnavailable(RuntimeError):
    """The remote detector could not answer a frame. Never treated as an empty frame."""


class HttpDetector:
    def __init__(
        self,
        url: str,
        api_key: str = "",
        *,
        threshold: float = 0.2,
        timeout_s: float = 30.0,
        retries: int = 3,
        max_side: int = 1280,
        jpeg_quality: int = 90,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not url:
            raise ValueError("HttpDetector needs a URL")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._url = url
        self._client = client or httpx.Client(timeout=timeout_s, headers=headers)
        self._threshold = threshold
        self._retries = max(0, retries)
        # RF-DETR resizes to 560 px anyway: a 4K frame would only cost upload time
        self._max_side = max_side
        self._jpeg_quality = jpeg_quality
        self._sleep = sleep

    def detect(self, frame: Frame) -> Sequence[Detection]:
        h, w = frame.image.shape[:2]
        image, scale = self._shrink(frame.image)
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
        if not ok:
            raise DetectorUnavailable("could not encode the frame as JPEG")
        payload = self._post(jpeg.tobytes())
        return parse(payload, scale=scale, width=w, height=h)

    def close(self) -> None:
        self._client.close()

    def _shrink(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        h, w = image.shape[:2]
        longest = max(h, w)
        if self._max_side <= 0 or longest <= self._max_side:
            return image, 1.0
        scale = self._max_side / longest
        small = cv2.resize(
            image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA
        )
        return small, scale

    def _post(self, jpeg: bytes) -> Any:
        last: str = ""
        for attempt in range(self._retries + 1):
            if attempt:
                self._sleep(min(2.0 ** (attempt - 1), 8.0))
            try:
                r = self._client.post(
                    self._url,
                    files={"image": ("frame.jpg", jpeg, "image/jpeg")},
                    data={"threshold": str(self._threshold)},
                )
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                log.warning("detector request failed (attempt %d): %s", attempt + 1, last)
                continue
            if r.status_code in RETRYABLE_STATUS:
                last = f"HTTP {r.status_code}"
                log.warning("detector returned %s (attempt %d)", r.status_code, attempt + 1)
                continue
            if r.status_code >= 400:
                # a bad key or a bad request will not fix itself on retry
                raise DetectorUnavailable(f"detector rejected the frame: HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError as e:
                raise DetectorUnavailable("detector answered with something other than JSON") from e
        raise DetectorUnavailable(
            f"detector unavailable after {self._retries + 1} attempts: {last}"
        )


def parse(payload: Any, *, scale: float, width: int, height: int) -> list[Detection]:
    """Read either response shape into detections in original-frame pixels."""
    if isinstance(payload, dict) and "detections" in payload:
        payload = payload["detections"]
    rows: list[tuple[Sequence[float], str, float]] = []
    try:
        if isinstance(payload, list):
            for d in payload:
                b = d["box"]
                rows.append(((b["xmin"], b["ymin"], b["xmax"], b["ymax"]), d["label"], d["score"]))
        elif isinstance(payload, dict) and {"boxes", "labels", "scores"} <= payload.keys():
            boxes, labels, scores = payload["boxes"], payload["labels"], payload["scores"]
            if not len(boxes) == len(labels) == len(scores):
                raise DetectorUnavailable("detector answer has mismatched boxes, labels and scores")
            rows = [(b, lab, s) for b, lab, s in zip(boxes, labels, scores, strict=True)]
        else:
            raise DetectorUnavailable("detector answer is in an unknown shape")
    except (KeyError, TypeError) as e:
        raise DetectorUnavailable(f"detector answer is malformed: {e}") from e

    out: list[Detection] = []
    for (x1, y1, x2, y2), label, score in rows:
        box = Box(
            min(max(float(x1) / scale, 0.0), width),
            min(max(float(y1) / scale, 0.0), height),
            min(max(float(x2) / scale, 0.0), width),
            min(max(float(y2) / scale, 0.0), height),
        )
        if box.area > 0:
            out.append(Detection(box, str(label), float(score)))
    return out
