"""Per-tenant single sign-on (proposal §2.2, M8 T8.2).

A tenant points IVaaS at its own identity provider (Azure AD, Google Workspace, or
any OpenID Connect provider) and names the email domains it vouches for. Its people
then sign in there, and IVaaS acts on what that provider asserts. The provider proves
who someone is; whether they belong here is IVaaS's to decide, by these rules:

- the token must come from this tenant's provider, for this tenant's client, carrying
  the nonce this sign-in sent: no other tenant's provider, no replayed token;
- the email must be in one of the tenant's domains: someone the provider knows from
  outside the organisation is an outsider;
- the account is matched on the provider's own id for the person once linked, so an
  email address later reassigned to someone else does not inherit the account;
- an account in another tenant, or a disabled one, is never signed into;
- someone with no account is admitted only if the tenant chose a role for newcomers.

Required SSO turns password sign-in off for the tenant's people, except its owners,
who keep a password so that a broken provider never locks a tenant out of itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from ivaas.domain.rbac import TENANT_ROLES, Role
from ivaas.domain.tenancy import TenancyError


class SsoError(TenancyError):
    pass


class SsoDenied(SsoError):
    """Who they are is proven; they may not come in. The message is shown to them."""


def _domain(value: str) -> str:
    d = value.strip().lower().lstrip("@")
    if not d or "." not in d or " " in d:
        raise SsoError(f"{value!r} is not an email domain")
    return d


@dataclass
class SsoConfig:
    tenant_id: UUID
    issuer: str
    client_id: str
    #: sealed at rest; never returned by the API
    client_secret: str
    domains: list[str]
    #: what a newcomer from the tenant's domains is given; None admits only existing accounts
    default_role: Role | None = None
    #: password sign-in off for everyone but owners
    required: bool = False
    updated_by: str = ""
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.issuer.startswith(("https://", "http://")):
            raise SsoError("the issuer is the provider's URL, starting https://")
        if not self.client_id.strip():
            raise SsoError("the client id is required")
        self.issuer = self.issuer.rstrip("/")
        self.domains = sorted({_domain(d) for d in self.domains})
        if not self.domains:
            raise SsoError("name at least one email domain the provider vouches for")
        if self.default_role is not None and self.default_role not in TENANT_ROLES:
            raise SsoError(f"{self.default_role.value} is not a role a tenant can give")
        if self.default_role in (Role.TENANT_OWNER, Role.TENANT_ADMIN, Role.INTEGRATION):
            raise SsoError("newcomers cannot be made owners, admins or integrations")

    def covers(self, email: str) -> bool:
        _, _, d = email.strip().lower().rpartition("@")
        return d in self.domains


def email_of(claims: dict) -> str | None:
    """The person's email: `email`, or for Azure AD work accounts `preferred_username`
    or `upn`, which are their sign-in address. Lower-cased."""
    for key in ("email", "preferred_username", "upn"):
        value = claims.get(key)
        if isinstance(value, str) and "@" in value:
            return value.strip().lower()
    return None
