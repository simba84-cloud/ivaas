"""Stage 5: count a crate once, when its track centre crosses the chokepoint line."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ivaas_pipeline.types import CrossDirection, Crossing, Frame, Track

Point = tuple[float, float]


@dataclass(frozen=True)
class Line:
    a: Point
    b: Point

    def side(self, p: Point) -> float:
        """Signed area: >0 on one side of a->b, <0 on the other, 0 on the line."""
        return (self.b[0] - self.a[0]) * (p[1] - self.a[1]) - (self.b[1] - self.a[1]) * (
            p[0] - self.a[0]
        )

    def spans(self, p: Point, q: Point) -> bool:
        """True if segment p->q actually intersects the finite line segment."""
        d1, d2 = self.side(p), self.side(q)
        other = Line(p, q)
        d3, d4 = other.side(self.a), other.side(self.b)
        return d1 * d2 < 0 and d3 * d4 < 0


class LineCrossingCounter:
    def __init__(self, line: Line, labels: frozenset[str] = frozenset({"crate"})) -> None:
        self._line = line
        self._labels = labels
        self._last: dict[int, Point] = {}

    def update(self, frame: Frame, tracks: Sequence[Track]) -> list[Crossing]:
        crossings: list[Crossing] = []
        seen: set[int] = set()
        for t in tracks:
            if t.label not in self._labels:
                continue
            seen.add(t.track_id)
            prev, cur = self._last.get(t.track_id), t.box.center
            if self._line.side(cur) == 0:
                continue  # exactly on the line: wait for the next frame to pick a side
            self._last[t.track_id] = cur
            if prev is None or not self._line.spans(prev, cur):
                continue
            direction = (
                CrossDirection.FORWARD if self._line.side(cur) > 0 else CrossDirection.BACKWARD
            )
            crossings.append(
                Crossing(frame.camera_id, t.track_id, direction, t.confidence, frame.captured_at)
            )
        # forget tracks the tracker has dropped, so memory stays bounded
        for gone in self._last.keys() - seen:
            del self._last[gone]
        return crossings
