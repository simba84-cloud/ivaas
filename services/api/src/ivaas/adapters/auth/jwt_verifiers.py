"""TokenVerifier adapters.

OidcTokenVerifier: any OpenID Connect provider (Keycloak, Authentik, Zitadel, ...).
  Keys come from the issuer's JWKS endpoint and are cached; a token signed with an
  unknown key id triggers one refresh (key rotation) before being rejected.
LocalTokenVerifier: HS256 with a shared secret, for development and tests only.
ApiKeyVerifier: static keys for machine callers (the pipeline), role SERVICE.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

import httpx
import jwt

from ivaas.ports.auth import AuthError, Principal, Role

ROLE_CLAIM = "roles"  # a flat list; Keycloak is configured to map realm roles into it


def _roles(claims: dict) -> frozenset[Role]:
    raw: Iterable[str] = claims.get(ROLE_CLAIM) or claims.get("realm_access", {}).get("roles", [])
    return frozenset(Role(r) for r in raw if r in Role.__members__.values())


def _principal(claims: dict) -> Principal:
    return Principal(
        subject=str(claims["sub"]),
        name=claims.get("name") or claims.get("preferred_username") or str(claims["sub"]),
        roles=_roles(claims),
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
        roles: Iterable[Role],
        ttl_s: int = 8 * 3600,
        *,
        must_change_password: bool = False,
        password_epoch: int = 0,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": self._issuer,
            "sub": subject,
            "name": name,
            ROLE_CLAIM: [r.value for r in roles],
            "iat": now,
            "exp": now + ttl_s,
            "mcp": must_change_password,
            "pwd": password_epoch,
        }
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
    """Static keys -> SERVICE principals. Keys are compared in constant time."""

    def __init__(self, keys: dict[str, str]) -> None:
        # {key: caller name}
        self._keys = dict(keys)

    async def verify(self, token: str) -> Principal:
        import hmac

        for key, name in self._keys.items():
            if hmac.compare_digest(key, token):
                return Principal(
                    subject=f"service:{name}", name=name, roles=frozenset({Role.SERVICE})
                )
        raise AuthError("invalid api key")
