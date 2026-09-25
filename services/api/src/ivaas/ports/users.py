"""Ports for accounts and password hashing."""

from __future__ import annotations

from typing import Protocol

from ivaas.domain.users import User


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, password_hash: str) -> bool:
        """Constant-time as far as the algorithm allows. False rather than raising."""
        ...

    def needs_rehash(self, password_hash: str) -> bool:
        """True when the hash was made with weaker parameters than we now use."""
        ...


class UserStore(Protocol):
    async def get(self, username: str) -> User | None: ...

    async def list_all(self) -> list[User]: ...

    async def save(self, user: User) -> None: ...

    async def count_enabled_admins(self) -> int: ...
