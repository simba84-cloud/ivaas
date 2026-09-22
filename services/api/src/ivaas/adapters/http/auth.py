"""FastAPI glue: turn a bearer token or API key into a Principal, enforce roles."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, WebSocket

from ivaas.ports.auth import AuthError, Principal, Role, TokenVerifier

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


async def current_principal(request: Request) -> Principal:
    try:
        return await _authenticate(
            request.app.state.container.verifiers, request.headers, request.query_params
        )
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


async def websocket_principal(ws: WebSocket) -> Principal | None:
    """Browsers cannot set headers on WebSockets, so the token rides in ?token=."""
    try:
        return await _authenticate(ws.app.state.container.verifiers, ws.headers, ws.query_params)
    except AuthError:
        return None


def require(role: Role) -> Callable:
    async def check(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.allows(role):
            raise HTTPException(403, f"requires role '{role.value}'")
        return principal

    return check
