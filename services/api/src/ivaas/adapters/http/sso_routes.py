"""Per-tenant single sign-on (M8, T8.2): the tenant's settings, and the sign-in itself.

The tenant's administrators set it up from `/api/v1/sso`: the provider's issuer, the
client IVaaS is registered as there, and the email domains it vouches for. The secret
is written, never read back. Sign-in is `/api/v1/auth/sso/start?org=<short name>`,
which goes to the provider, and `/api/v1/auth/sso/callback`, which comes back. It
ends at the portal with an ordinary IVaaS session, or with the reason it was refused.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ivaas.adapters.http.auth import current_principal, password_epoch, require
from ivaas.domain.audit import AuditAction
from ivaas.domain.rbac import Permission as P
from ivaas.domain.rbac import Role
from ivaas.domain.sso import SsoConfig, SsoDenied, SsoError
from ivaas.ports.auth import Principal
from ivaas.tenancy import require_tenant, system_context, tenant_context

Audit = Callable[..., Awaitable[None]]


class SsoIn(BaseModel):
    issuer: str = Field(min_length=8, max_length=300)
    client_id: str = Field(min_length=1, max_length=300)
    #: required the first time; left empty later, the stored one is kept
    client_secret: str = Field(default="", max_length=2048)
    domains: list[str] = Field(min_length=1, max_length=20)
    default_role: Role | None = None
    required: bool = False


class SsoOut(BaseModel):
    configured: bool
    issuer: str | None = None
    client_id: str | None = None
    #: whether a secret is stored; never the secret
    secret_configured: bool = False
    domains: list[str] = []
    default_role: str | None = None
    required: bool = False
    updated_by: str | None = None
    updated_at: datetime | None = None
    #: what to register at the provider as the redirect URI
    redirect_uri: str
    #: what people type, or follow, to sign in
    sign_in_url: str


def add_sso_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    def callback_uri(c: Any) -> str:
        return f"{c.settings.public_url.rstrip('/')}/api/v1/auth/sso/callback"

    async def out(c: Any, config: SsoConfig | None) -> SsoOut:
        with system_context():
            tenant = await c.tenants.get(require_tenant())
        slug = tenant.slug if tenant else ""
        base = dict(
            redirect_uri=callback_uri(c),
            sign_in_url=f"{c.settings.public_url.rstrip('/')}/api/v1/auth/sso/start?org={slug}",
        )
        if config is None:
            return SsoOut(configured=False, **base)
        return SsoOut(
            configured=True,
            issuer=config.issuer,
            client_id=config.client_id,
            secret_configured=bool(config.client_secret),
            domains=config.domains,
            default_role=config.default_role.value if config.default_role else None,
            required=config.required,
            updated_by=config.updated_by,
            updated_at=config.updated_at,
            **base,
        )

    # --- the tenant's settings ---------------------------------------------------------
    @app.get("/api/v1/sso", response_model=SsoOut, dependencies=[Depends(require(P.USER_MANAGE))])
    async def get_sso(c: Any = Depends(get_container)) -> SsoOut:
        return await out(c, await c.sso_configs.get(require_tenant()))

    @app.put("/api/v1/sso", response_model=SsoOut, dependencies=[Depends(require(P.USER_MANAGE))])
    async def put_sso(
        body: SsoIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> SsoOut:
        """Checked against the provider before it is saved: a typo in the issuer is
        refused here, not discovered when nobody can sign in."""
        tenant_id = require_tenant()
        existing = await c.sso_configs.get(tenant_id)
        secret = body.client_secret or (existing.client_secret if existing else "")
        if not secret:
            raise HTTPException(422, "the client secret is required")
        try:
            config = SsoConfig(
                tenant_id=tenant_id,
                issuer=body.issuer,
                client_id=body.client_id.strip(),
                client_secret=secret,
                domains=body.domains,
                default_role=body.default_role,
                required=body.required,
                updated_by=principal.name,
                updated_at=c.clock.now(),
            )
            await c.oidc.discover(config.issuer)
        except SsoError as exc:
            raise HTTPException(422, str(exc)) from exc
        await c.sso_configs.save(config)
        await audit(
            c,
            principal.name,
            AuditAction.SSO_CONFIGURED,
            config.issuer,
            domains=",".join(config.domains),
            required=config.required,
            default_role=config.default_role.value if config.default_role else None,
        )
        return await out(c, config)

    @app.delete(
        "/api/v1/sso", response_model=SsoOut, dependencies=[Depends(require(P.USER_MANAGE))]
    )
    async def delete_sso(
        principal: Principal = Depends(current_principal), c: Any = Depends(get_container)
    ) -> SsoOut:
        """Passwords work again for everyone; accounts keep their link to the provider."""
        tenant_id = require_tenant()
        if await c.sso_configs.get(tenant_id) is not None:
            await c.sso_configs.delete(tenant_id)
            await audit(c, principal.name, AuditAction.SSO_REMOVED, "single sign-on")
        return await out(c, None)

    # --- signing in -------------------------------------------------------------------
    def to_portal(c: Any, error: str) -> RedirectResponse:
        base = c.settings.public_url.rstrip("/")
        return RedirectResponse(f"{base}/?sso_error={quote(error)}", status_code=302)

    @app.get("/api/v1/auth/sso/start", include_in_schema=False)
    async def sso_start(
        org: str = Query(min_length=1, max_length=64), c: Any = Depends(get_container)
    ):
        if c.local_auth is None:
            raise HTTPException(404, "single sign-on runs in local auth mode")
        try:
            url = await c.sso().start(org, callback_uri(c))
        except SsoError as exc:
            return to_portal(c, str(exc))
        return RedirectResponse(url, status_code=302)

    @app.get("/api/v1/auth/sso/callback", include_in_schema=False)
    async def sso_callback(
        state: str = "",
        code: str = "",
        error: str = "",
        error_description: str = "",
        c: Any = Depends(get_container),
    ):
        if c.local_auth is None:
            raise HTTPException(404, "single sign-on runs in local auth mode")
        if error:  # the provider refused, or the person cancelled
            return to_portal(c, error_description or error)
        try:
            user = await c.sso().finish(state, code, callback_uri(c))
        except SsoDenied as exc:
            return to_portal(c, str(exc))
        except SsoError as exc:
            return to_portal(c, f"sign-in failed: {exc}")
        token = c.local_auth.mint(
            user.username,
            user.display_name,
            [r.value for r in user.roles],
            must_change_password=False,  # they never had a password to change
            password_epoch=password_epoch(user),
            tenant=str(user.tenant_id),
        )
        with tenant_context(user.tenant_id):
            await audit(
                c,
                user.username,
                AuditAction.SIGNED_IN,
                user.username,
                via="sso",
                roles=",".join(sorted(r.value for r in user.roles)),
            )
        # in the fragment: never sent to a server, never in a log
        base = c.settings.public_url.rstrip("/")
        return RedirectResponse(f"{base}/auth/sso#token={token}", status_code=302)
