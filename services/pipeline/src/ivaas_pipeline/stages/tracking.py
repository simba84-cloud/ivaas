"""Stage 5 (tracking half): greedy IoU tracker.

Deliberately dependency-free so the pipeline runs and is testable anywhere.
For production footage swap in ByteTrack via `adapters/bytetrack.py`; both
satisfy the same Tracker port.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ivaas_pipeline.types import Box, Detection, Track


@dataclass
class _Live:
    track_id: int
    box: Box
    label: str
    confidence: float
    missed: int = 0


class IouTracker:
    def __init__(self, iou_threshold: float = 0.3, max_missed: int = 15) -> None:
        self._iou = iou_threshold
        self._max_missed = max_missed
        self._live: list[_Live] = []
        self._next_id = 1

    def update(self, detections: Sequence[Detection]) -> list[Track]:
        pairs = sorted(
            (
                (t.box.iou(d.box), ti, di)
                for ti, t in enumerate(self._live)
                for di, d in enumerate(detections)
                if t.label == d.label
            ),
            reverse=True,
        )
        matched_t: set[int] = set()
        matched_d: set[int] = set()
        for iou, ti, di in pairs:
            if iou < self._iou:
                break
            if ti in matched_t or di in matched_d:
                continue
            matched_t.add(ti)
            matched_d.add(di)
            live, det = self._live[ti], detections[di]
            live.box, live.confidence, live.missed = det.box, det.confidence, 0

        for ti, live in enumerate(self._live):
            if ti not in matched_t:
                live.missed += 1
        self._live = [t for t in self._live if t.missed <= self._max_missed]

        for di, det in enumerate(detections):
            if di not in matched_d:
                self._live.append(_Live(self._next_id, det.box, det.label, det.confidence))
                self._next_id += 1

        return [
            Track(t.track_id, t.box, t.label, t.confidence) for t in self._live if t.missed == 0
        ]
