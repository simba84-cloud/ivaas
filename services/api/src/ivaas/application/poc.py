"""Build the POC report for a site over a window of its own days (proposal M9)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from ivaas.domain.availability import availability
from ivaas.domain.manifests import local_day, site_tz
from ivaas.domain.models import NotFoundError
from ivaas.domain.poc import NodeUptime, PocReport, build_poc

#: the longest window a report covers; the POC is fourteen days
MAX_DAYS = 62


@dataclass
class BuildPocReport:
    tenants: Any
    sites: Any
    bays: Any
    sessions: Any
    tally: Any
    exceptions: Any
    manifests: Any
    edge: Any
    availability: Any
    clock: Any
    target: float
    tenant_id: UUID | None = None

    async def __call__(
        self,
        site_id: UUID,
        start: date,
        end: date,
        *,
        uptime_target: float = 0.99,
        baseline_minutes: float | None = None,
        crate_value: float | None = None,
        currency: str = "USD",
    ) -> PocReport:
        site = await self.sites.get(site_id)
        if site is None:
            raise NotFoundError(f"site {site_id} not found")
        if end < start or (end - start).days >= MAX_DAYS:
            raise ValueError(f"give a window of 1 to {MAX_DAYS} days, the start first")
        tz = site_tz(site.timezone)
        now = self.clock.now()
        opens = datetime.combine(start, time.min, tz)
        # up to the end of its last day, or now if that is still to come
        closes = min(datetime.combine(end + timedelta(days=1), time.min, tz), now)
        bay_ids = {b.id for b in await self.bays.list_for_site(site_id)}

        loads = [
            s
            for s in await self.sessions.list_recent(since=opens - timedelta(hours=1), limit=20000)
            if s.bay_id in bay_ids and start <= local_day(s.opened_at, tz) <= end
        ]
        sheets = [
            t
            for t in await self.tally.list_recent(limit=20000)
            if t.bay_id in bay_ids and start <= t.date <= end
        ]
        here = {x.id for x in await self.manifests.since(start) if x.site_id == site_id}
        mine = {s.id for s in loads}
        exceptions = [
            e
            for e in await self.exceptions.list(since=start)
            if e.day <= end and (e.session_id in mine or e.line_id in here)
        ]
        nodes = []
        # history is kept from the first heartbeat recorded; before that, unmeasured
        recorded = await self.availability.first_recorded()
        for n in await self.edge.list_nodes() if recorded else []:
            # over the part of the window the node existed (enrolled, not yet revoked)
            # and history was being kept
            began = max(opens, n.enrolled_at, recorded)
            ended = min(closes, n.revoked_at or closes)
            if n.site_id != site_id or ended <= began:
                continue
            periods = await self.availability.between([n.id], began, ended)
            whole = [p for p in periods if p.camera_id is None]
            nodes.append(NodeUptime(n.name, availability(whole, began, ended)))
        tenant = await self.tenants.get(self.tenant_id) if self.tenant_id else None
        return build_poc(
            tenant=tenant.name if tenant else "",
            site=site.name,
            start=start,
            end=end,
            timezone=str(tz),
            now=now,
            loads=loads,
            sheets=sheets,
            exceptions=exceptions,
            nodes=nodes,
            recorded_from=recorded,
            target=self.target,
            uptime_target=uptime_target,
            baseline_minutes=baseline_minutes,
            crate_value=crate_value,
            currency=currency,
        )
