"""Pipeline metrics for Prometheus, scraped from the edge node.

    ivaas_pipeline_frame_lag_seconds{camera}        arrival -> result, last frame
    ivaas_pipeline_frame_processing_seconds{camera} time spent on each frame
    ivaas_pipeline_frames_processed_total{camera}
    ivaas_pipeline_frames_dropped_total{camera}     skipped because processing was busy
    ivaas_pipeline_stream_up{camera}                1 while the stream is open
    ivaas_pipeline_stream_reconnects_total{camera}

A camera keeping up shows a flat lag near its processing time and a drop counter
that does not move. A camera that cannot keep up shows the drop counter climbing:
lag stays bounded (the newest frame is always taken), but the tracker sees fewer
frames, which is what puts the count at risk.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

# processing time buckets: a GPU frame is tens of ms, a CPU frame hundreds
_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 2.0, 5.0)


class PrometheusMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        kw = {"registry": registry} if registry is not None else {}
        self._lag = Gauge(
            "ivaas_pipeline_frame_lag_seconds",
            "Seconds from a frame arriving to its result being ready (last frame)",
            ["camera"],
            **kw,
        )
        self._seconds = Histogram(
            "ivaas_pipeline_frame_processing_seconds",
            "Seconds spent processing one frame",
            ["camera"],
            buckets=_BUCKETS,
            **kw,
        )
        self._processed = Counter(
            "ivaas_pipeline_frames_processed", "Frames processed", ["camera"], **kw
        )
        self._dropped = Counter(
            "ivaas_pipeline_frames_dropped",
            "Frames skipped because the previous frame was still being processed",
            ["camera"],
            **kw,
        )
        self._was_up: dict[str, bool] = {}
        self._up = Gauge("ivaas_pipeline_stream_up", "1 while the stream is open", ["camera"], **kw)
        self._reconnects = Counter(
            "ivaas_pipeline_stream_reconnects", "Times the stream was reopened", ["camera"], **kw
        )
        # for the heartbeat: recent frame times and the last lag, per camera
        self._lock = threading.Lock()
        self._recent: dict[str, deque[float]] = {}
        self._last_lag: dict[str, float] = {}

    def processed(self, camera_id: str, seconds: float, lag: float) -> None:
        self._seconds.labels(camera_id).observe(seconds)
        self._lag.labels(camera_id).set(lag)
        self._processed.labels(camera_id).inc()
        with self._lock:
            self._recent.setdefault(camera_id, deque(maxlen=600)).append(time.monotonic())
            self._last_lag[camera_id] = lag

    def dropped(self, camera_id: str, frames: int) -> None:
        if frames > 0:
            self._dropped.labels(camera_id).inc(frames)

    def connected(self, camera_id: str, up: bool) -> None:
        before = self._was_up.get(camera_id)
        self._up.labels(camera_id).set(1 if up else 0)
        if up and before is False:  # the first open is not a reconnect
            self._reconnects.labels(camera_id).inc()
        self._was_up[camera_id] = up

    def snapshot(self, window_s: float = 60.0) -> dict[str, dict]:
        """Per camera: open or not, frames processed per second lately, last lag.

        A camera that has never opened is reported as disconnected, not omitted:
        the fleet view must see a camera that never came up.
        """
        now = time.monotonic()
        with self._lock:
            cameras = set(self._was_up) | set(self._recent)
            out = {}
            for cam in sorted(cameras):
                recent = [t for t in self._recent.get(cam, ()) if now - t <= window_s]
                out[cam] = {
                    "connected": bool(self._was_up.get(cam, False)),
                    "fps": round(len(recent) / window_s, 2),
                    "lag_s": round(self._last_lag[cam], 3) if cam in self._last_lag else None,
                }
        return out


def serve(port: int) -> None:
    """Expose /metrics on `port` for Prometheus."""
    start_http_server(port)
