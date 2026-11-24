"""The alert email outbox on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, Text, exists, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.alert_email import AlertEmail, AlertKind, EmailStatus


class AlertEmailRow(Base):
    __tablename__ = "alert_emails"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    recipients: Mapped[list] = mapped_column(JSONB)
    subject: Mapped[str] = mapped_column(String(200))
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    attempts: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_FIELDS = (
    "id",
    "dedupe_key",
    "subject",
    "body_text",
    "body_html",
    "attempts",
    "next_attempt_at",
    "last_error",
    "created_at",
    "sent_at",
)


def _values(e: AlertEmail) -> dict:
    return {
        **{f: getattr(e, f) for f in _FIELDS},
        "kind": e.kind.value,
        "status": e.status.value,
        "recipients": list(e.recipients),
    }


def _email(r: AlertEmailRow) -> AlertEmail:
    return AlertEmail(
        **{f: getattr(r, f) for f in _FIELDS},
        kind=AlertKind(r.kind),
        status=EmailStatus(r.status),
        recipients=tuple(r.recipients),
    )


class PostgresAlertEmailStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def queue(self, email: AlertEmail) -> bool:
        # tenant_id comes from the column default; the pair is unique
        stmt = (
            insert(AlertEmailRow)
            .values(**_values(email))
            .on_conflict_do_nothing(index_elements=["tenant_id", "dedupe_key"])
        )
        async with self._sm.begin() as db:
            result = await db.execute(stmt)
        return bool(result.rowcount)

    async def exists(self, dedupe_key: str) -> bool:
        stmt = select(exists().where(AlertEmailRow.dedupe_key == dedupe_key))
        async with self._sm() as db:
            return bool(await db.scalar(stmt))

    async def save(self, email: AlertEmail) -> None:
        async with self._sm.begin() as db:
            await db.merge(AlertEmailRow(**_values(email)))

    async def recent(self, limit: int = 20) -> list[AlertEmail]:
        stmt = select(AlertEmailRow).order_by(AlertEmailRow.created_at.desc()).limit(limit)
        async with self._sm() as db:
            return [_email(r) for r in (await db.scalars(stmt)).all()]

    async def claim_due(self, now: datetime, limit: int = 20) -> list[AlertEmail]:
        stmt = (
            select(AlertEmailRow)
            .where(
                AlertEmailRow.status == EmailStatus.PENDING.value,
                AlertEmailRow.next_attempt_at <= now,
            )
            .order_by(AlertEmailRow.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)  # a second API process takes the others
        )
        async with self._sm.begin() as db:
            claimed = []
            for r in (await db.scalars(stmt)).all():
                e = _email(r)
                e.claim(now)
                r.next_attempt_at = e.next_attempt_at
                claimed.append(e)
        return claimed


class InMemoryAlertEmailStore:
    def __init__(self) -> None:
        self._emails: dict[UUID, AlertEmail] = {}

    async def queue(self, email: AlertEmail) -> bool:
        if await self.exists(email.dedupe_key):
            return False
        self._emails[email.id] = email
        return True

    async def exists(self, dedupe_key: str) -> bool:
        return any(e.dedupe_key == dedupe_key for e in self._emails.values())

    async def save(self, email: AlertEmail) -> None:
        self._emails[email.id] = email

    async def recent(self, limit: int = 20) -> list[AlertEmail]:
        return sorted(self._emails.values(), key=lambda e: e.created_at, reverse=True)[:limit]

    async def claim_due(self, now: datetime, limit: int = 20) -> list[AlertEmail]:
        due = sorted(
            (
                e
                for e in self._emails.values()
                if e.status is EmailStatus.PENDING
                and e.next_attempt_at is not None
                and e.next_attempt_at <= now
            ),
            key=lambda e: e.next_attempt_at or now,
        )[:limit]
        for e in due:
            e.claim(now)
        return due
