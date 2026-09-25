"""FastAPI glue: turn a bearer token or API key into a Principal, enforce roles."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, WebSocket

from ivaas.domain.users import User
from ivaas.ports.auth import AuthError, Principal, Role, TokenVerifier


def password_epoch(user: User) -> int:
    """Milliseconds of the last password change: the version a token is minted for."""
    return int(user.password_changed_at.timestamp() * 1000) if user.password_changed_at else 0


BEARER = "bearer "
API_KEY_HEADER = "x-ivaas-key"


async def _authenticate(verifiers: dict[str, TokenVerifier], headers, query) -> Principal:
    auth = headers.get("authorization", "")
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


async def _check_account(container, principal: Principal, path: str) -> None:
    """A valid signature is not enough once an account can be disabled or reset.

    The token lives for hours, so disabling a user or resetting their password has
    to end the sessions they already hold. Both are decided here, against the
    account as it stands right now.
    """
    users = getattr(container, "users", None)
    if users is None or Role.SERVICE in principal.roles:
        return  # machine callers have no account
    user = await users.get(principal.subject)
    if user is None or user.disabled:
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


async def current_principal(request: Request) -> Principal:
    try:
        principal = await _authenticate(
            request.app.state.container.verifiers, request.headers, request.query_params
        )
        await _check_account(request.app.state.container, principal, request.url.path)
        return principal
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


async def websocket_principal(ws: WebSocket) -> Principal | None:
    """Browsers cannot set headers on WebSockets, so the token rides in ?token=."""
    try:
        principal = await _authenticate(
            ws.app.state.container.verifiers, ws.headers, ws.query_params
        )
        await _check_account(ws.app.state.container, principal, "/api/v1/auth/me")
        return principal
    except (AuthError, HTTPException):
        return None


def require(role: Role) -> Callable:
    async def check(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.allows(role):
            raise HTTPException(403, f"requires role '{role.value}'")
        return principal

    return check
