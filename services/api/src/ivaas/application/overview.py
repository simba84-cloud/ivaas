"""The operations overview behind the dashboard.

Every figure here is derived from stored sessions and camera state: the daily
series that the sparklines draw, the period-on-period deltas, and the insights.
Nothing is estimated or forecast — an operator acts on this screen, so a number
it cannot substantiate does not belong on it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from ivaas.domain.models import CameraStatus, LoadingSession, SessionStatus
from ivaas.ports.repositories import BayReader, CameraReader, Clock, SessionReader

TARGET_ACCURACY = 0.95
MAX_DAYS = 90
MAX_SESSIONS = 5000


class Severity(StrEnum):
    """How loudly an insight asks for attention."""

    GOOD = "good"
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Insight:
    key: str
    severity: Severity
    title: str
    detail: str
    metric: str | None = None


@dataclass(frozen=True)
class DayPoint:
    day: date
    crates: int
    sessions: int
    accuracy: float | None


@dataclass(frozen=True)
class Trend:
    """A value with the sparkline behind it and its change on the prior period.

    A None in `series` means "not measured that day", which is not the same as zero:
    accuracy on a day with no verified load is unknown, and a sparkline that drew it
    as 0 would show a collapse that never happened.
    """

    value: float | None
    delta_pct: float | None
    series: list[float | None] = field(default_factory=list)


@dataclass(frozen=True)
class Overview:
    generated_at: datetime
    days: int
    crates_today: int
    sessions_today: int
    open_sessions: int
    verified_sessions: int
    unverified_sessions: int
    mean_accuracy: float | None
    cameras_online: int
    cameras_total: int
    crates: Trend
    throughput: Trend
    accuracy: Trend
    daily: list[DayPoint]
    insights: list[Insight]


def _pct_change(current: float | None, prior: float | None) -> float | None:
    """Change as a fraction. Undefined against a zero or missing baseline."""
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / prior


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass
class OperationsOverview:
    sessions: SessionReader
    cameras: CameraReader
    bays: BayReader
    clock: Clock

    async def __call__(self, days: int = 14) -> Overview:
        days = max(2, min(MAX_DAYS, days))
        now = self.clock.now()
        today = now.date()
        since = datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=now.tzinfo)
        rows = await self.sessions.list_recent(since=since, limit=MAX_SESSIONS)
        cams = [
            c for b in await self.bays.list_all() for c in await self.cameras.list_for_bay(b.id)
        ]

        daily = self._daily(rows, today, days)
        todays = [s for s in rows if s.opened_at.date() == today]
        verified = [s for s in rows if s.accuracy is not None]

        # Compare the most recent half of the window with the half before it, so a
        # delta means "this week against last week" rather than one day's noise.
        half = days // 2
        recent, prior = daily[-half:], daily[-days:-half]

        return Overview(
            generated_at=now,
            days=days,
            crates_today=sum(s.ai_count for s in todays),
            sessions_today=len(todays),
            open_sessions=sum(1 for s in rows if s.status is SessionStatus.OPEN),
            verified_sessions=len(verified),
            unverified_sessions=sum(1 for s in rows if s.status is SessionStatus.CLOSED),
            mean_accuracy=_mean([s.accuracy or 0 for s in verified]),
            cameras_online=sum(1 for c in cams if c.status is CameraStatus.ONLINE),
            cameras_total=len(cams),
            crates=self._trend([float(d.crates) for d in daily], recent, prior, "crates"),
            throughput=self._trend([float(d.sessions) for d in daily], recent, prior, "sessions"),
            accuracy=self._accuracy_trend(daily, recent, prior),
            daily=daily,
            insights=self._insights(rows, verified, cams, recent, prior),
        )

    def _daily(self, rows: list[LoadingSession], today: date, days: int) -> list[DayPoint]:
        """One point per day, including the days on which nothing moved."""
        agg: dict[date, dict[str, float]] = defaultdict(
            lambda: {"crates": 0, "sessions": 0, "acc_sum": 0.0, "acc_n": 0}
        )
        for s in rows:
            a = agg[s.opened_at.date()]
            a["crates"] += s.ai_count
            a["sessions"] += 1
            if s.accuracy is not None:
                a["acc_sum"] += s.accuracy
                a["acc_n"] += 1
        points = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            a = agg.get(day)
            points.append(
                DayPoint(
                    day=day,
                    crates=int(a["crates"]) if a else 0,
                    sessions=int(a["sessions"]) if a else 0,
                    accuracy=(a["acc_sum"] / a["acc_n"]) if a and a["acc_n"] else None,
                )
            )
        return points

    def _trend(
        self,
        series: list[float | None],
        recent: list[DayPoint],
        prior: list[DayPoint],
        field_: str,
    ) -> Trend:
        take = (lambda d: float(d.crates)) if field_ == "crates" else (lambda d: float(d.sessions))
        return Trend(
            value=sum(v for v in series if v is not None),
            delta_pct=_pct_change(
                _mean([take(d) for d in recent]), _mean([take(d) for d in prior])
            ),
            series=series,
        )

    def _accuracy_trend(
        self, daily: list[DayPoint], recent: list[DayPoint], prior: list[DayPoint]
    ) -> Trend:
        verified = [d.accuracy for d in daily if d.accuracy is not None]
        return Trend(
            value=_mean(verified),
            # Accuracy is already a ratio, so report its change in points, not percent.
            delta_pct=(
                None
                if (r := _mean([d.accuracy for d in recent if d.accuracy is not None])) is None
                or (p := _mean([d.accuracy for d in prior if d.accuracy is not None])) is None
                else r - p
            ),
            # days with no verified load stay None: unknown, not zero
            series=[d.accuracy for d in daily],
        )

    def _insights(
        self,
        rows: list[LoadingSession],
        verified: list[LoadingSession],
        cams: list,
        recent: list[DayPoint],
        prior: list[DayPoint],
    ) -> list[Insight]:
        out: list[Insight] = []

        offline = [c for c in cams if c.status is not CameraStatus.ONLINE]
        if offline and cams:
            names = ", ".join(c.name for c in offline[:3])
            more = f" and {len(offline) - 3} more" if len(offline) > 3 else ""
            out.append(
                Insight(
                    "cameras_offline",
                    Severity.CRITICAL if len(offline) == len(cams) else Severity.WARN,
                    f"{len(offline)} of {len(cams)} cameras are not streaming",
                    f"{names}{more}. Crates passing an offline camera are not counted.",
                    metric=f"{len(cams) - len(offline)}/{len(cams)}",
                )
            )

        awaiting = [s for s in rows if s.status is SessionStatus.CLOSED]
        if awaiting:
            plural = "s" if len(awaiting) > 1 else ""
            out.append(
                Insight(
                    "awaiting_verification",
                    Severity.INFO,
                    f"{len(awaiting)} load{plural} awaiting a manual count",
                    "Accuracy cannot be measured until these are verified on the "
                    "reconciliation screen.",
                    metric=str(len(awaiting)),
                )
            )

        disputed = [s for s in rows if s.status is SessionStatus.DISPUTED]
        if disputed:
            net = sum(s.variance or 0 for s in disputed)
            out.append(
                Insight(
                    "disputed",
                    Severity.WARN,
                    f"{len(disputed)} load{'s' if len(disputed) > 1 else ''} disputed",
                    f"Net variance {net:+d} crates against the manual count. "
                    "Review the footage for these loads.",
                    metric=f"{net:+d}",
                )
            )

        if verified:
            mean = _mean([s.accuracy or 0 for s in verified]) or 0
            below = [s for s in verified if (s.accuracy or 0) < TARGET_ACCURACY]
            if mean >= TARGET_ACCURACY:
                out.append(
                    Insight(
                        "accuracy_on_target",
                        Severity.GOOD,
                        "Counting accuracy is meeting the 95% target",
                        f"{mean * 100:.1f}% across {len(verified)} verified "
                        f"load{'s' if len(verified) > 1 else ''}.",
                        metric=f"{mean * 100:.1f}%",
                    )
                )
            else:
                out.append(
                    Insight(
                        "accuracy_below_target",
                        Severity.CRITICAL,
                        f"Counting accuracy is {mean * 100:.1f}%, below the 95% target",
                        f"{len(below)} of {len(verified)} verified loads fell short. "
                        "Check camera placement and lighting at the bay.",
                        metric=f"{mean * 100:.1f}%",
                    )
                )

        unplated = [s for s in rows if s.plate is None and s.status is not SessionStatus.OPEN]
        if unplated:
            plural = "s" if len(unplated) > 1 else ""
            out.append(
                Insight(
                    "missing_plates",
                    Severity.WARN,
                    f"{len(unplated)} load{plural} without a number plate",
                    "The LPR camera did not read a plate, so these loads cannot be "
                    "attributed to a truck.",
                    metric=str(len(unplated)),
                )
            )

        r, p = _mean([float(d.crates) for d in recent]), _mean([float(d.crates) for d in prior])
        change = _pct_change(r, p)
        if change is not None and abs(change) >= 0.15:
            rising = change > 0
            out.append(
                Insight(
                    "throughput_shift",
                    Severity.INFO,
                    f"Crate throughput is {'up' if rising else 'down'} "
                    f"{abs(change) * 100:.0f}% on the previous period",
                    f"{r:.0f} crates a day against {p:.0f} over the period before it.",
                    metric=f"{change * 100:+.0f}%",
                )
            )

        order = {Severity.CRITICAL: 0, Severity.WARN: 1, Severity.INFO: 2, Severity.GOOD: 3}
        return sorted(out, key=lambda i: order[i.severity])
