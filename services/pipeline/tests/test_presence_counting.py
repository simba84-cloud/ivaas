from datetime import UTC, datetime, timedelta

import numpy as np

from ivaas_pipeline.stages.layers import LayerEstimate
from ivaas_pipeline.stages.presence_counting import PresenceZone, StackPresenceCounter
from ivaas_pipeline.types import Box, Frame

T0 = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
BLANK = np.zeros((4, 4, 3), np.uint8)
ZONE = PresenceZone(0, 0, 500, 1000)


class Layers:
    def __init__(self, v=14.0):
        self.v = v

    def estimate(self, image, box):
        return LayerEstimate(self.v, 50, 0.5)


def frame(i):
    return Frame("cc2", BLANK, T0 + timedelta(milliseconds=100 * i))


def track(tid, x):
    from ivaas_pipeline.types import Track

    return Track(tid, Box(x, 100, x + 120, 700), "stack", 0.9)


def test_stack_that_stays_is_counted_once_with_median_layers():
    c = StackPresenceCounter(ZONE, Layers(14.0), min_seconds=2.0)
    out = []
    for i in range(60):  # 6 s inside the zone, jittering
        out += c.update(frame(i), [track(1, 200 + (i % 3) * 5)])
    assert [(x.track_id, x.crates) for x in out] == [(1, 14)]


def test_stack_that_leaves_quickly_is_not_counted():
    c = StackPresenceCounter(ZONE, Layers(), min_seconds=2.0)
    out = []
    for i in range(10):  # 1 s inside
        out += c.update(frame(i), [track(1, 200)])
    for i in range(10, 40):  # then outside the zone
        out += c.update(frame(i), [track(1, 900)])
    assert out == []


def test_two_stacks_are_two_counts():
    c = StackPresenceCounter(ZONE, Layers(), min_seconds=1.0)
    out = []
    for i in range(30):
        out += c.update(frame(i), [track(1, 100), track(2, 350)])
    assert sorted(x.track_id for x in out) == [1, 2]


def test_too_few_layer_estimates_is_flagged_not_guessed():
    class Nothing:
        def estimate(self, image, box):
            return None

    c = StackPresenceCounter(ZONE, Nothing(), min_seconds=1.0)
    out = []
    for i in range(20):
        out += c.update(frame(i), [track(1, 200)])
    assert len(out) == 1 and out[0].crates == 1 and out[0].confidence == 0.0


class Cycling:
    def __init__(self, values):
        self.values, self.i = values, 0

    def estimate(self, image, box):
        v = self.values[self.i % len(self.values)] if self.values else None
        self.i += 1
        return None if v is None else LayerEstimate(v, 50, 0.5)


def _count_one(layers):
    c = StackPresenceCounter(ZONE, layers, min_seconds=1.0)
    out = []
    for i in range(20):
        out += c.update(frame(i), [track(1, 200)])
    assert len(out) == 1
    return out[0]


def test_unanimous_frames_agree_fully():
    assert _count_one(Layers(14.0)).agreement == 1.0


def test_split_frames_lower_the_agreement():
    # counted at frame 10, after 11 estimates: eight 14s and three 13s
    x = _count_one(Cycling([14.0, 14.0, 13.0]))
    assert x.crates == 14 and x.agreement == 8 / 11


def test_fallback_count_has_no_agreement():
    x = _count_one(Cycling([]))
    assert x.confidence == 0.0 and x.agreement is None
