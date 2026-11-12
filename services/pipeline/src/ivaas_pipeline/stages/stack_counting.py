"""Count crates by the stack: one crossing per stack, carrying its layer count."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from ivaas_pipeline.ports import LayerCounter
from ivaas_pipeline.stages.counting import Line, Point
from ivaas_pipeline.types import CrossDirection, Crossing, Frame, Track


def vote_layers(estimates: Sequence[float]) -> tuple[int, float]:
    """The crate count for a stack and how firmly its frames agree on it.

    The count is the rounded median of the per-frame layer estimates (at least 1).
    `agreement` is the share of estimates that round to that same median: 1.0 when
    every frame saw the same height, lower when frames disagreed. A controlled bay
    uses it to mark a column unverified instead of trusting a split vote.
    """
    rounded = round(median(estimates))
    agreement = sum(1 for e in estimates if round(e) == rounded) / len(estimates)
    return max(1, rounded), agreement


class StackCrossingCounter:
    """CrossingCounter for stacks.

    Every frame a stack is tracked contributes one layer estimate. When the stack's
    centre crosses the line we emit a single Crossing whose `crates` is the **median**
    of those estimates: one blurred frame, or a worker stepping in front, cannot swing it.
    A stack that crosses before it has enough confident estimates is still reported,
    with `crates=fallback_crates`, and flagged by a confidence of 0 so it can be reviewed
    instead of silently dropped or silently guessed.
    """

    def __init__(
        self,
        line: Line,
        layers: LayerCounter,
        *,
        label: str = "stack",
        min_estimates: int = 3,
        fallback_crates: int = 1,
        max_history: int = 60,
    ) -> None:
        self._line, self._layers, self._label = line, layers, label
        self._min, self._fallback, self._max_history = min_estimates, fallback_crates, max_history
        self._last: dict[int, Point] = {}
        self._estimates: dict[int, list[float]] = {}

    def update(self, frame: Frame, tracks: Sequence[Track]) -> list[Crossing]:
        out: list[Crossing] = []
        seen: set[int] = set()
        for t in tracks:
            if t.label != self._label:
                continue
            seen.add(t.track_id)
            estimate = self._layers.estimate(frame.image, t.box)
            if estimate is not None:
                history = self._estimates.setdefault(t.track_id, [])
                history.append(estimate.layers)
                del history[: -self._max_history]

            prev, cur = self._last.get(t.track_id), t.box.center
            if self._line.side(cur) == 0:
                continue
            self._last[t.track_id] = cur
            if prev is None or not self._line.spans(prev, cur):
                continue

            history = self._estimates.get(t.track_id, [])
            trusted = len(history) >= self._min
            crates, agreement = vote_layers(history) if trusted else (self._fallback, None)
            out.append(
                Crossing(
                    camera_id=frame.camera_id,
                    track_id=t.track_id,
                    direction=(
                        CrossDirection.FORWARD
                        if self._line.side(cur) > 0
                        else CrossDirection.BACKWARD
                    ),
                    confidence=t.confidence if trusted else 0.0,
                    at=frame.captured_at,
                    crates=crates,
                    agreement=agreement,
                )
            )
        for gone in self._last.keys() - seen:
            del self._last[gone]
            self._estimates.pop(gone, None)
        return out
