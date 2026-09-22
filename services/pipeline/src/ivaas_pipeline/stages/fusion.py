"""Stage 4/6: fuse the redundant chokepoint cameras.

Both chokepoint cameras see the same physical crate cross the same physical
line, so each crate produces up to one crossing per camera within a short
window. We keep the first and suppress its twins. When one camera misses a
crate (occlusion), the other camera's crossing still gets through, which is
the "re-check other camera views if a crate is lost" loop in the scope doc.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import datetime, timedelta

from ivaas_pipeline.types import Crossing


class TimeWindowFuser:
    def __init__(self, window: timedelta = timedelta(milliseconds=400)) -> None:
        self._window = window
        self._recent: deque[Crossing] = deque()
        self._newest: datetime | None = None

    def fuse(self, crossings: Sequence[Crossing]) -> list[Crossing]:
        out: list[Crossing] = []
        for c in sorted(crossings, key=lambda c: c.at):
            # Cameras deliver with network jitter, so events can arrive slightly
            # out of order: evict against the newest timestamp seen, and require
            # a twin to be genuinely close in time rather than merely still queued.
            self._newest = c.at if self._newest is None else max(self._newest, c.at)
            while self._recent and self._newest - self._recent[0].at > self._window:
                self._recent.popleft()
            twin = next(
                (
                    r
                    for r in self._recent
                    if r.camera_id != c.camera_id
                    and r.direction is c.direction
                    and abs(c.at - r.at) <= self._window
                ),
                None,
            )
            if twin is not None:
                self._recent.remove(twin)  # each crossing can absorb only one twin
                continue
            self._recent.append(c)
            out.append(c)
        return out
