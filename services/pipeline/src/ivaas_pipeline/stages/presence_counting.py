"""Count stacks by sustained presence in a zone, for cameras that look INTO the truck.

Found on site footage: a camera mounted behind the truck door sees a stack only
once it is already inside. It appears at the door edge, jitters while the worker
positions it, and is set down a short distance away. It never cleanly crosses any
line, so `StackCrossingCounter` produced noise there (one stack crossing a line
three times in two seconds, others never crossing at all).

This counter emits one Crossing per track that has been seen inside the zone for
at least `min_seconds`, with the median layer count over those frames. A stack that
leaves the zone again within `min_seconds` (picked up and taken back out) is not
counted. Direction is always FORWARD: this counter cannot see unloading, which
the site's loading-bay process does not do through the same door anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from statistics import median

from ivaas_pipeline.ports import LayerCounter
from ivaas_pipeline.types import Box, CrossDirection, Crossing, Frame, Track


class PresenceZone:
    """Axis-aligned zone in pixels; a track is 'inside' when its centre is."""

    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    def contains(self, box: Box) -> bool:
        cx, cy = box.center
        return self.x1 <= cx <= self.x2 and self.y1 <= cy <= self.y2


class StackPresenceCounter:
    def __init__(
        self,
        zone: PresenceZone,
        layers: LayerCounter,
        *,
        label: str = "stack",
        min_seconds: float = 2.0,
        min_estimates: int = 3,
        fallback_crates: int = 1,
    ) -> None:
        self._zone, self._layers, self._label = zone, layers, label
        self._min = timedelta(seconds=min_seconds)
        self._min_est, self._fallback = min_estimates, fallback_crates
        self._first_seen: dict[int, datetime] = {}
        self._estimates: dict[int, list[float]] = {}
        self._counted: set[int] = set()

    def update(self, frame: Frame, tracks: Sequence[Track]) -> list[Crossing]:
        out: list[Crossing] = []
        seen: set[int] = set()
        for t in tracks:
            if t.label != self._label or not self._zone.contains(t.box):
                continue
            seen.add(t.track_id)
            self._first_seen.setdefault(t.track_id, frame.captured_at)
            est = self._layers.estimate(frame.image, t.box)
            if est is not None:
                self._estimates.setdefault(t.track_id, []).append(est.layers)
            if t.track_id in self._counted:
                continue
            if frame.captured_at - self._first_seen[t.track_id] >= self._min:
                hist = self._estimates.get(t.track_id, [])
                trusted = len(hist) >= self._min_est
                self._counted.add(t.track_id)
                out.append(
                    Crossing(
                        camera_id=frame.camera_id,
                        track_id=t.track_id,
                        direction=CrossDirection.FORWARD,
                        confidence=t.confidence if trusted else 0.0,
                        at=frame.captured_at,
                        crates=max(1, round(median(hist))) if trusted else self._fallback,
                    )
                )
        # a track that left the zone before qualifying is forgotten; a counted one stays
        # remembered so a brief re-detection does not count it twice
        for gone in [
            tid for tid in self._first_seen if tid not in seen and tid not in self._counted
        ]:
            del self._first_seen[gone]
            self._estimates.pop(gone, None)
        return out
