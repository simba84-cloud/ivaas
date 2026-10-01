"""FastAPI glue: turn a bearer token or API key into a Principal, fix the tenant the
request runs for, and enforce permissions.

The tenant comes from the verified identity only: the account's own tenant, or the
tenant an API key is bound to. Nothing the client sends in a body, query or header
can choose it (proposal §3.2). The one exception is a break-glass grant: support
names one in a header, and it counts only if that tenant's owner approved it for
that very person and it has not ended. It is then read-only, and every request made
with it is written into the tenant's audit log.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, WebSocket

from ivaas.domain.audit import AuditAction, AuditEntry
from ivaas.domain.break_glass import GrantState
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission, Role, RoleBinding, Scope
from ivaas.domain.tenancy import ScopeType
from ivaas.domain.users import User
from ivaas.ports.auth import AuthError, Principal, TokenVerifier
from ivaas.tenancy import set_tenant, system_context, tenant_context


def password_epoch(user: User) -> int:
    """Milliseconds of the last password change: the version a token is minted for."""
    return int(user.password_changed_at.timestamp() * 1000) if user.password_changed_at else 0


BEARER = "bearer "
API_KEY_HEADER = "x-ivaas-key"
NODE_HEADER = "x-ivaas-node"
BREAK_GLASS_HEADER = "x-ivaas-break-glass"


async def _authenticate(verifiers: dict[str, TokenVerifier], headers, query) -> Principal:
    auth = headers.get("authorization", "")
    node = headers.get(NODE_HEADER)
    if node and "node" in verifiers:
        return await verifiers["node"].verify(node)
    api_key = headers.get(API_KEY_HEADER)
    if api_key and "api_key" in verifiers:
        return await verifiers["api_key"].verify(api_key)
    token = auth[len(BEARER) :] if auth.lower().startswith(BEARER) else query.get("token")
    if not token or "user" not in verifiers:
        raise AuthError("missing credentials")
    return await verifiers["user"].verify(token)


#: A principal holding a temporary password may reach only these, and nothing else.
PASSWORD_CHANGE_PATHS = frozenset(
    {"/api/v1/auth/me", "/api/v1/auth/password", "/api/v1/auth/config"}
)


async def _resolve_tenant(container, ref: str | None) -> UUID | None:
    if not ref:
        return None
    with system_context():
        try:
            tenant = await container.tenants.get(UUID(ref))
        except ValueError:
            tenant = await container.tenants.get_by_slug(ref)
    if tenant is None:
        raise AuthError("the credential names a tenant that does not exist")
    return tenant.id


async def _check_account(container, principal: Principal, path: str) -> Principal:
    """A valid signature is not enough once an account can be disabled or reset.

    The token lives for hours, so disabling a user or resetting their password has
    to end the sessions they already hold. Both are decided here, against the
    account as it stands right now, and so is what the account may do: roles are
    read from the account on every request, never trusted from the token.
    """
    if principal.is_service and principal.tenant_id is not None:
        return principal  # an edge node: its verifier already bound tenant and site
    if principal.is_service:
        tenant_id = await _resolve_tenant(container, principal.tenant_ref)
        if tenant_id is None:
            raise AuthError("a service key must belong to a tenant")
        bindings = (RoleBinding(Role.INTEGRATION, ScopeType.TENANT, tenant_id),)
        return dataclasses.replace(principal, tenant_id=tenant_id, bindings=bindings)

    with system_context():  # the tenant is what we are about to find out
        user = await container.users.get(principal.subject)
    if user is None:
        return await _identity_provider_account(container, principal)
    if user.disabled:
        raise AuthError("account is not active")
    # Exact rather than time-based: comparing the token's issue time against the
    # password's would miss a reset made in the same second it was issued.
    if principal.password_epoch != password_epoch(user):
        raise AuthError("password was changed; sign in again")
    if (principal.must_change_password or user.must_change_password) and (
        path not in PASSWORD_CHANGE_PATHS
    ):
        # enforced here, not merely in the portal: a temporary password unlocks nothing
        raise HTTPException(403, "password change required")
    return dataclasses.replace(principal, tenant_id=user.tenant_id, bindings=tuple(user.bindings))


async def _identity_provider_account(container, principal: Principal) -> Principal:
    """An OIDC user with no local account: roles and tenant come from the token.

    Local mode has no such users (every token is minted for an account), so a
    missing account there means it was removed and the token must stop working.
    """
    if container.settings.auth_mode == "local":
        raise AuthError("account is not active")
    tenant_id = await _resolve_tenant(container, principal.tenant_ref)
    if tenant_id is None:
        raise AuthError("the token does not name a tenant")
    bindings = []
    for name in principal.claimed_roles:
        try:
            bindings.append(RoleBinding(Role(name), ScopeType.TENANT, tenant_id))
        except ValueError:
            continue  # a role that cannot be tenant-wide is not granted from a claim
    return dataclasses.replace(principal, tenant_id=tenant_id, bindings=tuple(bindings))


#: what break-glass access refuses, whatever its role would allow: it only looks
_READ_ONLY = frozenset({"GET", "HEAD"})


async def _break_glass(
    container, principal: Principal, ref: str, method: str, path: str, query
) -> Principal:
    """Support inside one tenant, on a grant its owner approved, for as long as it
    lasts. Anything wrong with the grant is the same refusal: it says nothing about
    grants the caller does not hold."""
    if principal.tenant_id is not None or not holds(principal, Permission.SUPPORT_REQUEST):
        raise HTTPException(403, "break-glass access is for platform support")
    try:
        grant_id = UUID(ref)
    except ValueError:
        grant_id = None
    with system_context():
        grant = await container.break_glass.get(grant_id) if grant_id else None
    now = container.clock.now()
    if (
        grant is None
        or grant.requested_by != principal.subject
        or grant.state(now) is not GrantState.ACTIVE
    ):
        raise HTTPException(403, "break-glass access has ended or was not granted")
    if method not in _READ_ONLY:
        raise HTTPException(403, "break-glass access is read-only")
    if not path.startswith("/api/v1/auth/"):
        # what support tried to look at, allowed or not, in the tenant's own log
        with tenant_context(grant.tenant_id):
            try:
                await container.audit.record(
                    AuditEntry(
                        at=now,
                        actor=principal.name,
                        action=AuditAction.BREAK_GLASS_USED,
                        subject=path,
                        detail={"grant": str(grant.id), "query": str(query)}
                        if str(query)
                        else {"grant": str(grant.id)},
                    )
                )
            except Exception:
                logging.getLogger(__name__).exception("could not audit break-glass use")
    bindings = (RoleBinding(Role.BREAK_GLASS, ScopeType.TENANT, grant.tenant_id),)
    return dataclasses.replace(
        principal, tenant_id=grant.tenant_id, bindings=bindings, break_glass=grant.id
    )


async def _principal_for(container, headers, query, path: str, method: str = "GET") -> Principal:
    principal = await _authenticate(container.verifiers, headers, query)
    principal = await _check_account(container, principal, path)
    if ref := headers.get(BREAK_GLASS_HEADER):
        principal = await _break_glass(container, principal, ref, method, path, query)
    # From here to the end of the request, every query runs for this tenant.
    set_tenant(principal.tenant_id)
    return principal


async def current_principal(request: Request) -> Principal:
    try:
        return await _principal_for(
            request.app.state.container,
            request.headers,
            request.query_params,
            request.url.path,
            request.method,
        )
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


async def websocket_principal(ws: WebSocket) -> Principal | None:
    """Browsers cannot set headers on WebSockets, so the token rides in ?token=."""
    try:
        return await _principal_for(
            ws.app.state.container, ws.headers, ws.query_params, "/api/v1/auth/me"
        )
    except (AuthError, HTTPException):
        return None


#: Permissions over a tenant's own data. They mean nothing without a tenant in
#: context (the database would return nothing anyway); refusing makes that honest.
TENANT_DATA = frozenset(Permission) - {
    Permission.TENANT_CREATE,
    Permission.TENANT_SUSPEND,
    Permission.SUBSCRIPTION_MANAGE,
    Permission.INVOICE_READ,
    Permission.SUPPORT_REQUEST,
}


def holds(principal: Principal, permission: Permission, *, scoped: bool = False) -> bool:
    if principal.tenant_id is None and permission in TENANT_DATA:
        # platform and partner staff hold no standing access to a tenant's data
        return False
    for b in principal.bindings:
        if permission not in b.permissions:
            continue
        if not scoped and b.scope_type in (ScopeType.SITE, ScopeType.BAY):
            continue
        return True
    return False


def require_any(*permissions: Permission) -> Callable:
    """Allow the request if the caller holds any one of `permissions`, tenant-wide."""

    async def check(
        request: Request, principal: Principal = Depends(current_principal)
    ) -> Principal:
        if not any(holds(principal, p) for p in permissions):
            names = " or ".join(f"'{p.value}'" for p in permissions)
            raise HTTPException(403, f"requires permission {names}")
        return principal

    return check


def require(permission: Permission, *, scoped: bool = False) -> Callable:
    """Allow the request if the caller holds `permission`.

    Safe by default: a role held at one site or bay counts only on endpoints that
    pass `scoped=True`, and those check the site or bay they touch with
    `check_scope`. Everywhere else (dashboards, totals, the audit log) needs the
    role across the whole tenant, because a figure for the tenant includes loads
    at sites the caller has no right to see.
    """

    async def check(
        request: Request, principal: Principal = Depends(current_principal)
    ) -> Principal:
        if not holds(principal, permission, scoped=scoped):
            raise HTTPException(403, f"requires permission '{permission.value}'")
        await refuse_changes_while_suspended(request, principal, permission)
        return principal

    return check


#: what still works while a tenant is suspended: looking, and the edge counting
_SAFE = {"GET", "HEAD", "OPTIONS"}
_NEVER_STOPPED = {Permission.INGEST_WRITE}


async def refuse_changes_while_suspended(
    request: Request, principal: Principal, permission: Permission
) -> None:
    """A suspended tenant's people can look but not change anything (T7.6, T7.10).
    Counting never stops for a billing reason: what the edge sends always lands."""
    if principal.tenant_id is None or request.method in _SAFE or permission in _NEVER_STOPPED:
        return
    with system_context():
        tenant = await request.app.state.container.tenants.get(principal.tenant_id)
    if tenant is not None and tenant.status.value == "suspended":
        why = "its partner has put it on hold" if tenant.on_hold else "an invoice is unpaid"
        raise HTTPException(
            402,
            f"this account is suspended: {why}. Everything can still be viewed, and counting "
            "carries on; changes return when it is settled.",
        )


def check_scope(principal: Principal, permission: Permission, scope: Scope) -> None:
    """Refuse, as not found, a site or bay outside the caller's bindings (§3.2: a
    resource the caller may not see must not be confirmed to exist)."""
    if not principal.can(permission, scope):
        raise NotFoundError("not found")
