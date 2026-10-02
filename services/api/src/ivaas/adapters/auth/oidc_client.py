"""IVaaS as an OpenID Connect client of a tenant's identity provider (M8, T8.2).

The authorization-code flow, server side: IVaaS holds the client secret, so the
browser never sees it, and PKCE, state and nonce are all used, so an intercepted code
or a replayed token buys nothing. Works with Azure AD (v2.0 endpoints), Google, Okta,
Keycloak: anything that publishes `/.well-known/openid-configuration`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any

import httpx
import jwt

from ivaas.adapters.auth.jwt_verifiers import _signing_keys
from ivaas.domain.sso import SsoError


def pkce_pair() -> tuple[str, str]:
    """-> (verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class OidcClient:
    def __init__(self, client: httpx.AsyncClient | None = None, ttl_s: float = 3600) -> None:
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._ttl = ttl_s
        self._discovery: dict[str, tuple[float, dict[str, Any]]] = {}
        self._jwks: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def discover(self, issuer: str) -> dict[str, Any]:
        cached = self._discovery.get(issuer)
        if cached and time.time() - cached[0] < self._ttl:
            return cached[1]
        try:
            r = await self._client.get(f"{issuer}/.well-known/openid-configuration")
            conf = r.raise_for_status().json()
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
                conf[key]  # noqa: B018 - all four are required
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise SsoError(
                f"the identity provider at {issuer} could not be reached or read"
            ) from exc
        if conf["issuer"].rstrip("/") != issuer:
            raise SsoError(f"the provider calls itself {conf['issuer']}, not {issuer}")
        self._discovery[issuer] = (time.time(), conf)
        return conf

    async def _keys(self, issuer: str, force: bool = False) -> dict[str, jwt.PyJWK]:
        cached = self._jwks.get(issuer)
        if cached and not force and time.time() - cached[0] < self._ttl:
            return cached[1]
        conf = await self.discover(issuer)
        try:
            keys = (await self._client.get(conf["jwks_uri"])).raise_for_status().json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SsoError("the identity provider's signing keys could not be read") from exc
        found = _signing_keys(keys.get("keys", []))
        self._jwks[issuer] = (time.time(), found)
        return found

    async def authorize_url(
        self, issuer: str, client_id: str, redirect_uri: str, state: str, nonce: str, challenge: str
    ) -> str:
        conf = await self.discover(issuer)
        query = httpx.QueryParams(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{conf['authorization_endpoint']}?{query}"

    async def exchange(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> str:
        """The authorization code for the ID token."""
        conf = await self.discover(issuer)
        try:
            r = await self._client.post(
                conf["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": verifier,
                },
            )
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SsoError("the identity provider did not answer the sign-in") from exc
        if r.status_code != 200 or "id_token" not in body:
            reason = body.get("error_description") or body.get("error") or r.status_code
            raise SsoError(f"the identity provider refused the sign-in: {reason}")
        return body["id_token"]

    async def verify(self, issuer: str, client_id: str, id_token: str, nonce: str) -> dict:
        """Signed by the issuer's key, for this client, unexpired, with this nonce."""
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
        except jwt.PyJWTError as exc:
            raise SsoError("the provider sent a malformed token") from exc
        keys = await self._keys(issuer)
        if kid not in keys:
            keys = await self._keys(issuer, force=True)  # rotated
        if kid not in keys:
            raise SsoError("the token is signed with a key the provider does not publish")
        try:
            claims = jwt.decode(
                id_token,
                keys[kid].key,
                algorithms=["RS256", "ES256"],
                audience=client_id,
                issuer=issuer,
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise SsoError(f"the provider's token is not valid here: {type(exc).__name__}") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise SsoError("the token was not issued for this sign-in")
        return claims
