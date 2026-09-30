"""Balances over a window: crates out, back and outstanding, per truck, route or day.

One query behind the Balances page and the assistant, so the two never disagree on
what a truck has out (proposal T5.3, T6.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ivaas.domain.manifests import Balance, balances, local_day, site_tz

MAX_DAYS = 90

By = Literal["truck", "route", "day"]


@dataclass
class BalanceQuery:
    sessions: Any
    bays: Any
    sites: Any
    manifests: Any
    clock: Any

    async def __call__(self, days: int = 7, by: By = "truck") -> list[Balance]:
        """The last `days` days (1-90), from each load's count of record. Open loads
        are counted as in progress, never added in. Days are each site's own."""
        start = self.clock.now() - timedelta(days=max(1, min(days, MAX_DAYS)))
        sessions = await self.sessions.list_recent(since=start, limit=5000)
        site_of = {b.id: b.site_id for b in await self.bays.list_all()}
        tz_of = {s.id: site_tz(s.timezone) for s in await self.sites.list_all()}
        route_of = {
            x.session_id: x.route for x in await self.manifests.since(start.date()) if x.session_id
        }

        def key(s) -> str:
            if by == "route":
                return route_of.get(s.id, "")
            if by == "day":
                tz = tz_of.get(site_of.get(s.bay_id), ZoneInfo("UTC"))
                return local_day(s.opened_at, tz).isoformat()
            return s.plate or ""

        return balances(sessions, key)
