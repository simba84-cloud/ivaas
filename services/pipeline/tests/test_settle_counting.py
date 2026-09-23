from datetime import UTC, datetime, timedelta

import numpy as np

from ivaas_pipeline.stages.layers import LayerEstimate
from ivaas_pipeline.stages.settle_counting import StackSettleCounter
from ivaas_pipeline.types import Box, Frame, Track

T0 = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
BLANK = np.zeros((4, 4, 3), np.uint8)


class Layers:
    def estimate(self, image, box):
        return LayerEstimate(14.0, 50, 0.5)


def frame(i):
    return Frame("cc2", BLANK, T0 + timedelta(milliseconds=100 * i))


def track(tid, x, y=100):
    return Track(tid, Box(x, y, x + 120, y + 600), "stack", 0.9)


def test_stack_set_down_is_counted_once_with_its_layers():
    c = StackSettleCounter(Layers(), settle_seconds=3.0, settle_px=40)
    out = []
    for i in range(30):  # wheeled in over 3 s: moving, not counted
        out += c.update(frame(i), [track(1, 900 - 25 * i)])
    assert out == []
    for i in range(30, 100):  # set down, jittering a few px
        out += c.update(frame(i), [track(1, 150 + (i % 3) * 4)])
    assert [(x.track_id, x.crates) for x in out] == [(1, 14)]


def test_stack_wheeled_straight_past_is_never_counted():
    c = StackSettleCounter(Layers(), settle_seconds=3.0)
    out = []
    for i in range(80):
        out += c.update(frame(i), [track(1, 20 * i)])
    assert out == []


def test_two_stacks_settled_are_two_counts():
    c = StackSettleCounter(Layers(), settle_seconds=1.0)
    out = []
    for i in range(30):
        out += c.update(frame(i), [track(1, 100), track(2, 400)])
    assert sorted(x.track_id for x in out) == [1, 2]


def test_nudged_stack_is_not_counted_twice():
    c = StackSettleCounter(Layers(), settle_seconds=1.0)
    out = []
    for i in range(20):
        out += c.update(frame(i), [track(1, 100)])
    for i in range(20, 60):  # moved 200 px and left again for 4 s
        out += c.update(frame(i), [track(1, 300)])
    assert len(out) == 1
