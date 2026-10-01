"""Break-glass grants on Postgres (tenant-owned, row-level secured), and in memory.

Authentication looks a grant up by id before it knows the tenant, in
`system_context()`; the grant carries its tenant, which then becomes the request's.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, String, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.break_glass import BreakGlassGrant
from ivaas.tenancy import current_tenant, is_system

_FIELDS = (
    "id",
    "tenant_id",
    "requested_by",
    "reason",
    "requested_at",
    "decided_by",
    "decided_at",
    "approved",
    "ended_by",
    "ended_at",
)


class GrantRow(Base):
    __tablename__ = "break_glass_grants"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    requested_by: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(500))
    duration_s: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved: Mapped[bool | None] = mapped_column(Boolean)
    ended_by: Mapped[str | None] = mapped_column(String(120))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _grant(r: GrantRow) -> BreakGlassGrant:
    return BreakGlassGrant(
        duration=timedelta(seconds=r.duration_s), **{f: getattr(r, f) for f in _FIELDS}
    )


class PostgresBreakGlassStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, grant: BreakGlassGrant) -> None:
        values = {f: getattr(grant, f) for f in _FIELDS}
        async with self._sm.begin() as db:
            await db.merge(GrantRow(duration_s=int(grant.duration.total_seconds()), **values))

    async def get(self, grant_id: UUID) -> BreakGlassGrant | None:
        async with self._sm() as db:
            r = await db.get(GrantRow, grant_id)
        return _grant(r) if r else None

    async def list_all(self) -> list[BreakGlassGrant]:
        async with self._sm() as db:
            stmt = select(GrantRow).order_by(GrantRow.requested_at.desc())
            return [_grant(r) for r in (await db.scalars(stmt)).all()]


class InMemoryBreakGlassStore:
    """Sees what row-level security would let it see."""

    def __init__(self) -> None:
        self._grants: dict[UUID, BreakGlassGrant] = {}

    @staticmethod
    def _visible(g: BreakGlassGrant) -> bool:
        return is_system() or g.tenant_id == current_tenant()

    async def save(self, grant: BreakGlassGrant) -> None:
        self._grants[grant.id] = grant

    async def get(self, grant_id: UUID) -> BreakGlassGrant | None:
        g = self._grants.get(grant_id)
        return g if g is not None and self._visible(g) else None

    async def list_all(self) -> list[BreakGlassGrant]:
        mine = [g for g in self._grants.values() if self._visible(g)]
        return sorted(mine, key=lambda g: g.requested_at, reverse=True)
