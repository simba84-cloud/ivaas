"""Alert acknowledgements, stored so every screen and every restart agrees."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.alerts import AlertAcknowledgement


class AcknowledgementRow(Base):
    __tablename__ = "alert_acknowledgements"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    acknowledged_by: Mapped[str] = mapped_column(String(120))
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


def _of(r: AcknowledgementRow) -> AlertAcknowledgement:
    return AlertAcknowledgement(
        key=r.key, acknowledged_by=r.acknowledged_by, acknowledged_at=r.acknowledged_at, note=r.note
    )


class PostgresAcknowledgementStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def get(self, key: str) -> AlertAcknowledgement | None:
        async with self._sm() as db:
            row = await db.get(AcknowledgementRow, key)
        return _of(row) if row else None

    async def add(self, ack: AlertAcknowledgement) -> AlertAcknowledgement:
        stmt = (
            insert(AcknowledgementRow)
            .values(
                key=ack.key,
                acknowledged_by=ack.acknowledged_by,
                acknowledged_at=ack.acknowledged_at,
                note=ack.note,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "key"])
        )
        async with self._sm.begin() as db:
            await db.execute(stmt)
        kept = await self.get(ack.key)
        assert kept is not None
        return kept

    async def list_since(self, since: datetime) -> list[AlertAcknowledgement]:
        async with self._sm() as db:
            rows = (
                await db.scalars(
                    select(AcknowledgementRow)
                    .where(AcknowledgementRow.acknowledged_at >= since)
                    .order_by(AcknowledgementRow.acknowledged_at.desc())
                )
            ).all()
        return [_of(r) for r in rows]


class InMemoryAcknowledgementStore:
    def __init__(self) -> None:
        self._acks: dict[str, AlertAcknowledgement] = {}

    async def get(self, key: str) -> AlertAcknowledgement | None:
        return self._acks.get(key)

    async def add(self, ack: AlertAcknowledgement) -> AlertAcknowledgement:
        return self._acks.setdefault(ack.key, ack)

    async def list_since(self, since: datetime) -> list[AlertAcknowledgement]:
        rows = [a for a in self._acks.values() if a.acknowledged_at >= since]
        return sorted(rows, key=lambda a: a.acknowledged_at, reverse=True)
