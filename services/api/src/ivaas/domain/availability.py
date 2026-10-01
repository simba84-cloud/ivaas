"""How long each edge node and camera was working, over any window (POC: reliability).

Only the latest heartbeat is kept on the node record, so this keeps the history: a
run of heartbeats in one state becomes one period, extended while they keep coming.

- A node is up while it sends heartbeats. Time with none is down: nothing was heard.
- A camera is up while its node reports it connected, and down while its node reports
  it not. While the node is silent nobody can vouch for the camera, so that is down too.

An outage is a stretch longer than the heartbeat gap with no up period: between two,
or at either end of the window. Uptime is the rest of the window, so the two always
agree. Each period keeps the node's spool backlog, so an outage can
be shown to have been recovered with nothing lost: the backlog it built drained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

#: heartbeats come every 30 s; further apart than this and the gap between was down.
#: The same threshold the fleet view uses for "online".
GAP = timedelta(seconds=90)


@dataclass
class Period:
    node_id: UUID
    camera_id: UUID | None  # None: the node itself
    up: bool
    since: datetime
    until: datetime  # the last heartbeat that confirmed this state
    peak_spool: int = 0
    last_spool: int = 0
    id: UUID = field(default_factory=uuid4)


def observe(
    last: Period | None,
    *,
    node_id: UUID,
    camera_id: UUID | None,
    up: bool,
    at: datetime,
    spool: int = 0,
) -> Period:
    """The period this heartbeat belongs to: the last one, extended, or a new one."""
    if last is not None and last.up == up and timedelta(0) <= at - last.until <= GAP:
        last.until = at
        last.peak_spool = max(last.peak_spool, spool)
        last.last_spool = spool
        return last
    return Period(node_id, camera_id, up, at, at, peak_spool=spool, last_spool=spool)


@dataclass(frozen=True)
class Outage:
    start: datetime  # the last heartbeat before it, or the window's start
    end: datetime  # the first heartbeat after it, or the window's end
    #: the node's spool when it came back, at its highest, and whether it drained
    backlog: int | None
    drained: bool | None

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass(frozen=True)
class Availability:
    start: datetime
    end: datetime
    uptime: float | None  # 0..1; None when the window is empty
    outages: list[Outage]

    @property
    def down_minutes(self) -> float:
        return sum(o.minutes for o in self.outages)


def availability(periods: list[Period], start: datetime, end: datetime) -> Availability:
    """Uptime and outages of one subject (a node, or one camera) over [start, end)."""
    if end <= start:
        return Availability(start, end, None, [])
    ups = sorted(
        (p for p in periods if p.up and p.until >= start and p.since < end), key=lambda p: p.since
    )
    outages: list[Outage] = []
    cursor = start
    for p in ups:
        if p.since - cursor > GAP:
            outages.append(Outage(cursor, min(p.since, end), p.peak_spool, p.last_spool == 0))
        cursor = max(cursor, p.until)
    if end - cursor > GAP:
        outages.append(Outage(cursor, end, None, None))
    # up is everything that is not an outage: a subject heard from within the gap is
    # working, as the fleet view's "online" says, so uptime and outages always agree
    down = sum((o.end - o.start for o in outages), timedelta(0))
    return Availability(start, end, 1 - down / (end - start), outages)
