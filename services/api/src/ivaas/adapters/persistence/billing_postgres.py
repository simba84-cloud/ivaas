"""Billing on Postgres (tenant-owned, row-level secured), and in memory.

The usage ledger is append-only with a unique (tenant, key): a replayed event is
refused by the database, not merely by the code. Invoice numbers come from a
sequence, so they are sequential and unique across every API process.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Numeric, String, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.billing import Invoice, Line, Segment, Subscription, UsageEvent
from ivaas.tenancy import require_tenant


class SubscriptionRow(Base):
    __tablename__ = "subscriptions"
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    segments: Mapped[list] = mapped_column(JSONB)


class UsageRow(Base):
    __tablename__ = "usage_events"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    meter: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key: Mapped[str] = mapped_column(String(200))


class InvoiceRow(Base):
    __tablename__ = "invoices"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(40))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    lines: Mapped[list] = mapped_column(JSONB)
    tax_name: Mapped[str] = mapped_column(String(20))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    price_book: Mapped[str] = mapped_column(String(60))
    placeholder: Mapped[bool] = mapped_column(Boolean)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _segments_out(sub: Subscription) -> list[dict]:
    return [
        {"starts": s.starts.isoformat(), "plan": s.plan, "quantities": s.quantities, "by": s.by}
        for s in sub.segments
    ]


def _segments_in(tenant_id: UUID, raw: list[dict]) -> Subscription:
    return Subscription(
        tenant_id,
        [
            Segment(datetime.fromisoformat(s["starts"]), s["plan"], dict(s["quantities"]), s["by"])
            for s in raw
        ],
    )


def _lines_out(inv: Invoice) -> list[dict]:
    return [
        {
            "sku": x.sku,
            "description": x.description,
            "quantity": str(x.quantity),
            "unit_price": str(x.unit_price),
            "amount": str(x.amount),
        }
        for x in inv.lines
    ]


def _invoice_in(r: InvoiceRow, tenant_id: UUID) -> Invoice:
    return Invoice(
        tenant_id=tenant_id,
        period_start=r.period_start,
        period_end=r.period_end,
        currency=r.currency,
        lines=[
            Line(
                x["sku"],
                x["description"],
                Decimal(x["quantity"]),
                Decimal(x["unit_price"]),
                Decimal(x["amount"]),
            )
            for x in r.lines
        ],
        tax_name=r.tax_name,
        tax_rate=Decimal(r.tax_rate),
        price_book=r.price_book,
        placeholder=r.placeholder,
        number=r.number,
        issued_at=r.issued_at,
        id=r.id,
    )


class PostgresBillingStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def subscription(self) -> Subscription | None:
        tenant = require_tenant()
        async with self._sm() as db:
            r = await db.get(SubscriptionRow, tenant)
        return _segments_in(tenant, r.segments) if r else None

    async def save_subscription(self, sub: Subscription) -> None:
        stmt = insert(SubscriptionRow).values(tenant_id=sub.tenant_id, segments=_segments_out(sub))
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id"], set_={"segments": stmt.excluded.segments}
        )
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def record(self, e: UsageEvent) -> bool:
        stmt = (
            insert(UsageRow)
            .values(id=e.id, meter=e.meter, quantity=e.quantity, at=e.at, key=e.key)
            .on_conflict_do_nothing(index_elements=["tenant_id", "key"])
        )
        async with self._sm.begin() as db:
            return bool((await db.execute(stmt)).rowcount)

    async def usage(self, start: datetime, end: datetime) -> dict[str, Decimal]:
        stmt = (
            select(UsageRow.meter, func.sum(UsageRow.quantity))
            .where(UsageRow.at >= start, UsageRow.at < end)
            .group_by(UsageRow.meter)
        )
        async with self._sm() as db:
            return {m: Decimal(q) for m, q in (await db.execute(stmt)).all()}

    async def next_invoice_number(self, year: int) -> str:
        async with self._sm.begin() as db:
            n = (await db.execute(text("SELECT nextval('invoice_number_seq')"))).scalar_one()
        return f"IVAAS-{year}-{n:06d}"

    async def save_invoice(self, inv: Invoice) -> None:
        async with self._sm.begin() as db:
            db.add(
                InvoiceRow(
                    id=inv.id,
                    number=inv.number,
                    period_start=inv.period_start,
                    period_end=inv.period_end,
                    currency=inv.currency,
                    lines=_lines_out(inv),
                    tax_name=inv.tax_name,
                    tax_rate=inv.tax_rate,
                    price_book=inv.price_book,
                    placeholder=inv.placeholder,
                    issued_at=inv.issued_at,
                )
            )

    async def invoices(self) -> list[Invoice]:
        tenant = require_tenant()
        async with self._sm() as db:
            rows = (
                await db.scalars(select(InvoiceRow).order_by(InvoiceRow.issued_at.desc()))
            ).all()
        return [_invoice_in(r, tenant) for r in rows]

    async def invoice_for(self, period_start, period_end) -> Invoice | None:
        tenant = require_tenant()
        stmt = select(InvoiceRow).where(
            InvoiceRow.period_start == period_start, InvoiceRow.period_end == period_end
        )
        async with self._sm() as db:
            r = (await db.scalars(stmt)).first()
        return _invoice_in(r, tenant) if r else None


_numbers = itertools.count(1)  # one platform-wide sequence, as the database's is


class InMemoryBillingStore:
    def __init__(self) -> None:
        self._sub: Subscription | None = None
        self._events: dict[str, UsageEvent] = {}
        self._invoices: list[Invoice] = []

    async def subscription(self) -> Subscription | None:
        return self._sub

    async def save_subscription(self, sub: Subscription) -> None:
        self._sub = sub

    async def record(self, e: UsageEvent) -> bool:
        if e.key in self._events:
            return False
        self._events[e.key] = e
        return True

    async def usage(self, start: datetime, end: datetime) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(Decimal)
        for e in self._events.values():
            if start <= e.at < end:
                out[e.meter] += e.quantity
        return dict(out)

    async def next_invoice_number(self, year: int) -> str:
        return f"IVAAS-{year}-{next(_numbers):06d}"

    async def save_invoice(self, inv: Invoice) -> None:
        self._invoices.append(inv)

    async def invoices(self) -> list[Invoice]:
        return sorted(self._invoices, key=lambda i: i.issued_at or datetime.min, reverse=True)

    async def invoice_for(self, period_start, period_end) -> Invoice | None:
        return next(
            (
                i
                for i in self._invoices
                if (i.period_start, i.period_end) == (period_start, period_end)
            ),
            None,
        )
