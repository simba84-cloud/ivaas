"""Platform and partner endpoints: the hierarchy above a tenant (proposal §3, M1).

Cassava's platform admins see every partner and tenant. A partner admin sees and
provisions only its own customers. Neither reaches a tenant's operational data
from here: that takes an account inside the tenant.

Provisioning is API-only in M1; the consoles that drive it are M8.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.schemas import PartnerOut, ProvisionIn, ProvisionOut, TenantOut
from ivaas.domain.audit import AuditAction
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission as P
from ivaas.domain.tenancy import ScopeType, Tenant
from ivaas.ports.auth import Principal
from ivaas.tenancy import system_context, tenant_context

Audit = Callable[..., Awaitable[None]]


def _partners_of(principal: Principal) -> set[UUID] | None:
    """The partners a caller administers; None means every one (platform scope)."""
    if any(b.scope_type is ScopeType.PLATFORM for b in principal.bindings):
        return None
    return {
        b.scope_id
        for b in principal.bindings
        if b.scope_type is ScopeType.PARTNER and b.scope_id is not None
    }


def _sees(principal: Principal, tenant: Tenant) -> bool:
    partners = _partners_of(principal)
    return partners is None or tenant.partner_id in partners


def add_platform_routes(
    app: FastAPI, get_container: Callable[[Request], Any], audit: Audit
) -> None:
    @app.get(
        "/api/v1/platform/partners",
        response_model=list[PartnerOut],
        dependencies=[Depends(require(P.TENANT_CREATE))],
    )
    async def list_partners(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> list[PartnerOut]:
        mine = _partners_of(principal)
        with system_context():
            partners = await c.tenants.list_partners()
        return [PartnerOut.of(p) for p in partners if mine is None or p.id in mine]

    @app.get(
        "/api/v1/platform/tenants",
        response_model=list[TenantOut],
        dependencies=[Depends(require(P.TENANT_CREATE))],
    )
    async def list_tenants(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> list[TenantOut]:
        with system_context():
            tenants = await c.tenants.list_all()
        return [TenantOut.of(t) for t in tenants if _sees(principal, t)]

    @app.get(
        "/api/v1/platform/tenants/{tenant_id}",
        response_model=TenantOut,
        dependencies=[Depends(require(P.TENANT_CREATE))],
    )
    async def get_tenant(
        tenant_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> TenantOut:
        with system_context():
            tenant = await c.tenants.get(tenant_id)
        if tenant is None or not _sees(principal, tenant):
            raise NotFoundError(f"tenant {tenant_id} not found")
        return TenantOut.of(tenant)

    @app.post(
        "/api/v1/platform/tenants",
        response_model=ProvisionOut,
        dependencies=[Depends(require(P.TENANT_CREATE))],
    )
    async def provision_tenant(
        body: ProvisionIn,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> ProvisionOut:
        """Create a tenant and its owner. Send the same Idempotency-Key to retry safely:
        a repeat returns the tenant already made, without the owner's password."""
        partner_id = body.partner_id
        mine = _partners_of(principal)
        if mine is not None:  # a partner provisions only its own customers
            if partner_id is None and len(mine) == 1:
                partner_id = next(iter(mine))
            if partner_id not in mine:
                raise HTTPException(403, "a partner can only create its own customers")

        result = await c.provision_tenant(
            idempotency_key=idempotency_key,
            slug=body.slug,
            name=body.name,
            partner_id=partner_id,
            owner_username=body.owner_username,
            owner_display_name=body.owner_display_name,
        )
        if not _sees(principal, result.tenant):
            # a key another partner used: say nothing about what it created
            raise HTTPException(409, "that Idempotency-Key has already been used")
        if result.created:
            detail = {
                "tenant_id": str(result.tenant.id),
                "partner_id": str(partner_id) if partner_id else None,
                "owner": result.record.owner_username,
                "pending": ",".join(s.name for s in result.record.steps if not s.done),
            }
            # once in the platform's log, once in the new tenant's own
            with system_context():
                await audit(c, principal.name, AuditAction.TENANT_PROVISIONED, body.slug, **detail)
            with tenant_context(result.tenant.id):
                await audit(c, principal.name, AuditAction.TENANT_PROVISIONED, body.slug, **detail)
        return ProvisionOut.of(
            result.record, result.tenant, result.temporary_password, result.created
        )
