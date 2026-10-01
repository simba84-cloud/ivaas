"""The POC report (proposal M9, days 13-14): the scope's acceptance criteria, measured.

Each criterion is measured from the platform's own records over the POC window and
comes out pass, fail, or not measured, with the figure and how it was got. A criterion
the records cannot support is "not measured" and says why; it is never a pass by
default, and the report as a whole is then incomplete, not passed.

  Accuracy     per-truck AI count against the staff tally sheets (the M0 formula,
               LoadingSession.accuracy), over every load that has one
  Speed        loading cycle time against the baseline Bakers Inn measured before
  Reliability  edge node uptime, and every outage recovered with its backlog drained
  LPR          tally sheets whose load the camera identified by the right plate
  ROI          dispatched, returned and outstanding crates, manifest exceptions and
               corrections; a value only when a crate value is given
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from ivaas.domain.availability import Availability
from ivaas.domain.manifests import Balance, ManifestException, balances, settled, site_tz
from ivaas.domain.models import LoadingSession, SessionDirection
from ivaas.domain.plates import canonical
from ivaas.domain.tally import TallySheet, TallyStatus

Result = Literal["pass", "fail", "not measured"]
UTC_ZONE = ZoneInfo("UTC")
LPR_TARGET = 0.98


@dataclass(frozen=True)
class Criterion:
    name: str
    result: Result
    figure: str  # the headline number, as the report prints it
    target: str
    how: str  # how it was measured, in a sentence
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeUptime:
    name: str
    availability: Availability


@dataclass
class PocReport:
    tenant: str
    site: str
    start: date
    end: date
    timezone: str
    generated_at: datetime
    criteria: list[Criterion]
    loads: list[LoadingSession]
    balance: Balance
    exceptions: Counter
    corrections: int
    correction_crates: int  # count of record minus AI count, summed
    outstanding_value: float | None
    currency: str
    nodes: list[NodeUptime]

    @property
    def verdict(self) -> str:
        results = {c.result for c in self.criteria}
        if "fail" in results:
            return "fail"
        return "incomplete" if "not measured" in results else "pass"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def accuracy(loads: list[LoadingSession], target: float) -> Criterion:
    verified = [s for s in loads if s.accuracy is not None]
    loading = [s for s in loads if settled(s) and s.direction is SessionDirection.LOADING]
    how = "AI count against the staff tally sheet, per truck, on every load that has one"
    if not verified:
        return Criterion(
            "Accuracy",
            "not measured",
            "no tally sheets",
            f"> {_pct(target)}",
            how,
            ["No load in the window was reconciled with a tally sheet."],
        )
    mean = sum(s.accuracy or 0 for s in verified) / len(verified)
    truth = sum(s.manual_count or 0 for s in verified)
    counted = sum(s.ai_count for s in verified)
    passing = sum(1 for s in verified if (s.accuracy or 0) >= target)
    notes = [
        f"{len(verified)} loads verified, {passing} at or above {_pct(target)}.",
        f"Aggregate error {_pct(abs(counted - truth) / truth)} "
        f"({counted:,} counted by the AI, {truth:,} on the sheets)."
        if truth
        else "",
        f"Tally sheets cover {len(verified)} of {len(loading)} completed loading loads."
        if loading
        else "",
        "Accuracy is on the AI count; a person's correction never improves it.",
    ]
    return Criterion(
        "Accuracy",
        "pass" if mean > target else "fail",
        _pct(mean),
        f"> {_pct(target)}",
        how,
        [n for n in notes if n],
    )


def speed(loads: list[LoadingSession], baseline_minutes: float | None) -> Criterion:
    cycles = [
        (s.closed_at - s.opened_at).total_seconds() / 60
        for s in loads
        if s.closed_at and s.direction is SessionDirection.LOADING
    ]
    how = "time from a truck's load opening to it closing, against the baseline"
    latency = (
        "Dashboard latency, under 3 s to screen, is checked on every change by an "
        "automated test (T6.2); it is not instrumented on site."
    )
    if not cycles:
        return Criterion("Speed", "not measured", "no completed loads", "no delay", how, [latency])
    mid, p90 = median(cycles), _percentile(cycles, 0.9)
    notes = [f"{len(cycles)} loads: median {mid:.1f} min, 90% within {p90:.1f} min.", latency]
    if baseline_minutes is None:
        return Criterion(
            "Speed",
            "not measured",
            f"median {mid:.1f} min",
            "no delay against the baseline",
            how,
            ["No baseline cycle time was given, so there is nothing to compare with.", *notes],
        )
    return Criterion(
        "Speed",
        "pass" if mid <= baseline_minutes else "fail",
        f"median {mid:.1f} min",
        f"no slower than the baseline, {baseline_minutes:.1f} min",
        how,
        notes,
    )


def reliability(
    nodes: list[NodeUptime],
    uptime_target: float,
    recorded_from: datetime | None = None,
    tz: ZoneInfo = UTC_ZONE,
) -> Criterion:
    how = "edge node heartbeats: time with none is down; each outage's backlog must drain"
    target = f"uptime >= {_pct(uptime_target)}, every outage recovered with nothing lost"
    if recorded_from is None:
        return Criterion(
            "Reliability",
            "not measured",
            "no heartbeat history",
            target,
            how,
            ["No heartbeat has been recorded yet, so there is no history to measure."],
        )
    if not nodes:
        return Criterion(
            "Reliability",
            "not measured",
            "no edge node",
            target,
            how,
            ["No edge node at this site was working in the window while history was kept."],
        )
    notes = [f"Heartbeat history is kept from {recorded_from.astimezone(tz):%Y-%m-%d %H:%M}."]
    ok = True
    for n in nodes:
        a = n.availability
        up = a.uptime or 0
        lost = [o for o in a.outages if o.drained is not True]
        ok = ok and up >= uptime_target and not lost
        notes.append(
            f"{n.name} ({a.start.astimezone(tz):%d %b %H:%M} to "
            f"{a.end.astimezone(tz):%d %b %H:%M}): up {_pct(up)}, "
            f"{len(a.outages)} outage(s), {a.down_minutes:.0f} min down"
            + (f"; {len(lost)} not shown recovered" if lost else "")
        )
        for o in a.outages[:10]:
            how_back = (
                "still down at the end of the window"
                if o.drained is None
                else f"came back with {o.backlog} queued, drained"
                if o.drained
                else f"came back with {o.backlog} queued, not yet drained"
            )
            notes.append(
                f"  {o.start.astimezone(tz):%Y-%m-%d %H:%M} for {o.minutes:.0f} min: {how_back}"
            )
    worst = min(n.availability.uptime or 0 for n in nodes)
    return Criterion(
        "Reliability", "pass" if ok else "fail", f"{_pct(worst)} uptime", target, how, notes
    )


def lpr(sheets: list[TallySheet], loads: dict[UUID, LoadingSession]) -> Criterion:
    how = "tally sheets: was the load each describes identified by the camera, by its plate"
    linked = [t for t in sheets if t.session_id in loads]
    unmatched = [t for t in sheets if t.status is TallyStatus.UNMATCHED]
    if not linked:
        return Criterion(
            "LPR",
            "not measured",
            "no tally sheets linked",
            f">= {_pct(LPR_TARGET)}",
            how,
            ["No tally sheet was linked to a load, so no plate can be checked."],
        )
    right = wrong = unread = 0
    for t in linked:
        read = loads[t.session_id].plate_read  # the camera's reading, never a correction
        if not read:
            unread += 1
        elif canonical(read) == canonical(t.plate):
            right += 1
        else:
            wrong += 1
    rate = right / len(linked)
    notes = [
        f"{len(linked)} sheets linked to a load: plate read right {right}, read differently "
        f"{wrong}, not read {unread}.",
        "Measured on what the camera read, before any person identified a truck.",
    ]
    if unmatched:
        notes.append(
            f"{len(unmatched)} sheet(s) found no load (wrong plate, time or bay): not in "
            "the rate, and worth a look."
        )
    return Criterion(
        "LPR",
        "pass" if rate >= LPR_TARGET else "fail",
        _pct(rate),
        f">= {_pct(LPR_TARGET)}",
        how,
        notes,
    )


def build_poc(
    *,
    tenant: str,
    site: str,
    start: date,
    end: date,
    timezone: str,
    now: datetime,
    loads: list[LoadingSession],
    sheets: list[TallySheet],
    exceptions: list[ManifestException],
    nodes: list[NodeUptime],
    recorded_from: datetime | None,
    target: float,
    uptime_target: float,
    baseline_minutes: float | None,
    crate_value: float | None,
    currency: str,
) -> PocReport:
    loads = sorted(loads, key=lambda s: s.opened_at)
    by_id = {s.id: s for s in loads}
    [total] = balances(loads, lambda s: "all") or [Balance("all")]
    corrected = [s for s in loads if s.override_count is not None]
    return PocReport(
        tenant=tenant,
        site=site,
        start=start,
        end=end,
        timezone=timezone,
        generated_at=now,
        criteria=[
            accuracy(loads, target),
            speed(loads, baseline_minutes),
            reliability(nodes, uptime_target, recorded_from, site_tz(timezone)),
            lpr(sheets, by_id),
        ],
        loads=loads,
        balance=total,
        exceptions=Counter((e.kind.value, e.status.value) for e in exceptions),
        corrections=len(corrected),
        correction_crates=sum(s.count_of_record - s.ai_count for s in corrected),
        outstanding_value=None if crate_value is None else total.outstanding * crate_value,
        currency=currency,
        nodes=nodes,
    )
