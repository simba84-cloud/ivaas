"""Edge availability periods on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.availability import Period

_FIELDS = ("id", "node_id", "camera_id", "up", "since", "until", "peak_spool", "last_spool")


class PeriodRow(Base):
    __tablename__ = "edge_availability"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    camera_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    up: Mapped[bool] = mapped_column(Boolean)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    peak_spool: Mapped[int] = mapped_column(Integer)
    last_spool: Mapped[int] = mapped_column(Integer)


def _period(r: PeriodRow) -> Period:
    return Period(**{f: getattr(r, f) for f in _FIELDS})


class PostgresAvailabilityStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def latest(self, node_id: UUID) -> dict[UUID | None, Period]:
        stmt = (
            select(PeriodRow)
            .where(PeriodRow.node_id == node_id)
            .distinct(PeriodRow.camera_id)
            .order_by(PeriodRow.camera_id, PeriodRow.until.desc())
        )
        async with self._sm() as db:
            return {r.camera_id: _period(r) for r in (await db.scalars(stmt)).all()}

    async def save_all(self, periods: list[Period]) -> None:
        async with self._sm.begin() as db:
            for p in periods:
                await db.merge(PeriodRow(**{f: getattr(p, f) for f in _FIELDS}))

    async def between(self, node_ids: list[UUID], start: datetime, end: datetime) -> list[Period]:
        if not node_ids:
            return []
        stmt = select(PeriodRow).where(
            PeriodRow.node_id.in_(node_ids), PeriodRow.until >= start, PeriodRow.since < end
        )
        async with self._sm() as db:
            return [_period(r) for r in (await db.scalars(stmt)).all()]


class InMemoryAvailabilityStore:
    def __init__(self) -> None:
        self._periods: dict[UUID, Period] = {}

    async def latest(self, node_id: UUID) -> dict[UUID | None, Period]:
        out: dict[UUID | None, Period] = {}
        for p in self._periods.values():
            if p.node_id == node_id and (
                p.camera_id not in out or p.until > out[p.camera_id].until
            ):
                out[p.camera_id] = p
        return out

    async def save_all(self, periods: list[Period]) -> None:
        for p in periods:
            self._periods[p.id] = p

    async def between(self, node_ids: list[UUID], start: datetime, end: datetime) -> list[Period]:
        wanted = set(node_ids)
        return [
            p
            for p in self._periods.values()
            if p.node_id in wanted and p.until >= start and p.since < end
        ]
