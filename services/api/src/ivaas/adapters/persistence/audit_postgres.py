"""AuditLog on Postgres. `detail` is JSONB: read whole, never queried by field."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, desc, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.audit import AuditAction, AuditEntry

log = logging.getLogger(__name__)


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


def _to_domain(r: AuditRow) -> AuditEntry:
    return AuditEntry(
        id=r.id,
        at=r.at,
        actor=r.actor,
        action=AuditAction(r.action),
        subject=r.subject,
        detail=dict(r.detail or {}),
    )


class PostgresAuditLog:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def record(self, entry: AuditEntry) -> None:
        row = AuditRow(
            id=entry.id,
            at=entry.at,
            actor=entry.actor,
            action=entry.action.value,
            subject=entry.subject,
            detail=entry.detail,
        )
        try:
            async with self._sm.begin() as db:
                db.add(row)
        except Exception:
            # The action itself succeeded. Losing its audit row is worth a loud log,
            # not a 500 that tells the operator their approval failed when it did not.
            log.exception("could not write audit entry: %s by %s", entry.action, entry.actor)

    async def list_recent(
        self,
        *,
        since: datetime | None = None,
        actor: str | None = None,
        action: AuditAction | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        stmt = select(AuditRow).order_by(desc(AuditRow.at)).limit(max(1, min(limit, 1000)))
        if since is not None:
            stmt = stmt.where(AuditRow.at >= since)
        if actor:
            stmt = stmt.where(AuditRow.actor == actor)
        if action is not None:
            stmt = stmt.where(AuditRow.action == action.value)
        async with self._sm() as db:
            rows = (await db.scalars(stmt)).all()
        return [_to_domain(r) for r in rows]
