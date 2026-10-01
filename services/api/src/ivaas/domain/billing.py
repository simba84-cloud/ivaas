"""Plans, entitlements, metering and invoices (proposal §5, M7).

Money is Decimal, rounded half-up to the cent on each line, then taxed once on the
subtotal; floats never touch it. Prices come from a price book: SKUs, plans, the tax
rate and the currency. A price book that says it is a placeholder stamps every
invoice made from it, so a made-up price can never pass for a real one.

A subscription is a run of segments, each from a moment on, at a plan and quantities.
Changing quantities mid-period starts a segment; rating prorates each segment by the
days of the period it covers (T7.2). Entitlements are the current segment's limits,
enforced when something would exceed them, not only on the invoice (T7.3).

Usage is an append-only ledger keyed for idempotency, so a meter replayed or sent
twice counts once (T7.5). Soft limits (storage, assistant tokens) are allowed and
billed as overage above the plan's allowance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

CENT = Decimal("0.01")


class BillingError(ValueError):
    pass


class LimitReached(BillingError):  # noqa: N818 - it is the domain's word for it
    """A hard limit: refused, with what would lift it."""


def money(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


# --- the price book ---------------------------------------------------------------
@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    #: recurring SKUs and the quantity the plan includes by default
    recurring: dict[str, int]
    #: hard limits; od/lpr channels follow the subscription's quantities
    limits: dict[str, int]
    features: dict[str, bool]
    #: soft limits, per month: storage_gb, assistant_tokens
    allowances: dict[str, int]
    term_days: int | None = None  # a trial ends; None runs until changed


@dataclass(frozen=True)
class PriceBook:
    version: str
    currency: str
    tax_name: str
    tax_rate: Decimal
    #: recurring SKUs: price per unit per month; usage SKUs: price per unit
    prices: dict[str, Decimal]
    plans: dict[str, Plan]
    placeholder: bool

    @classmethod
    def load(cls, path: str | Path) -> PriceBook:
        raw = json.loads(Path(path).read_text())
        return cls.of(raw)

    @classmethod
    def of(cls, raw: dict[str, Any]) -> PriceBook:
        try:
            prices = {sku: Decimal(str(p["price"])) for sku, p in raw["skus"].items()}
            plans = {
                pid: Plan(
                    id=pid,
                    name=p["name"],
                    recurring=dict(p["recurring"]),
                    limits=dict(p.get("limits", {})),
                    features=dict(p.get("features", {})),
                    allowances=dict(p.get("allowances", {})),
                    term_days=p.get("term_days"),
                )
                for pid, p in raw["plans"].items()
            }
            book = cls(
                version=str(raw["version"]),
                currency=str(raw["currency"]),
                tax_name=str(raw["tax"]["name"]),
                tax_rate=Decimal(str(raw["tax"]["rate"])),
                prices=prices,
                plans=plans,
                placeholder=bool(raw.get("placeholder", True)),
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise BillingError(f"the price book is malformed: {exc}") from exc
        for plan in plans.values():
            missing = set(plan.recurring) - set(prices)
            if missing:
                raise BillingError(f"plan {plan.id} uses SKUs with no price: {sorted(missing)}")
        if any(p < 0 for p in prices.values()) or not (0 <= book.tax_rate < 1):
            raise BillingError("prices cannot be negative, and tax is a rate between 0 and 1")
        return book

    def plan(self, plan_id: str) -> Plan:
        if plan_id not in self.plans:
            raise BillingError(f"no plan {plan_id!r}; there are {sorted(self.plans)}")
        return self.plans[plan_id]


# --- subscriptions and entitlements -------------------------------------------------
OD, LPR = "ivaas-od-count", "ivaas-lpr"


@dataclass(frozen=True)
class Segment:
    """From `starts` on, this plan at these quantities (by SKU)."""

    starts: datetime
    plan: str
    quantities: dict[str, int]
    by: str = ""


@dataclass
class Subscription:
    tenant_id: UUID
    segments: list[Segment] = field(default_factory=list)

    def current(self, at: datetime) -> Segment | None:
        # segments are kept in the order they were made, starts never going back, so the
        # last one begun is in force; two changes in the same instant: the later wins
        live = [s for s in self.segments if s.starts <= at]
        return live[-1] if live else None

    def change(self, segment: Segment) -> None:
        if self.segments and segment.starts < max(s.starts for s in self.segments):
            raise BillingError("a change takes effect now or later, never before the last one")
        for sku, n in segment.quantities.items():
            if n < 0:
                raise BillingError(f"{sku}: a quantity cannot be negative")
        self.segments.append(segment)


@dataclass(frozen=True)
class Entitlements:
    plan: str
    valid_until: datetime | None
    limits: dict[str, int]
    features: dict[str, bool]
    allowances: dict[str, int]

    def allows(self, limit: str, wanted: int) -> bool:
        return limit not in self.limits or wanted <= self.limits[limit]


def entitlements(book: PriceBook, sub: Subscription, at: datetime) -> Entitlements | None:
    """What the tenant may do now; None when it has no subscription (unmetered)."""
    seg = sub.current(at)
    if seg is None:
        return None
    plan = book.plan(seg.plan)
    limits = dict(plan.limits)
    limits["od_channels"] = seg.quantities.get(OD, plan.recurring.get(OD, 0))
    limits["lpr_channels"] = seg.quantities.get(LPR, plan.recurring.get(LPR, 0))
    first = min(s.starts for s in sub.segments if s.plan == seg.plan)
    until = first + timedelta(days=plan.term_days) if plan.term_days else None
    return Entitlements(seg.plan, until, limits, plan.features, plan.allowances)


def check_channel(ents: Entitlements | None, kind: str, in_use: int) -> None:
    """Adding one more channel of `kind` ("od" or "lpr"): refused past the plan."""
    if ents is None:
        return  # no subscription: nothing is limited, nothing is billed
    limit = f"{kind}_channels"
    if not ents.allows(limit, in_use + 1):
        name = "counting" if kind == "od" else "plate-reading"
        raise LimitReached(
            f"the {ents.plan} plan includes {ents.limits[limit]} {name} channel(s), and all are "
            f"in use. Upgrade the plan to add another."
        )


# --- metering -------------------------------------------------------------------------
METERS = ("active_channel_days", "lpr_reads", "api_calls", "storage_gb_month", "assistant_tokens")


@dataclass(frozen=True)
class UsageEvent:
    meter: str
    quantity: Decimal
    at: datetime
    key: str  # idempotency: the same key, from anywhere, counts once
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.meter not in METERS:
            raise BillingError(f"no meter {self.meter!r}; there are {list(METERS)}")
        if self.quantity < 0:
            raise BillingError("usage cannot be negative")
        if not self.key.strip():
            raise BillingError("a usage event needs an idempotency key")


# --- invoices -------------------------------------------------------------------------
@dataclass(frozen=True)
class Line:
    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


@dataclass
class Invoice:
    tenant_id: UUID
    period_start: date
    period_end: date  # inclusive
    currency: str
    lines: list[Line]
    tax_name: str
    tax_rate: Decimal
    price_book: str
    placeholder: bool
    number: str | None = None  # given when issued
    issued_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    @property
    def subtotal(self) -> Decimal:
        return money(sum((line.amount for line in self.lines), Decimal(0)))

    @property
    def tax(self) -> Decimal:
        return money(self.subtotal * self.tax_rate)

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax


def month(period: str) -> tuple[date, date]:
    """'2026-10' -> (1 Oct, 31 Oct)."""
    try:
        start = date.fromisoformat(f"{period}-01")
    except ValueError as exc:
        raise BillingError("a billing period is YYYY-MM") from exc
    nxt = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def _days(a: date, b: date) -> int:
    return (b - a).days + 1


def rate(
    book: PriceBook,
    sub: Subscription,
    start: date,
    end: date,
    usage: dict[str, Decimal],
) -> Invoice:
    """The invoice for [start, end]: each recurring SKU prorated by the days each
    segment covers, then overage above the allowances, by the plan in force at the
    period's end."""
    period_days = Decimal(_days(start, end))
    segments = sorted(sub.segments, key=lambda s: s.starts)
    # each segment's share of the period: (from, to, plan, quantities)
    spans: list[tuple[date, date, Plan, dict[str, int]]] = []
    for i, seg in enumerate(segments):
        plan = book.plan(seg.plan)
        seg_from = max(seg.starts.date(), start)
        seg_to = end
        if i + 1 < len(segments):
            seg_to = min(seg_to, segments[i + 1].starts.date() - timedelta(days=1))
        if plan.term_days:
            seg_to = min(seg_to, seg.starts.date() + timedelta(days=plan.term_days - 1))
        if seg_to >= seg_from:
            qty = {sku: seg.quantities.get(sku, n) for sku, n in plan.recurring.items()}
            spans.append((seg_from, seg_to, plan, qty))
    # a SKU whose plan and quantity did not change stays one line; a change splits it
    lines: list[Line] = []
    for sku in dict.fromkeys(k for _, _, _, q in spans for k in q):
        runs: list[list] = []
        for seg_from, seg_to, plan, qty in spans:
            n = qty.get(sku, 0)
            last = runs[-1] if runs else None
            if (
                last
                and last[2] == plan.name
                and last[3] == n
                and last[1] + timedelta(days=1) == seg_from
            ):
                last[1] = seg_to
            else:
                runs.append([seg_from, seg_to, plan.name, n])
        for run_from, run_to, plan_name, n in runs:
            if n == 0:
                continue
            days = _days(run_from, run_to)
            price = book.prices[sku]
            amount = money(Decimal(n) * price * Decimal(days) / period_days)
            whole = days == int(period_days)
            span = (
                ""
                if whole
                else f", {run_from:%d %b} to {run_to:%d %b} ({days} of {int(period_days)} days)"
            )
            lines.append(Line(sku, f"{plan_name}: {sku} x {n}{span}", Decimal(n), price, amount))
    # overage is judged by the plan in force at the period's end
    in_force = [s for s in segments if s.starts.date() <= end]
    allowances = book.plan(in_force[-1].plan).allowances if in_force else {}
    for meter, sku, unit in (
        ("storage_gb_month", "ivaas-storage-overage", Decimal(1)),
        ("assistant_tokens", "ivaas-assistant", Decimal(1000)),
    ):
        used = usage.get(meter, Decimal(0))
        over = max(used - Decimal(allowances.get(meter, 0)), Decimal(0))
        if over > 0 and sku in book.prices:
            units = over / unit
            lines.append(
                Line(
                    sku,
                    f"{meter.replace('_', ' ')} above the allowance: {over:f}",
                    units,
                    book.prices[sku],
                    money(units * book.prices[sku]),
                )
            )
    return Invoice(
        tenant_id=sub.tenant_id,
        period_start=start,
        period_end=end,
        currency=book.currency,
        lines=lines,
        tax_name=book.tax_name,
        tax_rate=book.tax_rate,
        price_book=book.version,
        placeholder=book.placeholder,
    )
