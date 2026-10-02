"""The end of a tenant (M8): cancel, reinstate, export (T8.4), purge (T8.5).

The owner works from `/api/v1/account/`: it sees when its data goes, cancels, and
exports everything. Those routes are all a cancelled tenant can still reach
(adapters/http/auth.py). Cassava's platform admins cancel, reinstate, and, once the
retention window has elapsed, purge, which leaves a signed deletion certificate.
Cassava never exports a tenant's data: that is the tenant's to take.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from ivaas.adapters.http.auth import current_principal, require
from ivaas.application.lifecycle import Unavailable
from ivaas.domain.audit import AuditAction
from ivaas.domain.lifecycle import DeletionCertificate, LifecycleError
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission as P
from ivaas.domain.tenancy import Tenant
from ivaas.ports.auth import Principal
from ivaas.tenancy import system_context, tenant_context

Audit = Callable[..., Awaitable[None]]


class ConfirmIn(BaseModel):
    #: the tenant's short name, typed: these are not undone by a stray click
    confirm: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=300)


class LifecycleOut(BaseModel):
    tenant_id: UUID
    slug: str
    name: str
    status: str
    cancelled_at: datetime | None
    cancelled_by: str | None
    #: when its data may be purged; null unless cancelled
    purge_after: datetime | None
    retention_days: int
    #: export and purge read every table: false on the in-memory store
    export_available: bool


class CertificateOut(BaseModel):
    id: UUID
    purged_tenant_id: UUID
    tenant_slug: str
    tenant_name: str
    purged_at: datetime
    purged_by: str
    body: dict
    signature: str
    #: the signature checks out against the platform's certificate key
    valid: bool


def add_lifecycle_routes(
    app: FastAPI, get_container: Callable[[Request], Any], audit: Audit
) -> None:
    def out(c: Any, t: Tenant) -> LifecycleOut:
        lc = c.lifecycle()
        return LifecycleOut(
            tenant_id=t.id,
            slug=t.slug,
            name=t.name,
            status=t.status.value,
            cancelled_at=t.cancelled_at,
            cancelled_by=t.cancelled_by,
            purge_after=lc.purge_after(t),
            retention_days=lc.retention.days,
            export_available=c.tenant_data is not None,
        )

    def cert_out(c: Any, cert: DeletionCertificate) -> CertificateOut:
        return CertificateOut(
            **{k: getattr(cert, k) for k in CertificateOut.model_fields if k != "valid"},
            valid=cert.verify(c.settings.certificate_secret),
        )

    def refused(exc: LifecycleError) -> HTTPException:
        if isinstance(exc, Unavailable):
            return HTTPException(501, str(exc))
        if str(exc).startswith("no tenant"):
            return HTTPException(404, str(exc))
        return HTTPException(409 if "already" in str(exc) else 422, str(exc))

    async def a_tenant(c: Any, tenant_id: UUID) -> Tenant:
        with system_context():
            tenant = await c.tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError(f"tenant {tenant_id} not found")
        return tenant

    def own(principal: Principal) -> UUID:
        if principal.tenant_id is None:
            raise HTTPException(403, "a tenant's own account: staff use the platform routes")
        return principal.tenant_id

    # --- the owner ------------------------------------------------------------------
    @app.get(
        "/api/v1/account/lifecycle",
        response_model=LifecycleOut,
        dependencies=[Depends(require(P.DATA_EXPORT))],
    )
    async def my_lifecycle(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> LifecycleOut:
        return out(c, await a_tenant(c, own(principal)))

    @app.post(
        "/api/v1/account/cancel",
        response_model=LifecycleOut,
        dependencies=[Depends(require(P.DATA_EXPORT))],
    )
    async def cancel_mine(
        body: ConfirmIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> LifecycleOut:
        """The owner ends the account. Its data is kept for the retention window, to
        export, and Cassava can reinstate it until then."""
        try:
            tenant = await c.lifecycle().cancel(own(principal), principal.name, body.confirm)
        except LifecycleError as exc:
            raise refused(exc) from exc
        who = principal.name
        await audit(c, who, AuditAction.TENANT_CANCELLED, tenant.slug, reason=body.reason)
        return out(c, tenant)

    @app.get(
        "/api/v1/account/export",
        dependencies=[Depends(require(P.DATA_EXPORT))],
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}}},
    )
    async def export_mine(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> FileResponse:
        """Everything: each table as JSON and CSV, every object, and a manifest of
        counts and checksums. Secrets are withheld and the manifest says which."""
        tenant = await a_tenant(c, own(principal))
        fd, name = tempfile.mkstemp(prefix="ivaas-export-", suffix=".zip")
        os.close(fd)
        path = Path(name)
        try:
            manifest = await c.lifecycle().export(tenant.id, principal.name, path)
        except LifecycleError as exc:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise refused(exc) from exc
        await audit(
            c,
            principal.name,
            AuditAction.TENANT_EXPORTED,
            tenant.slug,
            rows=sum(manifest["counts"].values()),
            objects=len(manifest["objects"]),
        )
        stamp = c.clock.now().strftime("%Y%m%d")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"{tenant.slug}-export-{stamp}.zip",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    # --- Cassava ----------------------------------------------------------------------
    @app.get(
        "/api/v1/platform/tenants/{tenant_id}/lifecycle",
        response_model=LifecycleOut,
        dependencies=[Depends(require(P.TENANT_SUSPEND))],
    )
    async def tenant_lifecycle(tenant_id: UUID, c: Any = Depends(get_container)) -> LifecycleOut:
        return out(c, await a_tenant(c, tenant_id))

    async def platform_change(
        c: Any, tenant_id: UUID, principal: Principal, action: AuditAction, reason: str, run
    ) -> LifecycleOut:
        try:
            tenant = await run()
        except LifecycleError as exc:
            raise refused(exc) from exc
        for scope in (system_context(), tenant_context(tenant_id)):  # both logs
            with scope:
                await audit(c, principal.name, action, tenant.slug, reason=reason)
        return out(c, tenant)

    @app.post(
        "/api/v1/platform/tenants/{tenant_id}/cancel",
        response_model=LifecycleOut,
        dependencies=[Depends(require(P.TENANT_SUSPEND))],
    )
    async def cancel_tenant(
        tenant_id: UUID,
        body: ConfirmIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> LifecycleOut:
        return await platform_change(
            c,
            tenant_id,
            principal,
            AuditAction.TENANT_CANCELLED,
            body.reason,
            lambda: c.lifecycle().cancel(tenant_id, principal.name, body.confirm),
        )

    @app.post(
        "/api/v1/platform/tenants/{tenant_id}/reinstate",
        response_model=LifecycleOut,
        dependencies=[Depends(require(P.TENANT_SUSPEND))],
    )
    async def reinstate_tenant(
        tenant_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> LifecycleOut:
        """Before the purge only: the tenant comes back with everything it had."""
        return await platform_change(
            c,
            tenant_id,
            principal,
            AuditAction.TENANT_REINSTATED,
            "",
            lambda: c.lifecycle().reinstate(tenant_id),
        )

    @app.post(
        "/api/v1/platform/tenants/{tenant_id}/purge",
        response_model=CertificateOut,
        dependencies=[Depends(require(P.TENANT_SUSPEND))],
    )
    async def purge_tenant(
        tenant_id: UUID,
        body: ConfirmIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> CertificateOut:
        """Every row and object of a cancelled tenant whose retention has elapsed, then a
        scan proving none remain, then a signed certificate. Not undone."""
        await a_tenant(c, tenant_id)
        try:
            cert = await c.lifecycle().purge(tenant_id, principal.name, body.confirm)
        except LifecycleError as exc:
            raise refused(exc) from exc
        with system_context():  # the tenant's own log is gone with it
            await audit(
                c,
                principal.name,
                AuditAction.TENANT_PURGED,
                cert.tenant_slug,
                certificate=str(cert.id),
            )
        return cert_out(c, cert)

    @app.get(
        "/api/v1/platform/deletion-certificates",
        response_model=list[CertificateOut],
        dependencies=[Depends(require(P.TENANT_SUSPEND))],
    )
    async def certificates(c: Any = Depends(get_container)) -> list[CertificateOut]:
        return [cert_out(c, x) for x in await c.certificates.list_all()]
