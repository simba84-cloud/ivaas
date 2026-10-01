"""Billing use cases (M7): plans, the channel limit, usage, and month-end invoices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from ivaas.domain.billing import (
    BillingError,
    Entitlements,
    Invoice,
    PriceBook,
    Segment,
    Subscription,
    UsageEvent,
    check_channel,
    entitlements,
    month,
    rate,
)
from ivaas.domain.models import CameraRole
from ivaas.ports.billing import BillingStore
from ivaas.tenancy import require_tenant


def kind_of(role: CameraRole | str) -> str:
    return "lpr" if str(role) == CameraRole.LPR.value else "od"


@dataclass
class Billing:
    store: BillingStore
    book: PriceBook
    cameras: Any
    bays: Any
    clock: Any

    async def entitlements(self) -> Entitlements | None:
        sub = await self.store.subscription()
        return entitlements(self.book, sub, self.clock.now()) if sub else None

    async def channels_in_use(self) -> dict[str, int]:
        used = {"od": 0, "lpr": 0}
        for bay in await self.bays.list_all():
            for cam in await self.cameras.list_for_bay(bay.id):
                used[kind_of(cam.role)] += 1
        return used

    async def check_new_camera(self, role: CameraRole | str) -> None:
        """A hard limit, checked before the camera exists: refused with an upgrade."""
        kind = kind_of(role)
        check_channel(await self.entitlements(), kind, (await self.channels_in_use())[kind])

    async def set_plan(self, plan_id: str, quantities: dict[str, int], by: str) -> Subscription:
        plan = self.book.plan(plan_id)
        unknown = set(quantities) - set(plan.recurring)
        if unknown:
            raise BillingError(f"the {plan.name} plan has no {sorted(unknown)}")
        sub = await self.store.subscription() or Subscription(require_tenant())
        sub.change(Segment(self.clock.now(), plan_id, dict(quantities), by))
        await self.store.save_subscription(sub)
        return sub

    async def record_usage(self, meter: str, quantity: Decimal, at: datetime, key: str) -> bool:
        return await self.store.record(UsageEvent(meter, quantity, at, key))

    async def meter_channel_days(self) -> bool:
        """Yesterday's channels, once: the day's key makes running this every minute
        harmless. Recorded from the cameras registered when it runs, early the next day."""
        yesterday = self.clock.now().date() - timedelta(days=1)
        used = await self.channels_in_use()
        total = used["od"] + used["lpr"]
        at = datetime.combine(yesterday, time(12), self.clock.now().tzinfo)
        return await self.record_usage(
            "active_channel_days", Decimal(total), at, f"active_channel_days:{yesterday}"
        )

    async def draft(self, period: str) -> Invoice | None:
        """What the period would be invoiced at now; None without a subscription."""
        sub = await self.store.subscription()
        if sub is None:
            return None
        start, end = month(period)
        tz = self.clock.now().tzinfo
        usage = await self.store.usage(
            datetime.combine(start, time.min, tz),
            datetime.combine(end + timedelta(days=1), time.min, tz),
        )
        return rate(self.book, sub, start, end, usage)

    async def issue(self, period: str) -> Invoice:
        start, end = month(period)
        if end >= self.clock.now().date():
            raise BillingError("a month is invoiced once it is over")
        if await self.store.invoice_for(start, end) is not None:
            raise BillingError(f"{period} is already invoiced")
        invoice = await self.draft(period)
        if invoice is None:
            raise BillingError("this tenant has no subscription to invoice")
        invoice.number = await self.store.next_invoice_number(start.year)
        invoice.issued_at = self.clock.now()
        await self.store.save_invoice(invoice)
        return invoice


def period_of(day: date) -> str:
    return f"{day:%Y-%m}"
