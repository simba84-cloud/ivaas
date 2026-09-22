"""Read-only analytics over platform data, exposed to the assistant as tools.

These are the *only* things the assistant can do. There is no SQL tool, no write
tool and no free-form code execution, so the worst a confused or manipulated
model can do is call a harmless query with odd arguments.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ivaas.domain.models import CameraStatus, LoadingSession, SessionDirection, SessionStatus
from ivaas.ports.assistant import ToolSpec
from ivaas.ports.repositories import BayReader, CameraReader, Clock, SessionReader

MAX_ROWS = 50
MAX_DAYS = 90


def _days(value: Any, default: int = 7) -> int:
    try:
        return max(1, min(MAX_DAYS, int(value)))
    except (TypeError, ValueError):
        return default


def _row(s: LoadingSession) -> dict[str, Any]:
    return {
        "plate": s.plate,
        "direction": s.direction.value,
        "status": s.status.value,
        "ai_count": s.ai_count,
        "manual_count": s.manual_count,
        "variance": s.variance,
        "accuracy_pct": None if s.accuracy is None else round(s.accuracy * 100, 1),
        "opened_at": s.opened_at.isoformat(timespec="minutes"),
        "minutes": (
            None if s.closed_at is None else round((s.closed_at - s.opened_at).total_seconds() / 60)
        ),
    }


@dataclass
class AnalyticsTools:
    sessions: SessionReader
    cameras: CameraReader
    bays: BayReader
    clock: Clock

    SPECS = [
        ToolSpec(
            "list_sessions",
            "List truck loading/offloading sessions, newest first. Use for questions about "
            "specific trucks, plates, disputes, or recent activity.",
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
            "accuracy_report",
            "Counting accuracy of the AI versus manual verification: overall, per direction, "
            "and the worst sessions. Use for questions on accuracy, the 95% target, or variance.",
            {"type": "object", "properties": {"days": {"type": "integer"}}},
        ),
        ToolSpec(
            "totals_by_plate",
            "Per-truck totals: sessions, crates counted, net variance (AI minus manual). Use to "
            "find which trucks move the most crates or lose the most.",
            {"type": "object", "properties": {"days": {"type": "integer"}}},
        ),
        ToolSpec(
            "daily_totals",
            "Crates counted and sessions per day. Use for trends over time.",
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
        return {"days": n, "matched": len(rows), "sessions": [_row(s) for s in rows[:cap]]}

    async def _accuracy_report(self, days: Any = 7, **_: Any) -> dict[str, Any]:
        n, rows = await self._window(days)
        verified = [s for s in rows if s.accuracy is not None]
        if not verified:
            return {"days": n, "verified_sessions": 0, "note": "no manually verified sessions"}

        def mean(items: list[LoadingSession]) -> float | None:
            return (
                round(sum(s.accuracy or 0 for s in items) / len(items) * 100, 1) if items else None
            )

        truth = sum(s.manual_count or 0 for s in verified)
        counted = sum(s.ai_count for s in verified)
        return {
            "days": n,
            "verified_sessions": len(verified),
            "unverified_sessions": len(rows) - len(verified),
            "mean_session_accuracy_pct": mean(verified),
            "target_pct": 95.0,
            "sessions_below_target": sum(1 for s in verified if (s.accuracy or 0) < 0.95),
            "crates_ai": counted,
            "crates_manual": truth,
            "net_variance": counted - truth,
            "by_direction": {
                d.value: mean([s for s in verified if s.direction is d]) for d in SessionDirection
            },
            "worst_sessions": [
                _row(s) for s in sorted(verified, key=lambda s: s.accuracy or 0)[:5]
            ],
        }

    async def _totals_by_plate(self, days: Any = 7, **_: Any) -> dict[str, Any]:
        n, rows = await self._window(days)
        agg: dict[str, dict[str, int]] = defaultdict(
            lambda: {"sessions": 0, "crates_ai": 0, "net_variance": 0, "disputed": 0}
        )
        for s in rows:
            a = agg[s.plate or "UNKNOWN"]
            a["sessions"] += 1
            a["crates_ai"] += s.ai_count
            a["net_variance"] += s.variance or 0
            a["disputed"] += s.status is SessionStatus.DISPUTED
        ranked = sorted(agg.items(), key=lambda kv: kv[1]["crates_ai"], reverse=True)[:MAX_ROWS]
        return {"days": n, "trucks": [{"plate": k, **v} for k, v in ranked]}

    async def _daily_totals(self, days: Any = 7, **_: Any) -> dict[str, Any]:
        n, rows = await self._window(days)
        agg: dict[str, dict[str, int]] = defaultdict(lambda: {"sessions": 0, "crates_ai": 0})
        for s in rows:
            day = agg[s.opened_at.date().isoformat()]
            day["sessions"] += 1
            day["crates_ai"] += s.ai_count
        return {"days": n, "daily": [{"date": k, **v} for k, v in sorted(agg.items())]}

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
