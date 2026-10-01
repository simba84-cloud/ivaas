"""Billing use cases (M7): plans, the channel limit, usage, and month-end invoices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from ivaas.domain.billing import (
    BillingError,
    Entitlements,
    Invoice,
    PartnerInvoice,
    Payment,
    PriceBook,
    Segment,
    Subscription,
    UsageEvent,
    billing_status,
    check_channel,
    entitlements,
    month,
    rate,
    wholesale,
)
from ivaas.domain.models import CameraRole
from ivaas.domain.tenancy import Tenant, TenantStatus
from ivaas.ports.billing import BillingStore
from ivaas.tenancy import object_key, require_tenant, system_context, tenant_context


def kind_of(role: CameraRole | str) -> str:
    return "lpr" if str(role) == CameraRole.LPR.value else "od"


@dataclass
class Billing:
    store: BillingStore
    book: PriceBook
    cameras: Any
    bays: Any
    clock: Any
    tenants: Any = None
    partner_invoices: Any = None
    objects: Any = None

    async def tenant(self) -> Tenant:
        with system_context():
            tenant = await self.tenants.get(require_tenant())
        if tenant is None:
            raise BillingError("no such tenant")
        return tenant

    async def partner_billed(self) -> bool:
        """Its partner invoices it (LITZIM invoices Bakers Inn); Cassava invoices the partner."""
        return (await self.tenant()).partner_id is not None

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
        if self.tenants is not None:
            await self.refresh_status()  # a trial put on a paid plan is active (T7.8)
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

    async def meter_storage(self) -> bool:
        """Yesterday's share of a GB-month: the tenant's stored GB over the days in that
        month, so a month of these sums to its average. Once a day, by its key."""
        if self.objects is None:
            return False
        yesterday = self.clock.now().date() - timedelta(days=1)
        stored = await self.objects.size(object_key(""))
        start, end = month(period_of(yesterday))
        days = (end - start).days + 1
        gb_month = Decimal(stored) / Decimal(1024**3) / Decimal(days)
        at = datetime.combine(yesterday, time(12), self.clock.now().tzinfo)
        return await self.record_usage(
            "storage_gb_month",
            gb_month.quantize(Decimal("0.000001")),
            at,
            f"storage_gb_month:{yesterday}",
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
        if await self.partner_billed():
            raise BillingError(
                "this tenant's partner invoices it; Cassava invoices the partner wholesale"
            )
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
        invoice.due_date = invoice.issued_at.date() + timedelta(days=self.book.payment_days)
        await self.store.save_invoice(invoice)
        await self.refresh_status()
        return invoice

    async def pay(self, number: str, amount: Decimal, reference: str, by: str) -> Invoice:
        """A bank transfer, reconciled by hand: the POC's way of being paid."""
        invoice = await self.store.invoice(number)
        if invoice is None:
            raise BillingError(f"no invoice {number}")
        invoice.pay(Payment(amount, reference, self.clock.now(), by))
        await self.store.save_invoice(invoice)
        await self.refresh_status()
        return invoice

    async def refresh_status(self) -> tuple[str, str] | None:
        """Put the tenant where its invoices, its partner's invoices and any hold say it
        belongs. (old, new) when that moved it; None when it stayed."""
        tenant = await self.tenant()
        overdue = [i.due_date for i in await self.store.invoices() if i.due_date and not i.settled]
        if tenant.partner_id is not None and self.partner_invoices is not None:
            with system_context():
                theirs = await self.partner_invoices.for_partner(tenant.partner_id)
            overdue += [i.due_date for i in theirs if i.due_date and not i.settled]
        sub = await self.store.subscription()
        ents = entitlements(self.book, sub, self.clock.now()) if sub else None
        new = billing_status(
            current=tenant.status.value,
            on_trial=bool(ents and ents.valid_until),
            subscribed=sub is not None,
            overdue_since=overdue,
            on_hold=tenant.on_hold,
            today=self.clock.now().date(),
            grace_days=self.book.grace_days,
        )
        if new == tenant.status.value:
            return None
        old, tenant.status = tenant.status.value, TenantStatus(new)
        with system_context():
            await self.tenants.save(tenant)
        return old, new


def period_of(day: date) -> str:
    return f"{day:%Y-%m}"


@dataclass
class PartnerBilling:
    """A partner's wholesale invoice, made from each of its customers' plans and usage
    (T7.9), and the holds it may put on one of them (T7.10)."""

    tenants: Any
    partner_invoices: Any
    billing_for: Any  # () -> Billing, in whatever tenant is in context
    book: PriceBook
    clock: Any

    async def _partner(self, partner_id: UUID):
        with system_context():
            partner = await self.tenants.get_partner(partner_id)
            customers = await self.tenants.list_all(partner_id=partner_id)
        if partner is None:
            raise BillingError(f"no partner {partner_id}")
        return partner, customers

    async def draft(self, partner_id: UUID, period: str) -> PartnerInvoice:
        partner, customers = await self._partner(partner_id)
        start, end = month(period)
        tz = self.clock.now().tzinfo
        rated = []
        for t in customers:
            with tenant_context(t.id):
                store = self.billing_for().store
                sub = await store.subscription()
                if sub is None:
                    continue
                usage = await store.usage(
                    datetime.combine(start, time.min, tz),
                    datetime.combine(end + timedelta(days=1), time.min, tz),
                )
            rated.append((t.name, sub, usage))
        return wholesale(self.book, partner.id, partner.slug, partner.name, rated, start, end)

    async def issue(self, partner_id: UUID, period: str) -> PartnerInvoice:
        start, end = month(period)
        if end >= self.clock.now().date():
            raise BillingError("a month is invoiced once it is over")
        with system_context():
            done = await self.partner_invoices.for_partner(partner_id)
        if any((i.period_start, i.period_end) == (start, end) for i in done):
            raise BillingError(f"{period} is already invoiced")
        invoice = await self.draft(partner_id, period)
        with system_context():
            invoice.number = await self.billing_for().store.next_invoice_number(start.year)
        invoice.issued_at = self.clock.now()
        invoice.due_date = invoice.issued_at.date() + timedelta(days=self.book.payment_days)
        with system_context():
            await self.partner_invoices.save(invoice)
        await self.refresh(partner_id)
        return invoice

    async def pay(self, number: str, amount: Decimal, reference: str, by: str) -> PartnerInvoice:
        with system_context():
            invoice = await self.partner_invoices.by_number(number)
        if invoice is None:
            raise BillingError(f"no invoice {number}")
        invoice.pay(Payment(amount, reference, self.clock.now(), by))
        with system_context():
            await self.partner_invoices.save(invoice)
        await self.refresh(invoice.partner_id)
        return invoice

    async def hold(self, tenant_id: UUID, on: bool) -> tuple[str, str] | None:
        with system_context():
            tenant = await self.tenants.get(tenant_id)
            tenant.on_hold = on
            await self.tenants.save(tenant)
        with tenant_context(tenant_id):
            return await self.billing_for().refresh_status()

    async def refresh(self, partner_id: UUID) -> dict[UUID, tuple[str, str]]:
        """Every customer of the partner, re-placed: its unpaid invoice is theirs too."""
        _, customers = await self._partner(partner_id)
        moved = {}
        for t in customers:
            with tenant_context(t.id):
                change = await self.billing_for().refresh_status()
            if change:
                moved[t.id] = change
        return moved
