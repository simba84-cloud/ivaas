"""The ingest ledger on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, delete
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base


class IngestedEventRow(Base):
    __tablename__ = "ingested_events"
    # the primary key is (tenant_id, event_id); tenant_id comes from the column default
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PostgresIngestLedger:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def claim(self, event_id: UUID, kind: str, at: datetime) -> bool:
        # one statement decides it: two copies arriving together cannot both win
        stmt = (
            insert(IngestedEventRow)
            .values(event_id=event_id, kind=kind, received_at=at)
            .on_conflict_do_nothing(index_elements=["tenant_id", "event_id"])
            .returning(IngestedEventRow.event_id)
        )
        async with self._sm.begin() as db:
            return (await db.execute(stmt)).first() is not None

    async def release(self, event_id: UUID) -> None:
        async with self._sm.begin() as db:
            await db.execute(delete(IngestedEventRow).where(IngestedEventRow.event_id == event_id))

    async def prune(self, before: datetime) -> int:
        async with self._sm.begin() as db:
            result = await db.execute(
                delete(IngestedEventRow).where(IngestedEventRow.received_at < before)
            )
        return result.rowcount or 0


class InMemoryIngestLedger:
    def __init__(self) -> None:
        self._seen: dict[UUID, datetime] = {}

    async def claim(self, event_id: UUID, kind: str, at: datetime) -> bool:
        if event_id in self._seen:
            return False
        self._seen[event_id] = at
        return True

    async def release(self, event_id: UUID) -> None:
        self._seen.pop(event_id, None)

    async def prune(self, before: datetime) -> int:
        old = [k for k, at in self._seen.items() if at < before]
        for k in old:
            del self._seen[k]
        return len(old)
