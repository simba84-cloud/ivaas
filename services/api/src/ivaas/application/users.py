"""Managing accounts: create, assign roles, reset a password, enable or disable.

Two invariants run through all of it. An administrator must never be able to lock
the platform out — of themselves or of everyone — so the last enabled admin cannot
be demoted or disabled, and nobody can demote or disable their own account. And a
password is never readable: a reset mints a new temporary one, returns it exactly
once to the administrator who asked, and forces the owner to replace it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from ivaas.domain.users import (
    LastAdminError,
    SelfLockoutError,
    User,
    UserError,
    UserRole,
    validate_password,
)
from ivaas.ports.repositories import Clock
from ivaas.ports.users import PasswordHasher, UserStore

#: Unambiguous alphabet: no O/0, l/1, so a temporary password can be read aloud.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
TEMPORARY_PASSWORD_LENGTH = 16


def generate_temporary_password() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH))


class UserExistsError(UserError):
    def __init__(self, username: str) -> None:
        super().__init__(f"a user named {username!r} already exists")


class UnknownUserError(UserError):
    def __init__(self, username: str) -> None:
        super().__init__(f"no user named {username!r}")


@dataclass
class UserAdmin:
    users: UserStore
    hasher: PasswordHasher
    clock: Clock

    async def _require(self, username: str) -> User:
        user = await self.users.get(username)
        if user is None:
            raise UnknownUserError(username)
        return user

    async def _guard_last_admin(self, user: User, *, still_admin: bool) -> None:
        """Refuse a change that would leave nobody able to administer the platform."""
        if UserRole.ADMIN not in user.roles or user.disabled or still_admin:
            return
        if await self.users.count_enabled_admins() <= 1:
            raise LastAdminError()

    async def list_users(self) -> list[User]:
        return await self.users.list_all()

    async def create(
        self, username: str, display_name: str, roles: set[UserRole]
    ) -> tuple[User, str]:
        """Create an account with a temporary password, returned once."""
        username = username.strip().lower()
        if not username:
            raise UserError("a username is required")
        if await self.users.get(username) is not None:
            raise UserExistsError(username)
        temporary = generate_temporary_password()
        user = User(
            username=username,
            display_name=display_name.strip() or username,
            password_hash=self.hasher.hash(temporary),
            roles=set(roles),
            must_change_password=True,
            created_at=self.clock.now(),
            password_changed_at=self.clock.now(),
        )
        user.assign_roles(set(roles))
        await self.users.save(user)
        return user, temporary

    async def assign_roles(self, username: str, roles: set[UserRole], *, by: str) -> User:
        user = await self._require(username)
        if username == by and UserRole.ADMIN not in roles:
            raise SelfLockoutError("remove your own administrator role from")
        await self._guard_last_admin(user, still_admin=UserRole.ADMIN in roles)
        user.assign_roles(roles)
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
