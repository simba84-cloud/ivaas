"""Webhook endpoints and deliveries on Postgres (tenant-owned, row-level secured), and
in memory. An endpoint's signing secret is sealed at rest: it has to be read back to
sign, so it cannot be hashed like a password."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, Text, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.webhooks import Delivery, DeliveryStatus, WebhookEndpoint


class EndpointRow(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[list] = mapped_column(JSONB)
    secret: Mapped[str] = mapped_column(Text)  # sealed
    description: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeliveryRow(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    endpoint_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    event: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12))
    attempts: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(300))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replay_of: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_DELIVERY = (
    "id",
    "endpoint_id",
    "event_id",
    "event",
    "payload",
    "attempts",
    "next_attempt_at",
    "last_status_code",
    "last_error",
    "delivered_at",
    "replay_of",
    "created_at",
)


def _delivery(r: DeliveryRow) -> Delivery:
    return Delivery(**{f: getattr(r, f) for f in _DELIVERY}, status=DeliveryStatus(r.status))


class PostgresWebhookStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession], box: Any) -> None:
        self._sm, self._box = sm, box

    def _endpoint(self, r: EndpointRow) -> WebhookEndpoint:
        return WebhookEndpoint(
            id=r.id,
            url=r.url,
            events=tuple(r.events),
            secret=self._box.open(r.secret),
            description=r.description,
            created_by=r.created_by,
            created_at=r.created_at,
        )

    async def save_endpoint(self, e: WebhookEndpoint) -> None:
        async with self._sm.begin() as db:
            await db.merge(
                EndpointRow(
                    id=e.id,
                    url=e.url,
                    events=list(e.events),
                    secret=self._box.seal(e.secret),
                    description=e.description,
                    created_by=e.created_by,
                    created_at=e.created_at,
                )
            )

    async def endpoints(self) -> list[WebhookEndpoint]:
        async with self._sm() as db:
            rows = (await db.scalars(select(EndpointRow).order_by(EndpointRow.created_at))).all()
        return [self._endpoint(r) for r in rows]

    async def endpoint(self, endpoint_id: UUID) -> WebhookEndpoint | None:
        async with self._sm() as db:
            r = await db.get(EndpointRow, endpoint_id)
        return self._endpoint(r) if r else None

    async def delete_endpoint(self, endpoint_id: UUID) -> bool:
        async with self._sm.begin() as db:
            await db.execute(delete(DeliveryRow).where(DeliveryRow.endpoint_id == endpoint_id))
            gone = await db.execute(delete(EndpointRow).where(EndpointRow.id == endpoint_id))
        return bool(gone.rowcount)

    async def save_delivery(self, d: Delivery) -> None:
        async with self._sm.begin() as db:
            await db.merge(DeliveryRow(**{f: getattr(d, f) for f in _DELIVERY}, status=d.status))

    async def delivery(self, delivery_id: UUID) -> Delivery | None:
        async with self._sm() as db:
            r = await db.get(DeliveryRow, delivery_id)
        return _delivery(r) if r else None

    async def deliveries(self, endpoint_id: UUID, limit: int = 50) -> list[Delivery]:
        stmt = (
            select(DeliveryRow)
            .where(DeliveryRow.endpoint_id == endpoint_id)
            .order_by(DeliveryRow.created_at.desc())
            .limit(limit)
        )
        async with self._sm() as db:
            return [_delivery(r) for r in (await db.scalars(stmt)).all()]

    async def claim_due(self, now: datetime, limit: int = 20) -> list[Delivery]:
        stmt = (
            select(DeliveryRow)
            .where(
                DeliveryRow.status == DeliveryStatus.PENDING.value,
                DeliveryRow.next_attempt_at <= now,
            )
            .order_by(DeliveryRow.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)  # a second API process takes the others
        )
        async with self._sm.begin() as db:
            rows = (await db.scalars(stmt)).all()
            claimed = []
            for r in rows:
                d = _delivery(r)
                d.claim(now)
                r.next_attempt_at = d.next_attempt_at
                claimed.append(d)
        return claimed


class InMemoryWebhookStore:
    def __init__(self) -> None:
        self._endpoints: dict[UUID, WebhookEndpoint] = {}
        self._deliveries: dict[UUID, Delivery] = {}

    async def save_endpoint(self, e: WebhookEndpoint) -> None:
        self._endpoints[e.id] = e

    async def endpoints(self) -> list[WebhookEndpoint]:
        return sorted(self._endpoints.values(), key=lambda e: e.created_at)

    async def endpoint(self, endpoint_id: UUID) -> WebhookEndpoint | None:
        return self._endpoints.get(endpoint_id)

    async def delete_endpoint(self, endpoint_id: UUID) -> bool:
        self._deliveries = {
            k: d for k, d in self._deliveries.items() if d.endpoint_id != endpoint_id
        }
        return self._endpoints.pop(endpoint_id, None) is not None

    async def save_delivery(self, d: Delivery) -> None:
        self._deliveries[d.id] = d

    async def delivery(self, delivery_id: UUID) -> Delivery | None:
        return self._deliveries.get(delivery_id)

    async def deliveries(self, endpoint_id: UUID, limit: int = 50) -> list[Delivery]:
        mine = [d for d in self._deliveries.values() if d.endpoint_id == endpoint_id]
        return sorted(mine, key=lambda d: d.created_at, reverse=True)[:limit]

    async def claim_due(self, now: datetime, limit: int = 20) -> list[Delivery]:
        due = sorted(
            (
                d
                for d in self._deliveries.values()
                if d.status is DeliveryStatus.PENDING
                and d.next_attempt_at is not None
                and d.next_attempt_at <= now
            ),
            key=lambda d: d.next_attempt_at or now,
        )[:limit]
        for d in due:
            d.claim(now)
        return due
