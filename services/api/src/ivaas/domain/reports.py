"""The daily report: one site's day, as Bakers Inn reads it (proposal M6).

The POC runs on these reports and the portal, not on an ERP integration, so the
report carries the figures that matter: what went out and came back, how the AI
count compared with the tally sheets, what disagreed with the manifests, and what
people corrected. Every figure is computed from the same records the portal shows,
with the same formulas (accuracy is `LoadingSession.accuracy`, on the AI count).

A figure the day cannot support is stated as missing, never as zero: a day with no
reconciled tally sheets has no accuracy, not an accuracy of 0%.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ivaas.domain.manifests import ExceptionKind, ManifestException, settled
from ivaas.domain.models import LoadingSession, SessionDirection


@dataclass(frozen=True)
class LoadRow:
    opened: str  # local HH:MM
    closed: str
    plate: str
    identified: str
    direction: str
    ai_count: int
    corrected: int | None
    count_of_record: int
    tally: int | None
    accuracy: float | None
    status: str


@dataclass
class DailyReport:
    tenant: str
    site: str
    day: date
    timezone: str
    generated_at: datetime
    target: float
    loads: list[LoadRow] = field(default_factory=list)
    dispatched: int = 0
    returned: int = 0
    in_progress: int = 0
    unidentified: int = 0
    #: loads with a reconciled tally sheet, and the accuracy over them
    verified: int = 0
    passing: int = 0
    mean_accuracy: float | None = None
    aggregate_error: float | None = None
    exceptions: list[ManifestException] = field(default_factory=list)
    corrections: list[LoadRow] = field(default_factory=list)

    @property
    def outstanding(self) -> int:
        return self.dispatched - self.returned


EXCEPTION_LABELS = {
    ExceptionKind.COUNT_MISMATCH: "Count differs from manifest",
    ExceptionKind.NOT_SEEN: "Manifest truck never came",
    ExceptionKind.UNEXPECTED: "Load not on any manifest",
}


def _hm(moment: datetime | None, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).strftime("%H:%M") if moment else ""


def build_daily(
    *,
    tenant: str,
    site: str,
    day: date,
    tz: ZoneInfo,
    sessions: list[LoadingSession],
    exceptions: list[ManifestException],
    identified: dict,
    target: float,
    now: datetime,
) -> DailyReport:
    """`sessions` are the site's loads opened that local day; `identified` maps a
    session id to how its truck is known (registered, unregistered, ...)."""
    report = DailyReport(
        tenant=tenant,
        site=site,
        day=day,
        timezone=str(tz),
        generated_at=now,
        target=target,
        exceptions=sorted(exceptions, key=lambda e: (e.kind.value, e.plate or "")),
    )
    ai_sum = truth_sum = 0
    scored: list[float] = []
    for s in sorted(sessions, key=lambda x: x.opened_at):
        row = LoadRow(
            opened=_hm(s.opened_at, tz),
            closed=_hm(s.closed_at, tz),
            plate=s.plate or "",
            identified=str(identified.get(s.id, "")),
            direction=s.direction.value,
            ai_count=s.ai_count,
            corrected=s.override_count,
            count_of_record=s.count_of_record,
            tally=s.manual_count,
            accuracy=s.accuracy,
            status=s.status.value,
        )
        report.loads.append(row)
        if not s.plate:
            report.unidentified += 1
        if s.override_count is not None:
            report.corrections.append(row)
        if not settled(s):
            report.in_progress += 1
            continue
        if s.direction is SessionDirection.LOADING:
            report.dispatched += s.count_of_record
        else:
            report.returned += s.count_of_record
        if s.accuracy is not None:
            scored.append(s.accuracy)
            ai_sum += s.ai_count
            truth_sum += s.manual_count or 0
    report.verified = len(scored)
    report.passing = sum(1 for a in scored if a >= target)
    report.mean_accuracy = sum(scored) / len(scored) if scored else None
    report.aggregate_error = abs(ai_sum - truth_sum) / truth_sum if truth_sum else None
    return report
