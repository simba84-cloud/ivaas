"""Break-glass support access (M8, T8.3): support asks, the tenant's owner decides.

Support works from the platform routes: it asks a tenant, lists its own requests, and
ends its access early. The owner works from the tenant's own routes: it sees every
request made of its tenant, approves or refuses, and ends access at any time. Using
an approved grant is the `X-IVaaS-Break-Glass` header (adapters/http/auth.py).

Each step goes into the tenant's audit log, which is where the owner looks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ivaas.adapters.http.auth import current_principal, require
from ivaas.domain.audit import AuditAction
from ivaas.domain.break_glass import BreakGlassError, BreakGlassGrant, GrantState
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal
from ivaas.tenancy import require_tenant, system_context, tenant_context

Audit = Callable[..., Awaitable[None]]


class BreakGlassIn(BaseModel):
    #: what support needs to look at and why; the owner decides on this
    reason: str = Field(min_length=10, max_length=500)
    minutes: int = Field(ge=15, le=480)


class GrantOut(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_name: str
    requested_by: str
    reason: str
    minutes: int
    requested_at: datetime
    state: GrantState
    decided_by: str | None
    decided_at: datetime | None
    #: when approved access ends; null until approved
    expires_at: datetime | None
    ended_by: str | None
    ended_at: datetime | None


def add_break_glass_routes(
    app: FastAPI, get_container: Callable[[Request], Any], audit: Audit
) -> None:
    async def out(c: Any, g: BreakGlassGrant) -> GrantOut:
        with system_context():
            tenant = await c.tenants.get(g.tenant_id)
        return GrantOut(
            id=g.id,
            tenant_id=g.tenant_id,
            tenant_name=tenant.name if tenant else "",
            requested_by=g.requested_by,
            reason=g.reason,
            minutes=int(g.duration.total_seconds() // 60),
            requested_at=g.requested_at,
            state=g.state(c.clock.now()),
            decided_by=g.decided_by,
            decided_at=g.decided_at,
            expires_at=g.expires_at,
            ended_by=g.ended_by,
            ended_at=g.ended_at,
        )

    # --- support ------------------------------------------------------------------
    @app.post(
        "/api/v1/platform/tenants/{tenant_id}/break-glass",
        response_model=GrantOut,
        status_code=201,
        dependencies=[Depends(require(P.SUPPORT_REQUEST))],
    )
    async def request_access(
        tenant_id: UUID,
        body: BreakGlassIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> GrantOut:
        """Ask the tenant's owner for read-only access, for this long, for this reason."""
        with system_context():
            if await c.tenants.get(tenant_id) is None:
                raise NotFoundError(f"tenant {tenant_id} not found")
            mine = [
                g
                for g in await c.break_glass.list_all()
                if g.tenant_id == tenant_id and g.requested_by == principal.subject
            ]
        now = c.clock.now()
        if any(g.state(now) in (GrantState.PENDING, GrantState.ACTIVE) for g in mine):
            raise HTTPException(409, "you already have a request open with this tenant")
        try:
            grant = BreakGlassGrant.request(
                tenant_id, principal.subject, body.reason, timedelta(minutes=body.minutes), now
            )
        except BreakGlassError as exc:
            raise HTTPException(422, str(exc)) from exc
        with tenant_context(tenant_id):
            await c.break_glass.save(grant)
            await audit(
                c,
                principal.name,
                AuditAction.BREAK_GLASS_REQUESTED,
                principal.subject,
                grant=str(grant.id),
                minutes=body.minutes,
                reason=grant.reason,
            )
        return await out(c, grant)

    @app.get(
        "/api/v1/platform/break-glass",
        response_model=list[GrantOut],
        dependencies=[Depends(require(P.SUPPORT_REQUEST))],
    )
    async def my_requests(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> list[GrantOut]:
        """The caller's own requests, every tenant: never anyone else's."""
        with system_context():
            grants = await c.break_glass.list_all()
        return [await out(c, g) for g in grants if g.requested_by == principal.subject]

    @app.post(
        "/api/v1/platform/break-glass/{grant_id}/end",
        response_model=GrantOut,
        dependencies=[Depends(require(P.SUPPORT_REQUEST))],
    )
    async def end_my_access(
        grant_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> GrantOut:
        """Done early, or a request withdrawn."""
        with system_context():
            grant = await c.break_glass.get(grant_id)
        if grant is None or grant.requested_by != principal.subject:
            raise NotFoundError(f"grant {grant_id} not found")
        return await _end(c, grant, principal)

    async def _end(c: Any, grant: BreakGlassGrant, principal: Principal) -> GrantOut:
        try:
            grant.end(principal.subject, c.clock.now())
        except BreakGlassError as exc:
            raise HTTPException(409, str(exc)) from exc
        with tenant_context(grant.tenant_id):
            await c.break_glass.save(grant)
            await audit(
                c,
                principal.name,
                AuditAction.BREAK_GLASS_ENDED,
                grant.requested_by,
                grant=str(grant.id),
            )
        return await out(c, grant)

    # --- the tenant's owner -------------------------------------------------------------
    async def tenant_grant(c: Any, grant_id: UUID) -> BreakGlassGrant:
        grant = await c.break_glass.get(grant_id)  # RLS: this tenant's only
        if grant is None or grant.tenant_id != require_tenant():
            raise NotFoundError(f"grant {grant_id} not found")
        return grant

    @app.get(
        "/api/v1/support-access",
        response_model=list[GrantOut],
        dependencies=[Depends(require(P.SUPPORT_APPROVE))],
    )
    async def support_access(c: Any = Depends(get_container)) -> list[GrantOut]:
        """Every request support has made of this tenant, newest first."""
        return [await out(c, g) for g in await c.break_glass.list_all()]

    async def _decide(c: Any, grant_id: UUID, approve: bool, principal: Principal) -> GrantOut:
        grant = await tenant_grant(c, grant_id)
        try:
            grant.decide(approve, principal.subject, c.clock.now())
        except BreakGlassError as exc:
            raise HTTPException(409, str(exc)) from exc
        await c.break_glass.save(grant)
        await audit(
            c,
            principal.name,
            AuditAction.BREAK_GLASS_APPROVED if approve else AuditAction.BREAK_GLASS_DENIED,
            grant.requested_by,
            grant=str(grant.id),
            expires_at=grant.expires_at.isoformat() if grant.expires_at else None,
        )
        return await out(c, grant)

    @app.post(
        "/api/v1/support-access/{grant_id}/approve",
        response_model=GrantOut,
        dependencies=[Depends(require(P.SUPPORT_APPROVE))],
    )
    async def approve(
        grant_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> GrantOut:
        """From now, for the length asked: read-only, and every request recorded here."""
        return await _decide(c, grant_id, True, principal)

    @app.post(
        "/api/v1/support-access/{grant_id}/deny",
        response_model=GrantOut,
        dependencies=[Depends(require(P.SUPPORT_APPROVE))],
    )
    async def deny(
        grant_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> GrantOut:
        return await _decide(c, grant_id, False, principal)

    @app.post(
        "/api/v1/support-access/{grant_id}/end",
        response_model=GrantOut,
        dependencies=[Depends(require(P.SUPPORT_APPROVE))],
    )
    async def end_access(
        grant_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> GrantOut:
        """Effective on support's next request."""
        return await _end(c, await tenant_grant(c, grant_id), principal)
