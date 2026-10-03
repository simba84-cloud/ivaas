"""What Cassava's console shows across tenants (proposal §6): the fleet's health and
the revenue invoiced. Both read what exists and say plainly where nothing does.

The fleet is node-level operational state (alive, which cameras stream, how much is
queued): what an installer or support needs, never a tenant's counts.

Revenue is what Cassava has issued, to its direct customers and to its partners
wholesale. A month with nothing issued is "nothing issued", not a revenue of zero: the
month may not be invoiced yet. Drafts are not revenue.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from ivaas.domain.billing import money
from ivaas.domain.edge import NodeHealth
from ivaas.domain.tenancy import Tenant
from ivaas.tenancy import system_context, tenant_context


# --- fleet -------------------------------------------------------------------------------
@dataclass(frozen=True)
class NodeLine:
    id: UUID
    name: str
    health: NodeHealth
    last_seen_at: datetime | None
    version: str | None
    #: from its last report; None when it has never reported its cameras
    cameras_reported: int | None
    cameras_connected: int | None
    spool_pending: int | None


@dataclass(frozen=True)
class TenantFleet:
    tenant: Tenant
    nodes: list[NodeLine]

    @property
    def health(self) -> Counter:
        return Counter(n.health.value for n in self.nodes)

    @property
    def needs_attention(self) -> bool:
        """A node that is not online, or an online one dropping a camera. A revoked node
        is retired: its last report is history and flags nothing (found live: sixteen
        revoked test nodes flagged a tenant). Never true with no nodes either: that is
        "nothing installed", shown as such."""
        for n in self.nodes:
            if n.health is NodeHealth.REVOKED:
                continue
            if n.health is not NodeHealth.ONLINE:
                return True
            if n.cameras_connected is not None and n.cameras_connected < (n.cameras_reported or 0):
                return True
        return False


async def fleet(tenants: list[Tenant], edge: Any, now: datetime) -> list[TenantFleet]:
    out = []
    for t in tenants:
        with tenant_context(t.id):
            nodes = await edge.list_nodes()
        lines = []
        for n in nodes:
            cams = n.cameras if "cameras" in n.last_report else None
            lines.append(
                NodeLine(
                    id=n.id,
                    name=n.name,
                    health=n.health(now),
                    last_seen_at=n.last_seen_at,
                    version=n.last_report.get("version") or None,
                    cameras_reported=len(cams) if cams is not None else None,
                    cameras_connected=sum(c.connected for c in cams) if cams is not None else None,
                    spool_pending=n.last_report.get("spool_pending"),
                )
            )
        out.append(TenantFleet(t, lines))
    return out


# --- revenue -----------------------------------------------------------------------------
@dataclass
class MonthRevenue:
    period: str  # YYYY-MM
    invoices: int = 0
    subtotal: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    total: Decimal = Decimal(0)
    paid: Decimal = Decimal(0)
    direct: Decimal = Decimal(0)  # to Cassava's own customers, before tax
    wholesale: Decimal = Decimal(0)  # to partners, before tax
    placeholder: bool = False

    @property
    def outstanding(self) -> Decimal:
        return money(self.total - self.paid)


@dataclass
class Revenue:
    months: list[MonthRevenue]
    currency: str
    #: issued invoices whose due date has passed unpaid, as of today
    overdue: int = 0
    overdue_amount: Decimal = Decimal(0)
    placeholder: bool = False
    by_payer: dict[str, Decimal] = field(default_factory=dict)


def _months(today: date, count: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return list(reversed(out))


async def revenue(
    tenants: list[Tenant],
    partners: list[Any],
    billing_store: Any,
    partner_invoices: Any,
    today: date,
    months: int,
    currency: str,
) -> Revenue:
    shown = {p: MonthRevenue(p) for p in _months(today, months)}
    result = Revenue(list(shown.values()), currency)

    def add(inv: Any, kind: str, payer: str) -> None:
        if inv.number is None:  # a draft is not revenue
            return
        if inv.due_date and inv.due_date < today and not inv.settled:
            result.overdue += 1
            result.overdue_amount += inv.total - inv.paid
        month = shown.get(f"{inv.period_start:%Y-%m}")
        if month is None:
            return
        month.invoices += 1
        month.subtotal += inv.subtotal
        month.tax += inv.tax
        month.total += inv.total
        month.paid += inv.paid
        setattr(month, kind, getattr(month, kind) + inv.subtotal)
        month.placeholder |= bool(inv.placeholder)
        result.placeholder |= bool(inv.placeholder)
        result.by_payer[payer] = result.by_payer.get(payer, Decimal(0)) + inv.subtotal

    for t in tenants:
        with tenant_context(t.id):
            for inv in await billing_store.invoices():
                add(inv, "direct", t.name)
    for p in partners:
        with system_context():
            for inv in await partner_invoices.for_partner(p.id):
                add(inv, "wholesale", p.name)
    result.overdue_amount = money(result.overdue_amount)
    return result
