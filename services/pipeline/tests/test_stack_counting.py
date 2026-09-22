from datetime import UTC, datetime, timedelta

import numpy as np

from ivaas_pipeline.stages.counting import Line
from ivaas_pipeline.stages.layers import LayerEstimate
from ivaas_pipeline.stages.stack_counting import StackCrossingCounter
from ivaas_pipeline.stages.tracking import IouTracker
from ivaas_pipeline.types import Box, CrossDirection, Detection, Frame

T0 = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
BLANK = np.zeros((4, 4, 3), np.uint8)


class ScriptedLayers:
    def __init__(self, values):
        self.values = list(values)

    def estimate(self, image, box):
        v = self.values.pop(0) if self.values else None
        return None if v is None else LayerEstimate(v, 50, 0.5)


def stack(x):
    return Detection(Box(x, 100, x + 120, 700), "stack", 0.9)


def drive(counter, xs, label_fn=stack):
    tracker, out = IouTracker(), []
    for i, x in enumerate(xs):
        f = Frame("choke-1", BLANK, T0 + timedelta(milliseconds=50 * i))
        out += counter.update(f, tracker.update([label_fn(x)]))
    return out


LINE = Line((400, 0), (400, 800))
XS = [200 + 20 * i for i in range(15)]  # centre crosses x=400 around frame 7


def test_one_stack_is_one_crossing_with_the_median_layer_count():
    # one wild frame (31) and one missing estimate must not move the answer off 14
    layers = ScriptedLayers([14.2, 13.8, 31.0, None, 14.1, 13.9, 14.4, 14.0] + [14.0] * 10)
    out = drive(StackCrossingCounter(LINE, layers), XS)
    # moving +x across a line drawn top-to-bottom is BACKWARD by the signed-area convention;
    # which physical direction that means is per-camera config (`forward_means`)
    assert [(c.crates, c.direction) for c in out] == [(14, CrossDirection.BACKWARD)]
    assert out[0].confidence == 0.9


def test_stack_with_too_few_estimates_is_flagged_not_guessed():
    out = drive(StackCrossingCounter(LINE, ScriptedLayers([None] * 30), fallback_crates=1), XS)
    assert len(out) == 1
    assert out[0].crates == 1 and out[0].confidence == 0.0  # reviewable, not silently wrong


def test_direction_reverses_when_the_stack_comes_back():
    counter = StackCrossingCounter(LINE, ScriptedLayers([12.0] * 100))
    out = drive(counter, XS + XS[::-1])
    assert len(out) == 2 and out[0].direction is not out[1].direction
    assert [c.crates for c in out] == [12, 12]


def test_other_labels_are_ignored():
    person = lambda x: Detection(Box(x, 100, x + 120, 700), "person", 0.9)
    assert drive(StackCrossingCounter(LINE, ScriptedLayers([12.0] * 100)), XS, person) == []


def test_state_is_dropped_with_the_track():
    counter = StackCrossingCounter(LINE, ScriptedLayers([12.0] * 100))
    drive(counter, XS)
    counter.update(Frame("choke-1", BLANK, T0), [])
    assert counter._last == {} and counter._estimates == {}
