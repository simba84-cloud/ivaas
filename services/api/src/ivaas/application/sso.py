"""Signing in through a tenant's identity provider (M8, T8.2). The rules are in
domain/sso.py; this runs them.

A sign-in is two requests: `start` sends the browser to the provider with a fresh
state, nonce and PKCE challenge, and `finish` takes the provider's answer. What
`start` made is kept here for ten minutes and used once, so a callback that was not
started here, or is replayed, finds nothing.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ivaas.adapters.auth.oidc_client import pkce_pair
from ivaas.domain.rbac import Role, RoleBinding
from ivaas.domain.sso import SsoConfig, SsoDenied, SsoError, email_of
from ivaas.domain.tenancy import ScopeType, Tenant
from ivaas.domain.users import User
from ivaas.tenancy import system_context, tenant_context

PENDING_FOR_S = 600


@dataclass
class _Pending:
    tenant_id: UUID
    nonce: str
    verifier: str
    started: float


@dataclass
class SsoSignIn:
    configs: Any
    users: Any
    tenants: Any
    hasher: Any
    clock: Any
    oidc: Any
    _pending: dict[str, _Pending] = field(default_factory=dict)

    async def _tenant(self, slug: str) -> tuple[Tenant, SsoConfig]:
        with system_context():
            tenant = await self.tenants.get_by_slug(slug.strip().lower())
            config = await self.configs.get(tenant.id) if tenant else None
        if tenant is None or config is None:
            # the same answer either way: nobody learns which tenants exist
            raise SsoDenied("single sign-on is not set up for that organisation")
        if tenant.status.value == "cancelled":
            raise SsoDenied(f"{tenant.name}'s account is cancelled")
        return tenant, config

    async def start(self, slug: str, redirect_uri: str) -> str:
        """The provider's sign-in page, for this attempt only."""
        tenant, config = await self._tenant(slug)
        now = time.monotonic()
        for k in [k for k, p in self._pending.items() if now - p.started > PENDING_FOR_S]:
            del self._pending[k]
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        url = await self.oidc.authorize_url(
            config.issuer, config.client_id, redirect_uri, state, nonce, challenge
        )
        self._pending[state] = _Pending(tenant.id, nonce, verifier, now)
        return url

    async def finish(self, state: str, code: str, redirect_uri: str) -> User:
        pending = self._pending.pop(state, None)
        if pending is None or time.monotonic() - pending.started > PENDING_FOR_S:
            raise SsoDenied("this sign-in expired or was not started here: try again")
        with system_context():
            tenant = await self.tenants.get(pending.tenant_id)
            config = await self.configs.get(pending.tenant_id)
        if tenant is None or config is None:
            raise SsoDenied("single sign-on is no longer set up for this organisation")
        id_token = await self.oidc.exchange(
            config.issuer,
            config.client_id,
            config.client_secret,
            code,
            pending.verifier,
            redirect_uri,
        )
        claims = await self.oidc.verify(config.issuer, config.client_id, id_token, pending.nonce)
        return await self.admit(tenant, config, claims)

    async def admit(self, tenant: Tenant, config: SsoConfig, claims: dict) -> User:
        """Who the provider says this is, against who may come into this tenant."""
        email = email_of(claims)
        subject = str(claims.get("sub") or "")
        if not email or not subject:
            raise SsoDenied("the identity provider did not say who you are")
        if not config.covers(email):
            raise SsoDenied(f"{email} is not in {tenant.name}'s organisation")
        if claims.get("email_verified") is False:
            raise SsoDenied(f"{email} has not been verified by your identity provider")

        with system_context():
            linked = [
                u
                for u in await self.users.list_all()
                if u.sso_issuer == config.issuer and u.sso_subject == subject
            ]
            user = linked[0] if linked else await self.users.get(email)

        if user is not None:
            if user.tenant_id != tenant.id:
                raise SsoDenied(f"{email} belongs to another organisation here")
            if user.sso_subject and (user.sso_issuer, user.sso_subject) != (config.issuer, subject):
                # the email moved to someone else at the provider: not the same person
                raise SsoDenied(f"{email} is linked to a different person at your provider")
            if user.disabled:
                raise SsoDenied("your account here is disabled")
        elif config.default_role is None:
            raise SsoDenied(
                f"{email} has no account in {tenant.name}: ask your administrator to add you"
            )
        else:  # a newcomer from the organisation, given the role the tenant chose
            now = self.clock.now()
            user = User(
                username=email,
                display_name=str(claims.get("name") or email),
                # never used: they sign in through the provider. Random, so not guessable
                password_hash=self.hasher.hash(secrets.token_urlsafe(32)),
                tenant_id=tenant.id,
                created_at=now,
                password_changed_at=now,
            )
            user.set_bindings([RoleBinding(config.default_role, ScopeType.TENANT, tenant.id)])

        user.sso_issuer, user.sso_subject = config.issuer, subject
        user.last_login_at = self.clock.now()
        with tenant_context(tenant.id):
            await self.users.save(user)
        return user


def password_allowed(config: SsoConfig | None, user: User) -> bool:
    """Required SSO turns passwords off, except an owner's: a broken provider must
    never lock a tenant out of its own account."""
    return config is None or not config.required or Role.TENANT_OWNER in user.roles


def check_new_config(config: SsoConfig) -> None:
    if not config.client_secret:
        raise SsoError("the client secret is required")
