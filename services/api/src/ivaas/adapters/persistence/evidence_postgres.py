"""Evidence clips on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, String, delete, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.evidence import EvidenceClip, EvidenceKind

_FIELDS = (
    "id",
    "session_id",
    "bay_id",
    "camera_id",
    "started_at",
    "ended_at",
    "object_key",
    "size_bytes",
    "sha256",
    "created_at",
    "expires_at",
)


class EvidenceRow(Base):
    __tablename__ = "evidence_clips"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    bay_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    camera_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    object_key: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def _of(r: EvidenceRow) -> EvidenceClip:
    return EvidenceClip(kind=EvidenceKind(r.kind), **{f: getattr(r, f) for f in _FIELDS})


class PostgresEvidenceStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, clip: EvidenceClip) -> None:
        values = {f: getattr(clip, f) for f in _FIELDS} | {"kind": clip.kind.value}
        stmt = insert(EvidenceRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[EvidenceRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def for_session(self, session_id: UUID) -> list[EvidenceClip]:
        stmt = (
            select(EvidenceRow)
            .where(EvidenceRow.session_id == session_id)
            .order_by(EvidenceRow.started_at)
        )
        async with self._sm() as db:
            return [_of(r) for r in (await db.scalars(stmt)).all()]

    async def expired(self, now: datetime, limit: int = 500) -> list[EvidenceClip]:
        stmt = select(EvidenceRow).where(EvidenceRow.expires_at <= now).limit(limit)
        async with self._sm() as db:
            return [_of(r) for r in (await db.scalars(stmt)).all()]

    async def delete(self, clip_id: UUID) -> None:
        async with self._sm.begin() as db:
            await db.execute(delete(EvidenceRow).where(EvidenceRow.id == clip_id))


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._clips: dict[UUID, EvidenceClip] = {}

    async def save(self, clip: EvidenceClip) -> None:
        self._clips[clip.id] = clip

    async def for_session(self, session_id: UUID) -> list[EvidenceClip]:
        rows = [c for c in self._clips.values() if c.session_id == session_id]
        return sorted(rows, key=lambda c: c.started_at)

    async def expired(self, now: datetime, limit: int = 500) -> list[EvidenceClip]:
        return [c for c in self._clips.values() if c.expires_at <= now][:limit]

    async def delete(self, clip_id: UUID) -> None:
        self._clips.pop(clip_id, None)
