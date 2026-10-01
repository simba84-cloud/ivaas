"""Edge availability: heartbeats become periods; periods become uptime and outages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ivaas.domain.availability import GAP, availability, observe

T0 = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
NODE = uuid4()


def beats(*spans, every=30, up=True, spools=None):
    """Heartbeats every `every` s over each (start_min, end_min) span -> periods."""
    periods, last = [], None
    for a, b in spans:
        t = T0 + timedelta(minutes=a)
        while t <= T0 + timedelta(minutes=b):
            spool = spools(t) if spools else 0
            p = observe(last, node_id=NODE, camera_id=None, up=up, at=t, spool=spool)
            if p is not last:
                periods.append(p)
            last = p
            t += timedelta(seconds=every)
    return periods


def test_steady_heartbeats_are_one_period_and_full_uptime():
    periods = beats((0, 60))
    assert len(periods) == 1
    a = availability(periods, T0, T0 + timedelta(minutes=60))
    assert a.uptime == pytest.approx(1.0) and a.outages == []


def test_a_silence_longer_than_the_gap_is_an_outage_and_a_new_period():
    periods = beats((0, 30), (40, 60))
    assert len(periods) == 2
    a = availability(periods, T0, T0 + timedelta(minutes=60))
    [out] = a.outages
    assert out.start == T0 + timedelta(minutes=30) and out.end == T0 + timedelta(minutes=40)
    assert out.minutes == 10 and a.uptime == pytest.approx(50 / 60)


def test_a_late_first_heartbeat_within_the_gap_is_not_an_outage():
    periods = beats((1, 60))  # the window opens a minute before the first beat
    a = availability(
        periods, T0 + timedelta(seconds=60) - GAP + timedelta(seconds=1), T0 + timedelta(minutes=60)
    )
    assert a.outages == []


def test_never_heard_from_is_down_not_up():
    a = availability([], T0, T0 + timedelta(hours=1))
    assert a.uptime == 0 and len(a.outages) == 1 and a.outages[0].minutes == 60


def test_an_outage_says_whether_its_backlog_drained():
    # the node spooled during the outage, came back with 40 queued, and sent them
    periods = beats(
        (0, 10), (20, 30), spools=lambda t: 40 if t == T0 + timedelta(minutes=20) else 0
    )
    [out] = availability(periods, T0, T0 + timedelta(minutes=30)).outages
    assert (out.backlog, out.drained) == (40, True)


def test_a_change_of_state_opens_a_new_period():
    first = observe(None, node_id=NODE, camera_id=uuid4(), up=True, at=T0)
    same = observe(
        first, node_id=NODE, camera_id=first.camera_id, up=True, at=T0 + timedelta(seconds=30)
    )
    assert same is first and first.until == T0 + timedelta(seconds=30)
    down = observe(
        first, node_id=NODE, camera_id=first.camera_id, up=False, at=T0 + timedelta(seconds=60)
    )
    assert down is not first and not down.up


def test_a_window_clips_the_periods_it_overlaps():
    periods = beats((0, 120))
    a = availability(periods, T0 + timedelta(minutes=30), T0 + timedelta(minutes=90))
    assert a.uptime == pytest.approx(1.0)
    assert availability(periods, T0, T0).uptime is None
