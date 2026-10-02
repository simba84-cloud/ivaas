"""Tenants' SSO settings on Postgres (row-level secured), and in memory. The client
secret is sealed with the platform's secret box, as camera credentials are.

Sign-in reads a tenant's settings before anyone is signed in, in `system_context()`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, delete, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.rbac import Role
from ivaas.domain.sso import SsoConfig
from ivaas.tenancy import current_tenant, is_system


class SsoRow(Base):
    __tablename__ = "sso_configs"
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(300))
    client_id: Mapped[str] = mapped_column(String(300))
    client_secret: Mapped[str] = mapped_column(String(2048))
    domains: Mapped[Any] = mapped_column(JSONB)
    default_role: Mapped[str | None] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(Boolean)
    updated_by: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresSsoStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession], box: Any) -> None:
        self._sm, self._box = sm, box

    async def get(self, tenant_id: UUID) -> SsoConfig | None:
        async with self._sm() as db:
            r = await db.get(SsoRow, tenant_id)
        if r is None:
            return None
        return SsoConfig(
            tenant_id=r.tenant_id,
            issuer=r.issuer,
            client_id=r.client_id,
            client_secret=self._box.open(r.client_secret),
            domains=list(r.domains),
            default_role=Role(r.default_role) if r.default_role else None,
            required=r.required,
            updated_by=r.updated_by,
            updated_at=r.updated_at,
        )

    async def save(self, c: SsoConfig) -> None:
        values = {
            "tenant_id": c.tenant_id,
            "issuer": c.issuer,
            "client_id": c.client_id,
            "client_secret": self._box.seal(c.client_secret),
            "domains": c.domains,
            "default_role": c.default_role.value if c.default_role else None,
            "required": c.required,
            "updated_by": c.updated_by,
            "updated_at": c.updated_at,
        }
        stmt = insert(SsoRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[SsoRow.tenant_id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def delete(self, tenant_id: UUID) -> None:
        async with self._sm.begin() as db:
            await db.execute(delete(SsoRow).where(SsoRow.tenant_id == tenant_id))

    async def list_all(self) -> list[SsoConfig]:
        async with self._sm() as db:
            ids = (await db.scalars(select(SsoRow.tenant_id))).all()
        return [c for i in ids if (c := await self.get(i))]


class InMemorySsoStore:
    """Sees what row-level security would let it see."""

    def __init__(self) -> None:
        self._configs: dict[UUID, SsoConfig] = {}

    @staticmethod
    def _visible(tenant_id: UUID) -> bool:
        return is_system() or tenant_id == current_tenant()

    async def get(self, tenant_id: UUID) -> SsoConfig | None:
        return self._configs.get(tenant_id) if self._visible(tenant_id) else None

    async def save(self, c: SsoConfig) -> None:
        self._configs[c.tenant_id] = c

    async def delete(self, tenant_id: UUID) -> None:
        if self._visible(tenant_id):
            self._configs.pop(tenant_id, None)

    async def list_all(self) -> list[SsoConfig]:
        return [c for t, c in self._configs.items() if self._visible(t)]
