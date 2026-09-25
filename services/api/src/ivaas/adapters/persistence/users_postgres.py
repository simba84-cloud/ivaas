"""UserStore on Postgres, plus an in-memory twin for dev and tests."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, select
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.users import User, UserRole


class UserRow(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(16)))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _to_domain(r: UserRow) -> User:
    return User(
        username=r.username,
        display_name=r.display_name,
        password_hash=r.password_hash,
        roles={UserRole(x) for x in r.roles},
        disabled=r.disabled,
        must_change_password=r.must_change_password,
        password_is_default=r.password_is_default,
        created_at=r.created_at,
        password_changed_at=r.password_changed_at,
        last_login_at=r.last_login_at,
    )


def _values(u: User) -> dict:
    return {
        "username": u.username,
        "display_name": u.display_name,
        "password_hash": u.password_hash,
        "roles": sorted(r.value for r in u.roles),
        "disabled": u.disabled,
        "must_change_password": u.must_change_password,
        "password_is_default": u.password_is_default,
        "created_at": u.created_at,
        "password_changed_at": u.password_changed_at,
        "last_login_at": u.last_login_at,
    }


class PostgresUserStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def get(self, username: str) -> User | None:
        async with self._sm() as db:
            r = await db.get(UserRow, username)
            return _to_domain(r) if r else None

    async def list_all(self) -> list[User]:
        async with self._sm() as db:
            rows = (await db.scalars(select(UserRow).order_by(UserRow.username))).all()
        return [_to_domain(r) for r in rows]

    async def save(self, user: User) -> None:
        values = _values(user)
        stmt = insert(UserRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[UserRow.username], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def count_enabled_admins(self) -> int:
        stmt = (
            select(func.count())
            .select_from(UserRow)
            .where(~UserRow.disabled, UserRow.roles.any(UserRole.ADMIN.value))
        )
        async with self._sm() as db:
            return int((await db.scalar(stmt)) or 0)


class InMemoryUserStore:
    def __init__(self, users: list[User] | None = None) -> None:
        self._users = {u.username: u for u in users or []}

    async def get(self, username: str) -> User | None:
        return self._users.get(username)

    async def list_all(self) -> list[User]:
        return [self._users[k] for k in sorted(self._users)]

    async def save(self, user: User) -> None:
        self._users[user.username] = user

    async def count_enabled_admins(self) -> int:
        return sum(1 for u in self._users.values() if not u.disabled and UserRole.ADMIN in u.roles)
