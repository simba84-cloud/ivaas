"""Ports for billing: subscriptions, the usage ledger and issued invoices."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from ivaas.domain.billing import Invoice, Subscription, UsageEvent


class BillingStore(Protocol):
    """Tenant-scoped, like every store."""

    async def subscription(self) -> Subscription | None: ...

    async def save_subscription(self, sub: Subscription) -> None: ...

    async def record(self, event: UsageEvent) -> bool:
        """Append it; False when its key was seen before (it is not counted twice)."""
        ...

    async def usage(self, start: datetime, end: datetime) -> dict[str, Decimal]:
        """Each meter's total over [start, end)."""
        ...

    async def next_invoice_number(self, year: int) -> str:
        """Sequential across the platform, as local invoicing rules want."""
        ...

    async def save_invoice(self, invoice: Invoice) -> None: ...

    async def invoices(self) -> list[Invoice]: ...

    async def invoice_for(self, period_start, period_end) -> Invoice | None: ...
