"""Read-only analytics over platform data, exposed to the assistant as tools.

These are the *only* things the assistant can do. There is no SQL tool, no write
tool and no free-form code execution, so the worst a confused or manipulated
model can do is call a harmless query with odd arguments.

Every figure comes from the code that produces it for people: a day is the daily
report (`BuildDailyReport`), crates out and back are the Balances page
(`BalanceQuery`), accuracy is `LoadingSession.accuracy` against the configured
target. The assistant cannot give a number the reports would not (proposal T6.6).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from ivaas.domain.manifests import Balance, local_day, site_tz
from ivaas.domain.models import CameraStatus, LoadingSession, SessionDirection, SessionStatus
from ivaas.domain.reports import EXCEPTION_LABELS, DailyReport
from ivaas.ports.assistant import ToolSpec
from ivaas.ports.repositories import BayReader, CameraReader, Clock, SessionReader

MAX_ROWS = 50
MAX_DAYS = 90


def _days(value: Any, default: int = 7) -> int:
    try:
        return max(1, min(MAX_DAYS, int(value)))
    except (TypeError, ValueError):
        return default


def _pct(value: float | None) -> float | None:
    """As the report prints it: a percentage to one decimal place."""
    return None if value is None else round(value * 100, 1)


def _balance(b: Balance, key: str) -> dict[str, Any]:
    return {
        key: b.key or None,
        "dispatched": b.dispatched,
        "returned": b.returned,
        "outstanding": b.outstanding,
        "loads_out": b.loads_out,
        "loads_back": b.loads_back,
        "still_at_the_bay": b.in_progress,
        "corrected_loads": b.corrected,
    }


@dataclass
class AnalyticsTools:
    sessions: SessionReader
    cameras: CameraReader
    bays: BayReader
    sites: Any
    clock: Clock
    #: BalanceQuery: (days, by) -> balances, exactly as the Balances page
    balances: Callable[[int, str], Awaitable[list[Balance]]]
    #: BuildDailyReport: (site_id, day) -> the daily report
    daily: Callable[[UUID, date], Awaitable[DailyReport]]
    target: float = 0.95

    SPECS = [
        ToolSpec(
            "list_sessions",
            "List truck loads (sessions), newest first, with each load's AI count, any "
            "correction by a person, the count of record and the manual tally. Use for "
            "questions about specific trucks, plates, disputes, corrections or recent loads.",
            {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "look-back window, default 7"},
                    "status": {"type": "string", "enum": [s.value for s in SessionStatus]},
                    "direction": {"type": "string", "enum": [d.value for d in SessionDirection]},
                    "plate": {"type": "string", "description": "full or partial number plate"},
                    "limit": {"type": "integer", "description": f"max rows, up to {MAX_ROWS}"},
                },
            },
        ),
        ToolSpec(
            "daily_report",
            "One site's day, exactly as the daily report states it: loads, crates dispatched, "
            "returned and outstanding, loads still at the bay, loads without a plate, accuracy "
            "against the tally sheets, manifest exceptions and corrections. Use for any "
            "question about a particular day, including today and yesterday.",
            {
                "type": "object",
                "properties": {
                    "day": {
                        "type": "string",
                        "description": "YYYY-MM-DD, or 'today' or 'yesterday' (site time)",
                    },
                    "site": {"type": "string", "description": "site name; omit if only one"},
                },
            },
        ),
        ToolSpec(
            "balances",
            "Crates dispatched, returned and still outstanding over the last N days, per "
            "truck, per route or per day, exactly as the Balances page. Use for which trucks "
            "have crates out, leakage, and trends over several days.",
            {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "look-back window, default 7"},
                    "by": {"type": "string", "enum": ["truck", "route", "day"]},
                },
            },
        ),
        ToolSpec(
            "accuracy_report",
            "Counting accuracy of the AI count against the manual tally over the last N days: "
            "overall, per direction, against the target, and the worst loads. Accuracy is "
            "always on the AI count, never on a person's correction.",
            {"type": "object", "properties": {"days": {"type": "integer"}}},
        ),
        ToolSpec(
            "camera_health",
            "Current status of every camera: online/offline, protocol, last seen.",
            {"type": "object", "properties": {}},
        ),
    ]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_{name}", None)
        if handler is None or name not in {s.name for s in self.SPECS}:
            return {"error": f"unknown tool '{name}'"}
        return await handler(**{k: v for k, v in args.items() if v is not None})

    async def _window(self, days: Any) -> tuple[int, list[LoadingSession]]:
        n = _days(days)
        since = self.clock.now() - timedelta(days=n)
        return n, await self.sessions.list_recent(since=since, limit=5000)

    async def _where(self) -> dict[UUID, tuple[str, Any]]:
        """Each bay's site name and time zone, so loads are told in site time."""
        sites = {s.id: s for s in await self.sites.list_all()}
        out = {}
        for b in await self.bays.list_all():
            site = sites.get(b.site_id)
            out[b.id] = (site.name if site else "", site_tz(site.timezone if site else None))
        return out

    async def _list_sessions(
        self,
        days: Any = 7,
        status: str | None = None,
        direction: str | None = None,
        plate: str | None = None,
        limit: Any = 20,
        **_: Any,
    ) -> dict[str, Any]:
        n, rows = await self._window(days)
        if status:
            rows = [s for s in rows if s.status.value == status]
        if direction:
            rows = [s for s in rows if s.direction.value == direction]
        if plate:
            needle = plate.replace(" ", "").upper()
            rows = [s for s in rows if s.plate and needle in s.plate.replace(" ", "").upper()]
        cap = max(1, min(MAX_ROWS, _days(limit, 20)))
        where = await self._where()

        def row(s: LoadingSession) -> dict[str, Any]:
            site, tz = where.get(s.bay_id, ("", site_tz(None)))
            opened = s.opened_at.astimezone(tz)
            return {
                "plate": s.plate,
                "site": site or None,
                "day": opened.date().isoformat(),
                "opened": opened.strftime("%H:%M"),
                "direction": s.direction.value,
                "status": s.status.value,
                "ai_count": s.ai_count,
                "corrected_count": s.override_count,
                "count_of_record": s.count_of_record,
                "manual_count": s.manual_count,
                "variance": s.variance,
                "accuracy_pct": _pct(s.accuracy),
                "minutes": None
                if s.closed_at is None
                else round((s.closed_at - s.opened_at).total_seconds() / 60),
            }

        return {"days": n, "matched": len(rows), "sessions": [row(s) for s in rows[:cap]]}

    async def _daily_report(self, day: Any = "today", site: Any = None, **_: Any) -> dict:
        sites = await self.sites.list_all()
        if site:
            wanted = str(site).strip().lower()
            chosen = [s for s in sites if s.name.lower() == wanted] or [
                s for s in sites if wanted in s.name.lower()
            ]
        else:
            chosen = sites
        if len(chosen) != 1:
            return {
                "error": "no site by that name" if site and not chosen else "which site?",
                "sites": [s.name for s in sites],
            }
        where = chosen[0]
        today = local_day(self.clock.now(), site_tz(where.timezone))
        text = str(day).strip().lower()
        if text in ("", "today"):
            the_day = today
        elif text == "yesterday":
            the_day = today - timedelta(days=1)
        else:
            try:
                the_day = date.fromisoformat(text)
            except ValueError:
                return {"error": "day must be YYYY-MM-DD, 'today' or 'yesterday'"}
        r = await self.daily(where.id, the_day)
        return {
            "site": r.site,
            "day": r.day.isoformat(),
            "timezone": r.timezone,
            "day_still_running": the_day == today,
            "loads": len(r.loads),
            "dispatched": r.dispatched,
            "returned": r.returned,
            "outstanding": r.outstanding,
            "still_at_the_bay": r.in_progress,
            # which loads: a count alone invites a model to guess the plate
            "loads_at_the_bay": [
                {
                    "opened": x.opened,
                    "plate": x.plate or None,
                    "direction": x.direction,
                    "counted_so_far": x.ai_count,
                }
                for x in r.loads
                if x.status == "open"
            ],
            "without_a_plate": r.unidentified,
            "accuracy": {
                "verified_loads": r.verified,
                "mean_accuracy_pct": _pct(r.mean_accuracy),
                "aggregate_error_pct": _pct(r.aggregate_error),
                "target_pct": _pct(r.target),
                "at_or_above_target": r.passing,
                "note": None
                if r.verified
                else "no tally sheet was reconciled for this day, so there is no accuracy figure",
            },
            "manifest_exceptions": [
                {
                    "what": EXCEPTION_LABELS[e.kind],
                    "plate": e.plate,
                    "route": e.route or None,
                    "manifest": e.expected,
                    "counted": e.counted,
                    "difference": e.difference,
                    "status": e.status.value,
                }
                for e in r.exceptions
            ],
            "corrections": [
                {
                    "opened": c.opened,
                    "plate": c.plate or None,
                    "ai_count": c.ai_count,
                    "corrected_to": c.corrected,
                }
                for c in r.corrections
            ],
        }

    async def _balances(self, days: Any = 7, by: Any = "truck", **_: Any) -> dict[str, Any]:
        n = _days(days)
        how = by if by in ("truck", "route", "day") else "truck"
        key = {"truck": "plate", "route": "route", "day": "day"}[how]
        rows = await self.balances(n, how)
        if how == "day":
            rows = sorted(rows, key=lambda b: b.key)
        return {"days": n, "by": how, "rows": [_balance(b, key) for b in rows[:MAX_ROWS]]}

    async def _accuracy_report(self, days: Any = 7, **_: Any) -> dict[str, Any]:
        n, rows = await self._window(days)
        verified = [s for s in rows if s.accuracy is not None]
        if not verified:
            return {
                "days": n,
                "verified_sessions": 0,
                "note": "no tally sheet was reconciled in this window, so there is no "
                "accuracy figure",
            }

        def mean(items: list[LoadingSession]) -> float | None:
            return _pct(sum(s.accuracy or 0 for s in items) / len(items)) if items else None

        truth = sum(s.manual_count or 0 for s in verified)
        counted = sum(s.ai_count for s in verified)
        return {
            "days": n,
            "verified_sessions": len(verified),
            "unverified_sessions": len(rows) - len(verified),
            "mean_session_accuracy_pct": mean(verified),
            "aggregate_error_pct": _pct(abs(counted - truth) / truth) if truth else None,
            "target_pct": _pct(self.target),
            "sessions_below_target": sum(1 for s in verified if (s.accuracy or 0) < self.target),
            "crates_ai": counted,
            "crates_manual": truth,
            "net_variance": counted - truth,
            "by_direction": {
                d.value: mean([s for s in verified if s.direction is d]) for d in SessionDirection
            },
            "worst_sessions": [
                {
                    "plate": s.plate,
                    "direction": s.direction.value,
                    "ai_count": s.ai_count,
                    "manual_count": s.manual_count,
                    "accuracy_pct": _pct(s.accuracy),
                }
                for s in sorted(verified, key=lambda s: s.accuracy or 0)[:5]
            ],
        }

    async def _camera_health(self, **_: Any) -> dict[str, Any]:
        cams = [
            c for b in await self.bays.list_all() for c in await self.cameras.list_for_bay(b.id)
        ]
        return {
            "total": len(cams),
            "online": sum(1 for c in cams if c.status is CameraStatus.ONLINE),
            "cameras": [
                {
                    "name": c.name,
                    "position": c.role.value,
                    "status": c.status.value,
                    "protocol": c.source.protocol,
                    "last_seen": c.last_seen_at.isoformat(timespec="minutes")
                    if c.last_seen_at
                    else None,
                }
                for c in cams
            ],
        }
