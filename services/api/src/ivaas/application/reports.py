"""Build a site's daily report, and file yesterday's every morning (proposal M6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ivaas.domain.fleet import identification
from ivaas.domain.manifests import local_day
from ivaas.domain.models import NotFoundError
from ivaas.domain.reports import DailyReport, build_daily
from ivaas.tenancy import object_key

#: yesterday's report is filed once the site's day has properly started
FILE_AFTER = time(6, 0)


@dataclass
class StoredReport:
    site_id: UUID
    day: date
    pdf_key: str
    csv_key: str
    loads: int
    generated_at: datetime
    id: UUID | None = None

    def __post_init__(self) -> None:
        self.id = self.id or uuid4()


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


@dataclass
class BuildDailyReport:
    tenants: Any
    sites: Any
    bays: Any
    sessions: Any
    exceptions: Any
    fleet: Any
    clock: Any
    target: float
    tenant_id: UUID | None = None

    async def __call__(self, site_id: UUID, day: date) -> DailyReport:
        site = await self.sites.get(site_id)
        if site is None:
            raise NotFoundError(f"site {site_id} not found")
        tz = _tz(site.timezone)
        start = datetime.combine(day, time.min, tz)
        bay_ids = {b.id for b in await self.bays.list_for_site(site_id)}
        loads = [
            s
            for s in await self.sessions.list_recent(since=start - timedelta(hours=1), limit=5000)
            if s.bay_id in bay_ids and local_day(s.opened_at, tz) == day
        ]
        has_register = bool(await self.fleet.list_all())
        day_exceptions = [e for e in await self.exceptions.list(since=day) if e.day == day]
        tenant = await self.tenants.get(self.tenant_id) if self.tenant_id else None
        return build_daily(
            tenant=tenant.name if tenant else "",
            site=site.name,
            day=day,
            tz=tz,
            sessions=loads,
            exceptions=day_exceptions,
            identified={
                s.id: identification(s.plate, s.vehicle_id, has_register).value for s in loads
            },
            target=self.target,
            now=self.clock.now(),
        )


@dataclass
class FileDailyReports:
    """Every morning, after 06:00 site time: yesterday's report, once, per site."""

    build: BuildDailyReport
    sites: Any
    reports: Any
    objects: Any
    clock: Any
    #: the renderers are adapters (ReportLab, csv); the composition root supplies them
    render_pdf: Callable[[DailyReport], bytes]
    render_csv: Callable[[DailyReport], bytes]

    async def __call__(self) -> list[StoredReport]:
        filed = []
        now = self.clock.now()
        for site in await self.sites.list_all():
            tz = _tz(site.timezone)
            local = now.astimezone(tz)
            if local.time() < FILE_AFTER:
                continue
            day = local.date() - timedelta(days=1)
            if await self.reports.get(site.id, day) is not None:
                continue
            filed.append(await self.file(site.id, day))
        return filed

    async def file(self, site_id: UUID, day: date) -> StoredReport:
        report = await self.build(site_id, day)
        base = object_key(f"reports/{site_id}/{day.isoformat()}")
        pdf_key, csv_key = f"{base}.pdf", f"{base}.csv"
        await self.objects.put(pdf_key, self.render_pdf(report), "application/pdf")
        await self.objects.put(csv_key, self.render_csv(report), "text/csv")
        stored = StoredReport(
            site_id=site_id,
            day=day,
            pdf_key=pdf_key,
            csv_key=csv_key,
            loads=len(report.loads),
            generated_at=self.clock.now(),
        )
        await self.reports.save(stored)
        return stored
