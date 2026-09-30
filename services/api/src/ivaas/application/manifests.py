"""Match manifests to loads, raise exceptions, and work out balances (M5, T5.3-T5.4).

Run after every import and on the idle sweep, so a load that closes, a plate an
operator fills in, or a count a person corrects is reflected without anyone asking.
Running it twice raises nothing twice: each exception is keyed to its manifest line
or its load, and one whose cause has gone away closes itself, saying so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from ivaas.domain.manifests import (
    ExceptionKind,
    ExceptionStatus,
    LineStatus,
    ManifestException,
    ManifestLine,
    local_day,
    match_line,
    settled,
    site_tz,
)
from ivaas.domain.models import LoadingSession

SUBJECT_EXCEPTION = "ivaas.exception.raised"
SYSTEM = "system"


def exception_payload(e: ManifestException) -> dict:
    return {
        "id": str(e.id),
        "kind": e.kind.value,
        "day": e.day.isoformat(),
        "plate": e.plate,
        "route": e.route,
        "expected": e.expected,
        "counted": e.counted,
        "session_id": str(e.session_id) if e.session_id else None,
    }


@dataclass
class ReconcileManifests:
    lines: Any
    exceptions: Any
    sessions: Any
    bays: Any
    sites: Any
    events: Any
    clock: Any
    #: a difference of this many crates or fewer is not an exception
    tolerance: int = 0
    lookback: timedelta = timedelta(days=14)

    async def __call__(self) -> list[ManifestException]:
        """-> the exceptions raised this run."""
        now = self.clock.now()
        recent = await self.sessions.list_recent(since=now - self.lookback, limit=5000)
        by_id = {s.id: s for s in recent}
        site_of = {b.id: b.site_id for b in await self.bays.list_all()}
        tz_of = {s.id: site_tz(s.timezone) for s in await self.sites.list_all()}
        lines: list[ManifestLine] = await self.lines.since(
            (now - self.lookback).date() - timedelta(days=1)
        )
        taken = {line.session_id for line in lines if line.session_id}
        raised: list[ManifestException] = []

        for line in sorted(lines, key=lambda x: (x.imported_at or now, x.reference)):
            tz = tz_of.get(line.site_id, ZoneInfo("UTC"))
            if line.session_id is None:
                at_site = [s for s in recent if site_of.get(s.bay_id) == line.site_id]
                found = match_line(line, at_site, tz=tz, taken=taken)
                if found is not None:
                    line.session_id, line.status = found.id, LineStatus.MATCHED
                    taken.add(found.id)
                    await self.lines.save(line)
                    await self._close(
                        line.id, ExceptionKind.NOT_SEEN, now, "the truck was counted after all"
                    )
                elif line.status is LineStatus.PENDING and line.day < local_day(now, tz):
                    line.status = LineStatus.NOT_SEEN
                    await self.lines.save(line)
                    raised += await self._raise(
                        ManifestException(
                            kind=ExceptionKind.NOT_SEEN,
                            day=line.day,
                            raised_at=now,
                            plate=line.plate,
                            route=line.route,
                            line_id=line.id,
                            expected=line.expected,
                        )
                    )
            session = by_id.get(line.session_id) if line.session_id else None
            if session is not None and settled(session):
                raised += await self._compare(line, session, now)

        # A load no manifest expected, once its day is over at a site that had manifests
        # that day (a site that sends no manifests has nothing to be unexpected against).
        manifested = {(line.site_id, line.day) for line in lines}
        for s in recent:
            site = site_of.get(s.bay_id)
            if site is None or not settled(s) or s.id in taken:
                continue
            day = local_day(s.opened_at, tz_of.get(site, ZoneInfo("UTC")))
            if (site, day) not in manifested or day >= local_day(now, tz_of[site]):
                continue
            if await self.exceptions.find(session_id=s.id, kind=ExceptionKind.UNEXPECTED):
                continue
            raised += await self._raise(
                ManifestException(
                    kind=ExceptionKind.UNEXPECTED,
                    day=day,
                    raised_at=now,
                    plate=s.plate,
                    session_id=s.id,
                    counted=s.count_of_record,
                )
            )
        return raised

    async def _compare(
        self, line: ManifestLine, session: LoadingSession, now: datetime
    ) -> list[ManifestException]:
        counted = session.count_of_record
        existing = await self.exceptions.find(line_id=line.id, kind=ExceptionKind.COUNT_MISMATCH)
        if abs(counted - line.expected) > self.tolerance:
            if existing is None:
                return await self._raise(
                    ManifestException(
                        kind=ExceptionKind.COUNT_MISMATCH,
                        day=line.day,
                        raised_at=now,
                        plate=session.plate or line.plate,
                        route=line.route,
                        session_id=session.id,
                        line_id=line.id,
                        expected=line.expected,
                        counted=counted,
                    )
                )
            if existing.status is ExceptionStatus.OPEN and existing.counted != counted:
                existing.counted = counted  # a correction since: show the figure as it is now
                await self.exceptions.save(existing)
        elif existing is not None and existing.status is ExceptionStatus.OPEN:
            existing.resolve(SYSTEM, now, "the count now agrees with the manifest")
            await self.exceptions.save(existing)
        return []

    async def _close(self, line_id: UUID, kind: ExceptionKind, now: datetime, why: str) -> None:
        existing = await self.exceptions.find(line_id=line_id, kind=kind)
        if existing is not None and existing.status is ExceptionStatus.OPEN:
            existing.resolve(SYSTEM, now, why)
            await self.exceptions.save(existing)

    async def _raise(self, e: ManifestException) -> list[ManifestException]:
        await self.exceptions.save(e)
        await self.events.publish(SUBJECT_EXCEPTION, exception_payload(e))
        return [e]


def day_window(days: int, now: datetime) -> tuple[date, datetime]:
    """The first day of a `days`-long window ending today, and the moment it starts."""
    start = now - timedelta(days=max(1, days) - 1)
    return start.date(), start.replace(hour=0, minute=0, second=0, microsecond=0)
