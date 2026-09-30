"""TokenVerifier adapters.

OidcTokenVerifier: any OpenID Connect provider (Keycloak, Authentik, Zitadel, ...).
  Keys come from the issuer's JWKS endpoint and are cached; a token signed with an
  unknown key id triggers one refresh (key rotation) before being rejected.
LocalTokenVerifier: HS256 with a shared secret, for development and tests only.
ApiKeyVerifier: static keys for machine callers (the pipeline), each bound to a tenant.

A token names its tenant in the `tenant` claim (a slug or id). The auth dependency
resolves it and loads the caller's role bindings; nothing here decides access.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import httpx
import jwt

from ivaas.domain.rbac import Role
from ivaas.domain.tenancy import BAKERS_INN_ID
from ivaas.ports.auth import AuthError, Principal

ROLE_CLAIM = "roles"  # a flat list; Keycloak is configured to map realm roles into it
#: the tenant a token acts for; Keycloak sets it from the user's organisation
TENANT_CLAIM = "tenant"


def _roles(claims: dict) -> tuple[str, ...]:
    raw: Iterable[str] = claims.get(ROLE_CLAIM) or claims.get("realm_access", {}).get("roles", [])
    known = set(Role.__members__.values())
    return tuple(sorted(r for r in raw if r in known))


def _principal(claims: dict) -> Principal:
    return Principal(
        subject=str(claims["sub"]),
        name=claims.get("name") or claims.get("preferred_username") or str(claims["sub"]),
        claimed_roles=_roles(claims),
        tenant_ref=claims.get(TENANT_CLAIM) or None,
        password_epoch=int(claims.get("pwd") or 0),
        must_change_password=bool(claims.get("mcp", False)),
    )


class LocalTokenVerifier:
    ALG = "HS256"

    def __init__(self, secret: str, issuer: str = "ivaas-local") -> None:
        if len(secret) < 16:
            raise ValueError("local auth secret must be at least 16 characters")
        self._secret, self._issuer = secret, issuer

    def mint(
        self,
        subject: str,
        name: str,
        roles: Iterable[str],
        ttl_s: int = 8 * 3600,
        *,
        must_change_password: bool = False,
        password_epoch: int = 0,
        tenant: str | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": self._issuer,
            "sub": subject,
            "name": name,
            # informational: access comes from the bindings stored for the account
            ROLE_CLAIM: sorted(str(r) for r in roles),
            "iat": now,
            "exp": now + ttl_s,
            "mcp": must_change_password,
            "pwd": password_epoch,
        }
        if tenant:
            claims[TENANT_CLAIM] = tenant
        return jwt.encode(claims, self._secret, algorithm=self.ALG)

    async def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(token, self._secret, algorithms=[self.ALG], issuer=self._issuer)
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {type(exc).__name__}") from exc
        return _principal(claims)


def _signing_keys(jwks: list[dict]) -> dict[str, jwt.PyJWK]:
    """Keycloak (and others) publish encryption keys ('use': 'enc', RSA-OAEP) alongside
    signing keys; PyJWT cannot build those and must not take the rest down with them."""
    out: dict[str, jwt.PyJWK] = {}
    for k in jwks:
        if "kid" not in k or k.get("use", "sig") != "sig":
            continue
        try:
            out[k["kid"]] = jwt.PyJWK(k)
        except jwt.PyJWKError:
            continue  # an algorithm we do not verify with; harmless to ignore
    return out


class OidcTokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        client: httpx.AsyncClient | None = None,
        jwks_ttl_s: float = 3600,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._ttl = jwks_ttl_s
        self._jwks: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _refresh(self, force: bool = False) -> None:
        async with self._lock:
            if not force and self._jwks and time.time() - self._fetched_at < self._ttl:
                return
            try:
                conf = (
                    (await self._client.get(f"{self._issuer}/.well-known/openid-configuration"))
                    .raise_for_status()
                    .json()
                )
                keys = (await self._client.get(conf["jwks_uri"])).raise_for_status().json()
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise AuthError(f"identity provider unavailable: {type(exc).__name__}") from exc
            self._jwks = _signing_keys(keys.get("keys", []))
            self._fetched_at = time.time()

    async def verify(self, token: str) -> Principal:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise AuthError("malformed token") from exc
        await self._refresh()
        if kid not in self._jwks:
            await self._refresh(force=True)  # key rotation
        key = self._jwks.get(kid)
        if key is None:
            raise AuthError("unknown signing key")
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {type(exc).__name__}") from exc
        return _principal(claims)


class ApiKeyVerifier:
    """Static keys -> service principals. Keys are compared in constant time.

    Each key belongs to one tenant, named after the caller as `name@tenant-slug`. A
    bare name belongs to Bakers Inn, the first tenant, so existing keys keep working.
    The key cannot choose its tenant per request: the binding is in configuration.
    """

    def __init__(self, keys: dict[str, str]) -> None:
        # {key: "caller name[@tenant slug]"}
        self._keys = dict(keys)

    async def verify(self, token: str) -> Principal:
        import hmac

        for key, caller in self._keys.items():
            if hmac.compare_digest(key, token):
                name, _, tenant = caller.partition("@")
                return Principal(
                    subject=f"service:{name}",
                    name=name,
                    tenant_ref=tenant or str(BAKERS_INN_ID),
                    is_service=True,
                    claimed_roles=(Role.INTEGRATION.value,),
                )
        raise AuthError("invalid api key")
