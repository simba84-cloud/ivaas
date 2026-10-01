"""Billing over HTTP (M7): the plan and its limits, usage, and invoices.

A tenant's owner sees and changes its plan and reads its invoices; platform billing
staff set any tenant's plan and issue month-end invoices; services post usage, each
event with an idempotency key so a replay counts once. An invoice made from a
placeholder price book says so on every line of the way: it is not for issue.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.platform_routes import _sees
from ivaas.application.billing import period_of
from ivaas.domain.audit import AuditAction
from ivaas.domain.billing import METERS, BillingError, Entitlements, Invoice
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal
from ivaas.tenancy import current_tenant, system_context, tenant_context

Audit = Callable[..., Awaitable[None]]
NOT_FOR_ISSUE = "PLACEHOLDER PRICES: NOT FOR ISSUE"


class PlanOut(BaseModel):
    id: str
    name: str
    recurring: dict[str, int]
    limits: dict[str, int]
    features: dict[str, bool]
    allowances: dict[str, int]
    term_days: int | None


class PriceBookOut(BaseModel):
    version: str
    #: true for made-up prices: invoices from it are stamped not for issue
    placeholder: bool
    currency: str
    tax_name: str
    tax_rate: str
    prices: dict[str, str]
    plans: list[PlanOut]


class EntitlementsOut(BaseModel):
    plan: str
    valid_until: datetime | None
    limits: dict[str, int]
    features: dict[str, bool]
    allowances: dict[str, int]

    @classmethod
    def of(cls, e: Entitlements) -> EntitlementsOut:
        return cls(**vars(e))


class SegmentOut(BaseModel):
    starts: datetime
    plan: str
    quantities: dict[str, int]
    by: str


class SubscriptionOut(BaseModel):
    #: false: no plan, so nothing is limited and nothing is billed
    subscribed: bool
    entitlements: EntitlementsOut | None
    segments: list[SegmentOut]
    channels_in_use: dict[str, int]
    usage_this_month: dict[str, str]


class PlanIn(BaseModel):
    plan: str = Field(min_length=1, max_length=40)
    #: by SKU, e.g. {"ivaas-od-count": 24}; the plan's defaults otherwise
    quantities: dict[str, int] = Field(default_factory=dict)


class UsageIn(BaseModel):
    meter: str
    quantity: Decimal = Field(ge=0)
    at: datetime
    key: str = Field(min_length=1, max_length=200)


class UsageOut(BaseModel):
    #: false when the key was seen before: the event is not counted twice
    counted: bool


class LineOut(BaseModel):
    sku: str
    description: str
    quantity: str
    unit_price: str
    amount: str


class InvoiceOut(BaseModel):
    number: str | None
    period_start: date
    period_end: date
    currency: str
    lines: list[LineOut]
    subtotal: str
    tax_name: str
    tax_rate: str
    tax: str
    total: str
    price_book: str
    placeholder: bool
    #: set on every invoice from a placeholder price book
    stamp: str | None
    issued_at: datetime | None

    @classmethod
    def of(cls, inv: Invoice) -> InvoiceOut:
        return cls(
            number=inv.number,
            period_start=inv.period_start,
            period_end=inv.period_end,
            currency=inv.currency,
            lines=[
                LineOut(
                    sku=x.sku,
                    description=x.description,
                    quantity=str(x.quantity.normalize()),
                    unit_price=str(x.unit_price),
                    amount=str(x.amount),
                )
                for x in inv.lines
            ],
            subtotal=str(inv.subtotal),
            tax_name=inv.tax_name,
            tax_rate=str(inv.tax_rate),
            tax=str(inv.tax),
            total=str(inv.total),
            price_book=inv.price_book,
            placeholder=inv.placeholder,
            stamp=NOT_FOR_ISSUE if inv.placeholder else None,
            issued_at=inv.issued_at,
        )


async def own_tenant() -> None:
    """A tenant's own billing needs a tenant: platform and partner staff, who hold the
    billing permissions without one, are pointed to the routes that name the tenant."""
    if current_tenant() is None:
        raise HTTPException(
            403, "this is a tenant's own billing; use /api/v1/platform/tenants/{id}/..."
        )


def add_billing_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    async def subscription_out(c: Any) -> SubscriptionOut:
        billing = c.billing()
        sub = await billing.store.subscription()
        ents = await billing.entitlements()
        draft = await billing.draft(period_of(c.clock.now().date())) if sub else None
        usage = (
            await billing.store.usage(
                datetime.combine(draft.period_start, datetime.min.time(), c.clock.now().tzinfo),
                c.clock.now(),
            )
            if draft
            else {}
        )
        return SubscriptionOut(
            subscribed=sub is not None,
            entitlements=EntitlementsOut.of(ents) if ents else None,
            segments=[SegmentOut(**vars(s)) for s in (sub.segments if sub else [])],
            channels_in_use=await billing.channels_in_use(),
            usage_this_month={k: f"{v.normalize():f}" for k, v in usage.items()},  # never 2.5E+5
        )

    @app.get(
        "/api/v1/billing/price-book",
        response_model=PriceBookOut,
        dependencies=[Depends(require(P.INVOICE_READ))],
    )
    async def price_book(c: Any = Depends(get_container)) -> PriceBookOut:
        b = c.price_book
        return PriceBookOut(
            version=b.version,
            placeholder=b.placeholder,
            currency=b.currency,
            tax_name=b.tax_name,
            tax_rate=str(b.tax_rate),
            prices={k: str(v) for k, v in b.prices.items()},
            plans=[PlanOut(**vars(p)) for p in b.plans.values()],
        )

    @app.get(
        "/api/v1/billing/subscription",
        response_model=SubscriptionOut,
        dependencies=[Depends(require(P.INVOICE_READ)), Depends(own_tenant)],
    )
    async def subscription(c: Any = Depends(get_container)) -> SubscriptionOut:
        return await subscription_out(c)

    @app.put(
        "/api/v1/billing/subscription",
        response_model=SubscriptionOut,
        dependencies=[Depends(require(P.SUBSCRIPTION_MANAGE)), Depends(own_tenant)],
    )
    async def change_subscription(
        body: PlanIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> SubscriptionOut:
        """The tenant's own plan or quantities, from now: an upgrade after a refusal."""
        try:
            await c.billing().set_plan(body.plan, body.quantities, principal.name)
        except BillingError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            c,
            principal.name,
            AuditAction.SUBSCRIPTION_CHANGED,
            body.plan,
            quantities=body.quantities,
        )
        return await subscription_out(c)

    @app.post(
        "/api/v1/billing/usage",
        response_model=UsageOut,
        status_code=201,
        responses={200: {"model": UsageOut, "description": "seen before: not counted again"}},
        dependencies=[Depends(require(P.INGEST_WRITE))],
    )
    async def record_usage(body: UsageIn, c: Any = Depends(get_container)) -> Any:
        if body.meter not in METERS:
            raise HTTPException(422, f"no meter {body.meter!r}; there are {list(METERS)}")
        counted = await c.billing().record_usage(body.meter, body.quantity, body.at, body.key)
        return UsageOut(counted=True) if counted else JSONResponse({"counted": False}, 200)

    @app.get(
        "/api/v1/billing/invoices/draft",
        response_model=InvoiceOut,
        dependencies=[Depends(require(P.INVOICE_READ)), Depends(own_tenant)],
    )
    async def draft_invoice(
        period: str = Query(pattern=r"^\d{4}-\d{2}$"), c: Any = Depends(get_container)
    ) -> InvoiceOut:
        """What the month comes to now, as the invoice would show it. Not issued."""
        try:
            inv = await c.billing().draft(period)
        except BillingError as exc:
            raise HTTPException(422, str(exc)) from exc
        if inv is None:
            raise HTTPException(404, "no subscription: nothing is billed")
        return InvoiceOut.of(inv)

    @app.get(
        "/api/v1/billing/invoices",
        response_model=list[InvoiceOut],
        dependencies=[Depends(require(P.INVOICE_READ)), Depends(own_tenant)],
    )
    async def invoices(c: Any = Depends(get_container)) -> list[InvoiceOut]:
        return [InvoiceOut.of(i) for i in await c.billing_store.invoices()]

    # --- platform billing -------------------------------------------------------------
    async def billed_tenant(c: Any, principal: Principal, tenant_id: UUID) -> None:
        """A tenant the caller may bill: platform staff any, a partner its own."""
        with system_context():
            tenant = await c.tenants.get(tenant_id)
        if tenant is None or not _sees(principal, tenant):
            raise NotFoundError(f"tenant {tenant_id} not found")

    @app.put(
        "/api/v1/platform/tenants/{tenant_id}/subscription",
        response_model=SubscriptionOut,
        dependencies=[Depends(require(P.SUBSCRIPTION_MANAGE))],
    )
    async def set_tenant_plan(
        tenant_id: UUID,
        body: PlanIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> SubscriptionOut:
        await billed_tenant(c, principal, tenant_id)
        with tenant_context(tenant_id):
            try:
                await c.billing().set_plan(body.plan, body.quantities, principal.name)
            except BillingError as exc:
                raise HTTPException(422, str(exc)) from exc
            await audit(
                c,
                principal.name,
                AuditAction.SUBSCRIPTION_CHANGED,
                body.plan,
                quantities=body.quantities,
            )
            return await subscription_out(c)

    @app.get(
        "/api/v1/platform/tenants/{tenant_id}/subscription",
        response_model=SubscriptionOut,
        dependencies=[Depends(require(P.SUBSCRIPTION_MANAGE))],
    )
    async def tenant_plan(
        tenant_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> SubscriptionOut:
        await billed_tenant(c, principal, tenant_id)
        with tenant_context(tenant_id):
            return await subscription_out(c)

    @app.get(
        "/api/v1/platform/tenants/{tenant_id}/invoices/draft",
        response_model=InvoiceOut,
        dependencies=[Depends(require(P.SUBSCRIPTION_MANAGE))],
    )
    async def tenant_draft(
        tenant_id: UUID,
        period: str = Query(pattern=r"^\d{4}-\d{2}$"),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> InvoiceOut:
        await billed_tenant(c, principal, tenant_id)
        with tenant_context(tenant_id):
            inv = await c.billing().draft(period)
        if inv is None:
            raise HTTPException(404, "no subscription: nothing is billed")
        return InvoiceOut.of(inv)

    @app.post(
        "/api/v1/platform/tenants/{tenant_id}/invoices",
        response_model=InvoiceOut,
        status_code=201,
        dependencies=[Depends(require(P.SUBSCRIPTION_MANAGE))],
    )
    async def issue_invoice(
        tenant_id: UUID,
        period: str = Query(pattern=r"^\d{4}-\d{2}$"),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> InvoiceOut:
        """A finished month, numbered. Once per tenant and month."""
        await billed_tenant(c, principal, tenant_id)
        with tenant_context(tenant_id):
            try:
                inv = await c.billing().issue(period)
            except BillingError as exc:
                raise HTTPException(409 if "already" in str(exc) else 422, str(exc)) from exc
            await audit(
                c,
                principal.name,
                AuditAction.INVOICE_ISSUED,
                inv.number or "",
                period=period,
                total=str(inv.total),
                placeholder=inv.placeholder,
            )
            return InvoiceOut.of(inv)
