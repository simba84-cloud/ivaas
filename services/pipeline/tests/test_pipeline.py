from datetime import UTC, datetime, timedelta

import numpy as np

from ivaas_pipeline.runner import CameraPipeline, FusingSink
from ivaas_pipeline.stages.counting import Line, LineCrossingCounter
from ivaas_pipeline.stages.fusion import TimeWindowFuser
from ivaas_pipeline.stages.tracking import IouTracker
from ivaas_pipeline.types import Box, CrossDirection, Crossing, Detection, Frame

T0 = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
BLANK = np.zeros((4, 4, 3), dtype=np.uint8)


def frame(i, camera="choke-1"):
    return Frame(camera, BLANK, T0 + timedelta(milliseconds=40 * i))


def crate(x, y=100.0, size=40.0):
    return Detection(Box(x, y, x + size, y + size), "crate", 0.9)


def test_tracker_keeps_identity_across_frames():
    tracker = IouTracker()
    ids = [tracker.update([crate(10 + 8 * i)])[0].track_id for i in range(10)]
    assert set(ids) == {1}


def test_tracker_separates_two_crates():
    tracker = IouTracker()
    tracks = tracker.update([crate(10), crate(300)])
    assert {t.track_id for t in tracks} == {1, 2}


def test_tracker_survives_short_occlusion():
    tracker = IouTracker(max_missed=5)
    first = tracker.update([crate(10)])[0].track_id
    for _ in range(3):
        assert tracker.update([]) == []
    assert tracker.update([crate(14)])[0].track_id == first


def test_line_counter_counts_each_crate_once_with_direction():
    tracker, counter = IouTracker(), LineCrossingCounter(Line((200, 0), (200, 400)))
    crossings = []
    for i in range(30):  # crate slides left -> right across x=200
        f = frame(i)
        crossings += counter.update(f, tracker.update([crate(100 + 8 * i)]))
    assert len(crossings) == 1
    forward = crossings[0].direction
    for i in range(30):  # and is carried back again
        f = frame(30 + i)
        crossings += counter.update(f, tracker.update([crate(332 - 8 * i)]))
    assert len(crossings) == 2
    assert crossings[1].direction is not forward


def test_line_counter_ignores_motion_beyond_segment_ends():
    counter = LineCrossingCounter(Line((200, 0), (200, 50)))
    tracker = IouTracker()
    crossings = []
    for i in range(30):  # passes x=200 but at y~120, outside the 0..50 segment
        crossings += counter.update(frame(i), tracker.update([crate(100 + 8 * i)]))
    assert crossings == []


def test_line_counter_ignores_non_crate_labels():
    counter = LineCrossingCounter(Line((200, 0), (200, 400)))
    tracker = IouTracker()
    crossings = []
    for i in range(30):
        person = Detection(Box(100 + 8 * i, 100, 140 + 8 * i, 200), "person", 0.9)
        crossings += counter.update(frame(i), tracker.update([person]))
    assert crossings == []


def cx(camera, ms, direction=CrossDirection.FORWARD, track=1):
    return Crossing(camera, track, direction, 0.9, T0 + timedelta(milliseconds=ms))


def test_fuser_dedupes_twin_cameras():
    fuser = TimeWindowFuser(timedelta(milliseconds=400))
    out = fuser.fuse([cx("choke-1", 0), cx("choke-2", 120)])
    assert len(out) == 1


def test_fuser_keeps_crate_seen_by_only_one_camera():
    fuser = TimeWindowFuser(timedelta(milliseconds=400))
    assert len(fuser.fuse([cx("choke-2", 0)])) == 1


def test_fuser_keeps_rapid_crates_from_same_camera():
    fuser = TimeWindowFuser(timedelta(milliseconds=400))
    out = fuser.fuse([cx("choke-1", 0, track=1), cx("choke-1", 100, track=2)])
    assert len(out) == 2


def test_fuser_pairs_twins_one_to_one():
    fuser = TimeWindowFuser(timedelta(milliseconds=400))
    out = fuser.fuse(
        [
            cx("choke-1", 0, track=1),
            cx("choke-1", 100, track=2),
            cx("choke-2", 50, track=7),
            cx("choke-2", 150, track=8),
        ]
    )
    assert len(out) == 2


def test_fuser_does_not_merge_opposite_directions():
    fuser = TimeWindowFuser(timedelta(milliseconds=400))
    out = fuser.fuse([cx("choke-1", 0), cx("choke-2", 100, CrossDirection.BACKWARD)])
    assert len(out) == 2


def test_fuser_tolerates_out_of_order_arrival_without_false_merges():
    fuser = TimeWindowFuser(timedelta(milliseconds=400))
    out = fuser.fuse([cx("choke-1", 3000)])
    out += fuser.fuse([cx("choke-2", 0)])  # late, and 3s away: a different crate
    assert len(out) == 2


class Passthrough:
    def process(self, f):
        return f


class ScriptedDetector:
    """Three crates cross x=200, one every 30 frames."""

    def __init__(self):
        self.i = -1

    def detect(self, f):
        self.i += 1
        return [crate(100 + 8 * (self.i % 30))]


class Collect:
    def __init__(self):
        self.items = []

    def emit(self, c):
        self.items.append(c)


def test_runner_end_to_end_single_camera():
    class Source:
        def frames(self):
            return (frame(i) for i in range(90))

    sink = Collect()
    emitted = CameraPipeline(
        Source(),
        Passthrough(),
        ScriptedDetector(),
        IouTracker(max_missed=0),
        LineCrossingCounter(Line((200, 0), (200, 400))),
        sink,
    ).run()
    assert emitted == 3 == len(sink.items)


def test_two_chokepoint_cameras_share_one_fused_count():
    sink = Collect()
    shared = FusingSink(TimeWindowFuser(), sink)
    rigs = {
        cam: (
            ScriptedDetector(),
            IouTracker(max_missed=0),
            LineCrossingCounter(Line((200, 0), (200, 400))),
        )
        for cam in ("choke-1", "choke-2")
    }
    for i in range(90):  # lockstep, as two live cameras would be
        for cam, (detector, tracker, counter) in rigs.items():
            f = frame(i, cam)
            for crossing in counter.update(f, tracker.update(detector.detect(f))):
                shared.emit(crossing)
    assert len(sink.items) == 3  # not 6
