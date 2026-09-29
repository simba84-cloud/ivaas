"""Authentication port. Who is calling, for which tenant, and what may they do?"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from ivaas.domain.rbac import Permission, RoleBinding, Scope, authorize, permissions_of


@dataclass(frozen=True)
class Principal:
    subject: str
    name: str
    #: every role this caller holds, each with the scope it applies to
    bindings: tuple[RoleBinding, ...] = ()
    #: the one tenant this caller acts for; None for platform and partner staff
    tenant_id: UUID | None = None
    #: the tenant as a credential names it (a slug or id) before it is resolved
    tenant_ref: str | None = None
    #: which password this token was minted for; any change to it invalidates the token
    password_epoch: int = 0
    #: the account must set a new password before it may do anything else
    must_change_password: bool = False
    #: a machine caller (the edge pipeline); it has no account to check
    is_service: bool = False
    #: role names a token carried, for identity providers that manage roles themselves
    claimed_roles: tuple[str, ...] = field(default_factory=tuple)

    def can(self, permission: Permission, scope: Scope | None = None) -> bool:
        return authorize(self.bindings, permission, scope)

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_of(self.bindings)


class AuthError(Exception):
    pass


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Principal: ...
