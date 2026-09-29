"""UserStore on Postgres, plus an in-memory twin for dev and tests."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, delete, func, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.rbac import ADMINISTERING, Role, RoleBinding
from ivaas.domain.tenancy import ScopeType
from ivaas.domain.users import User
from ivaas.tenancy import current_tenant, is_system


class UserRow(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoleBindingRow(Base):
    """Who holds which role, and where. The only source of an account's access."""

    __tablename__ = "role_bindings"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    username: Mapped[str] = mapped_column(
        ForeignKey("users.username", ondelete="CASCADE"), index=True
    )
    # the account's tenant, so row-level security can hide other tenants' grants
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(32))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


def _binding(r: RoleBindingRow) -> RoleBinding:
    return RoleBinding(Role(r.role), ScopeType(r.scope_type), r.scope_id)


def _to_domain(r: UserRow, bindings: list[RoleBindingRow]) -> User:
    return User(
        username=r.username,
        display_name=r.display_name,
        password_hash=r.password_hash,
        tenant_id=r.tenant_id,
        bindings=[_binding(b) for b in bindings],
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
        "tenant_id": u.tenant_id,
        "display_name": u.display_name,
        "password_hash": u.password_hash,
        "disabled": u.disabled,
        "must_change_password": u.must_change_password,
        "password_is_default": u.password_is_default,
        "created_at": u.created_at,
        "password_changed_at": u.password_changed_at,
        "last_login_at": u.last_login_at,
    }


class PostgresUserStore:
    """Row-level security decides which accounts are visible: the caller's tenant's,
    or every one inside `system_context()` (signing in, before a tenant is known)."""

    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def _load(self, db: AsyncSession, rows: list[UserRow]) -> list[User]:
        if not rows:
            return []
        stmt = select(RoleBindingRow).where(RoleBindingRow.username.in_([r.username for r in rows]))
        by_user: dict[str, list[RoleBindingRow]] = {}
        for b in (await db.scalars(stmt.order_by(RoleBindingRow.role))).all():
            by_user.setdefault(b.username, []).append(b)
        return [_to_domain(r, by_user.get(r.username, [])) for r in rows]

    async def get(self, username: str) -> User | None:
        async with self._sm() as db:
            r = await db.get(UserRow, username)
            found = await self._load(db, [r] if r else [])
        return found[0] if found else None

    async def list_all(self) -> list[User]:
        async with self._sm() as db:
            rows = (await db.scalars(select(UserRow).order_by(UserRow.username))).all()
            return await self._load(db, list(rows))

    async def save(self, user: User) -> None:
        values = _values(user)
        stmt = insert(UserRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[UserRow.username], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)
            await db.execute(delete(RoleBindingRow).where(RoleBindingRow.username == user.username))
            for b in user.bindings:
                db.add(
                    RoleBindingRow(
                        id=uuid4(),
                        username=user.username,
                        tenant_id=user.tenant_id,
                        role=b.role.value,
                        scope_type=b.scope_type.value,
                        scope_id=b.scope_id,
                    )
                )

    async def count_enabled_admins(self) -> int:
        """In the current tenant: row-level security has already narrowed both tables."""
        stmt = (
            select(func.count(func.distinct(UserRow.username)))
            .select_from(UserRow)
            .join(RoleBindingRow, RoleBindingRow.username == UserRow.username)
            .where(
                ~UserRow.disabled,
                RoleBindingRow.scope_type == ScopeType.TENANT.value,
                RoleBindingRow.role.in_([r.value for r in ADMINISTERING]),
            )
        )
        async with self._sm() as db:
            return int((await db.scalar(stmt)) or 0)


class InMemoryUserStore:
    """Sees what row-level security would let it see, so tests catch the same leaks."""

    def __init__(self, users: list[User] | None = None) -> None:
        self._users = {u.username: u for u in users or []}

    @staticmethod
    def _visible(user: User) -> bool:
        return is_system() or (user.tenant_id is not None and user.tenant_id == current_tenant())

    async def get(self, username: str) -> User | None:
        user = self._users.get(username)
        return user if user is not None and self._visible(user) else None

    async def list_all(self) -> list[User]:
        return [self._users[k] for k in sorted(self._users) if self._visible(self._users[k])]

    async def save(self, user: User) -> None:
        self._users[user.username] = user

    async def count_enabled_admins(self) -> int:
        return sum(1 for u in await self.list_all() if u.administers)
