"""Count a stack once it has been set down: stationary for `settle_seconds`.

A door camera sees stacks only once inside the truck, and the truck parks in a
different spot every visit, so no fixed zone or line can separate "delivered"
from "wheeled past". What does separate them is motion: a delivered stack is
set down and stays put; a passing one keeps moving.

A track is counted the first time its centre has stayed within `settle_px` of
where it was `settle_seconds` earlier. Then it is remembered so a stack that
is later nudged, or briefly re-detected, is not counted again.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import datetime
from statistics import median

from ivaas_pipeline.ports import LayerCounter
from ivaas_pipeline.types import CrossDirection, Crossing, Frame, Track


class StackSettleCounter:
    def __init__(
        self,
        layers: LayerCounter,
        *,
        label: str = "stack",
        settle_seconds: float = 3.0,
        settle_px: float = 40.0,
        min_estimates: int = 3,
        fallback_crates: int = 1,
    ) -> None:
        self._layers, self._label = layers, label
        self._settle_s, self._settle_px = settle_seconds, settle_px
        self._min_est, self._fallback = min_estimates, fallback_crates
        self._history: dict[int, deque[tuple[datetime, float, float]]] = {}
        self._estimates: dict[int, list[float]] = {}
        self._counted: set[int] = set()

    def update(self, frame: Frame, tracks: Sequence[Track]) -> list[Crossing]:
        out: list[Crossing] = []
        seen: set[int] = set()
        now = frame.captured_at
        for t in tracks:
            if t.label != self._label:
                continue
            seen.add(t.track_id)
            cx, cy = t.box.center
            hist = self._history.setdefault(t.track_id, deque())
            hist.append((now, cx, cy))
            while hist and (now - hist[0][0]).total_seconds() > self._settle_s * 1.5:
                hist.popleft()
            est = self._layers.estimate(frame.image, t.box)
            if est is not None:
                self._estimates.setdefault(t.track_id, []).append(est.layers)
            if t.track_id in self._counted:
                continue
            # settled = a position from >= settle_seconds ago exists and we have not moved from it
            old = next(
                ((x, y) for ts, x, y in hist if (now - ts).total_seconds() >= self._settle_s), None
            )
            if old is None:
                continue
            if max(abs(cx - old[0]), abs(cy - old[1])) > self._settle_px:
                continue
            counts = self._estimates.get(t.track_id, [])
            trusted = len(counts) >= self._min_est
            self._counted.add(t.track_id)
            out.append(
                Crossing(
                    camera_id=frame.camera_id,
                    track_id=t.track_id,
                    direction=CrossDirection.FORWARD,
                    confidence=t.confidence if trusted else 0.0,
                    at=now,
                    crates=max(1, round(median(counts))) if trusted else self._fallback,
                )
            )
        for gone in [tid for tid in self._history if tid not in seen and tid not in self._counted]:
            del self._history[gone]
            self._estimates.pop(gone, None)
        return out
