"""Managing accounts: create, assign roles, reset a password, enable or disable.

Two invariants run through all of it. An administrator must never be able to lock
a tenant out — of themselves or of everyone — so the tenant's last enabled admin
cannot be demoted or disabled, and nobody can demote or disable their own account. And a
password is never readable: a reset mints a new temporary one, returns it exactly
once to the administrator who asked, and forces the owner to replace it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import ADMINISTERING, Role, RoleBinding
from ivaas.domain.tenancy import ScopeType
from ivaas.domain.users import (
    LastAdminError,
    SelfLockoutError,
    User,
    UserError,
    validate_password,
)
from ivaas.ports.repositories import Clock
from ivaas.ports.users import PasswordHasher, UserStore
from ivaas.tenancy import require_tenant, system_context

#: Unambiguous alphabet: no O/0, l/1, so a temporary password can be read aloud.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
TEMPORARY_PASSWORD_LENGTH = 16


def generate_temporary_password() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH))


class UserExistsError(UserError):
    def __init__(self, username: str) -> None:
        super().__init__(f"a user named {username!r} already exists")


class UnknownUserError(NotFoundError, UserError):
    """Not found, not a conflict: another tenant's account must look like no account."""

    def __init__(self, username: str) -> None:
        super().__init__(f"no user named {username!r}")


@dataclass
class UserAdmin:
    users: UserStore
    hasher: PasswordHasher
    clock: Clock

    async def _require(self, username: str) -> User:
        # the store only sees this tenant's accounts: another tenant's is "no such user"
        user = await self.users.get(username)
        if user is None:
            raise UnknownUserError(username)
        return user

    async def _guard_last_admin(self, user: User, *, still_admin: bool) -> None:
        """Refuse a change that would leave nobody able to administer the tenant."""
        if not user.administers or still_admin:
            return
        if await self.users.count_enabled_admins() <= 1:
            raise LastAdminError()

    @staticmethod
    def _administering(bindings: list[RoleBinding]) -> bool:
        return any(b.role in ADMINISTERING and b.scope_type is ScopeType.TENANT for b in bindings)

    async def list_users(self) -> list[User]:
        return await self.users.list_all()

    async def create(self, username: str, display_name: str, roles: set[Role]) -> tuple[User, str]:
        """Create an account in the caller's tenant with a temporary password, returned once."""
        username = username.strip().lower()
        if not username:
            raise UserError("a username is required")
        # Usernames are what people sign in with, before any tenant is known, so they
        # are unique across the platform. The check has to see every tenant's.
        with system_context():
            if await self.users.get(username) is not None:
                raise UserExistsError(username)
        temporary = generate_temporary_password()
        user = User(
            username=username,
            display_name=display_name.strip() or username,
            password_hash=self.hasher.hash(temporary),
            tenant_id=require_tenant(),
            must_change_password=True,
            created_at=self.clock.now(),
            password_changed_at=self.clock.now(),
        )
        user.assign_roles(set(roles))
        await self.users.save(user)
        return user, temporary

    async def assign_roles(self, username: str, roles: set[Role], *, by: str) -> User:
        """Replace the account's roles, each across the whole tenant."""
        user = await self._require(username)
        tenant = require_tenant()
        bindings = [RoleBinding(r, ScopeType.TENANT, tenant) for r in sorted(roles)]
        return await self._bind(user, bindings, by=by)

    async def set_bindings(self, username: str, bindings: list[RoleBinding], *, by: str) -> User:
        """Replace the account's roles with these, each at its own scope (site, bay...)."""
        return await self._bind(await self._require(username), bindings, by=by)

    async def _bind(self, user: User, bindings: list[RoleBinding], *, by: str) -> User:
        still_admin = self._administering(bindings)
        if user.username == by and user.administers and not still_admin:
            raise SelfLockoutError("remove your own administrator role from")
        await self._guard_last_admin(user, still_admin=still_admin)
        user.set_bindings(bindings)
        await self.users.save(user)
        return user

    async def set_enabled(self, username: str, enabled: bool, *, by: str) -> User:
        user = await self._require(username)
        if username == by and not enabled:
            raise SelfLockoutError("disable")
        await self._guard_last_admin(user, still_admin=enabled)
        user.disabled = not enabled
        if not enabled:
            # ending the account also ends its open sessions
            user.password_changed_at = self.clock.now()
        await self.users.save(user)
        return user

    async def reset_password(self, username: str) -> tuple[User, str]:
        """Mint a temporary password. Returned once, to the administrator, never stored."""
        user = await self._require(username)
        temporary = generate_temporary_password()
        user.set_password(self.hasher.hash(temporary), self.clock.now(), temporary=True)
        await self.users.save(user)
        return user, temporary

    async def change_own_password(self, username: str, current: str, new: str) -> User:
        user = await self._require(username)
        if not self.hasher.verify(current, user.password_hash):
            raise UserError("the current password is not correct")
        if current == new:
            raise UserError("the new password must be different from the current one")
        validate_password(new, username=username)
        user.set_password(self.hasher.hash(new), self.clock.now())
        await self.users.save(user)
        return user
