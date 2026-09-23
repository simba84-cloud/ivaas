from datetime import UTC, datetime, timedelta
from pathlib import Path

from ivaas_pipeline.batch import clip_entries, group_loads
from ivaas_pipeline.types import CrossDirection, Crossing

T0 = datetime(2000, 1, 1, tzinfo=UTC)


def cx(seconds, crates=14, conf=0.9):
    return Crossing(
        "c", int(seconds), CrossDirection.FORWARD, conf, T0 + timedelta(seconds=seconds), crates
    )


def test_loads_split_on_idle_gap_and_sum_crates():
    loads = group_loads([cx(10), cx(40, 8), cx(300), cx(330, 7, conf=0.0)], idle_seconds=120)
    assert [(ld["stacks"], ld["crates"], ld["low_confidence"]) for ld in loads] == [
        (2, 22, 0),
        (2, 21, 1),
    ]
    assert loads[0]["start_s"] == 10 and loads[0]["end_s"] == 40


def test_out_of_order_crossings_are_sorted_first():
    loads = group_loads([cx(40), cx(10)], idle_seconds=120)
    assert len(loads) == 1 and loads[0]["start_s"] == 10


def test_empty():
    assert group_loads([], 120) == []


def test_clip_entries_accept_paths_and_windows():
    assert clip_entries(["/a.mp4", {"path": "/b.mp4", "window": [10, 20]}, {"path": "/c.mp4"}]) == [
        (Path("/a.mp4"), None),
        (Path("/b.mp4"), (10, 20)),
        (Path("/c.mp4"), None),
    ]
