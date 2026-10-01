"""Partners, tenants and provisioning records on Postgres, plus the in-memory twin.

Partners and tenants are platform records: they carry no tenant_id of their own and
are reached only through platform endpoints and the auth layer. Provisioning
records do belong to a tenant and are row-level secured like any other.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.tenancy import (
    Partner,
    ProvisioningRecord,
    ProvisioningStep,
    Tenant,
    TenantStatus,
)
from ivaas.tenancy import current_tenant, is_system


class PartnerRow(Base):
    __tablename__ = "partners"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))


class TenantRow(Base):
    __tablename__ = "tenants"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    partner_id: Mapped[UUID | None] = mapped_column(ForeignKey("partners.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    on_hold: Mapped[bool] = mapped_column(Boolean, default=False)


class ProvisioningRow(Base):
    __tablename__ = "tenant_provisioning"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    owner_username: Mapped[str] = mapped_column(String(64))
    steps: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _tenant(r: TenantRow) -> Tenant:
    return Tenant(
        r.id, r.slug, r.name, r.partner_id, TenantStatus(r.status), r.created_at, r.on_hold
    )


def _record(r: ProvisioningRow) -> ProvisioningRecord:
    return ProvisioningRecord(
        idempotency_key=r.idempotency_key,
        tenant_id=r.tenant_id,
        owner_username=r.owner_username,
        steps=[ProvisioningStep(**s) for s in r.steps],
        created_at=r.created_at,
        id=r.id,
    )


class PostgresTenantStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def get(self, tenant_id: UUID) -> Tenant | None:
        async with self._sm() as db:
            r = await db.get(TenantRow, tenant_id)
        return _tenant(r) if r else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        async with self._sm() as db:
            r = (await db.scalars(select(TenantRow).where(TenantRow.slug == slug))).first()
        return _tenant(r) if r else None

    async def list_all(self, *, partner_id: UUID | None = None) -> list[Tenant]:
        stmt = select(TenantRow).order_by(TenantRow.name)
        if partner_id is not None:
            stmt = stmt.where(TenantRow.partner_id == partner_id)
        async with self._sm() as db:
            return [_tenant(r) for r in (await db.scalars(stmt)).all()]

    async def save(self, tenant: Tenant) -> None:
        values = {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "partner_id": tenant.partner_id,
            "status": tenant.status.value,
            "on_hold": tenant.on_hold,
            "created_at": tenant.created_at,
        }
        stmt = insert(TenantRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[TenantRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get_partner(self, partner_id: UUID) -> Partner | None:
        async with self._sm() as db:
            r = await db.get(PartnerRow, partner_id)
        return Partner(r.id, r.slug, r.name) if r else None

    async def list_partners(self) -> list[Partner]:
        async with self._sm() as db:
            rows = (await db.scalars(select(PartnerRow).order_by(PartnerRow.name))).all()
        return [Partner(r.id, r.slug, r.name) for r in rows]

    async def save_partner(self, partner: Partner) -> None:
        values = {"id": partner.id, "slug": partner.slug, "name": partner.name}
        stmt = insert(PartnerRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[PartnerRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get_provisioning(self, idempotency_key: str) -> ProvisioningRecord | None:
        async with self._sm() as db:
            r = (
                await db.scalars(
                    select(ProvisioningRow).where(
                        ProvisioningRow.idempotency_key == idempotency_key
                    )
                )
            ).first()
        return _record(r) if r else None

    async def save_provisioning(self, record: ProvisioningRecord) -> None:
        values = {
            "id": record.id,
            "idempotency_key": record.idempotency_key,
            "tenant_id": record.tenant_id,
            "owner_username": record.owner_username,
            "steps": [vars(s) for s in record.steps],
            "created_at": record.created_at,
        }
        stmt = insert(ProvisioningRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[ProvisioningRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)


class InMemoryTenantStore:
    def __init__(self) -> None:
        self._tenants: dict[UUID, Tenant] = {}
        self._partners: dict[UUID, Partner] = {}
        self._records: dict[str, ProvisioningRecord] = {}

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return self._tenants.get(tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self._tenants.values() if t.slug == slug), None)

    async def list_all(self, *, partner_id: UUID | None = None) -> list[Tenant]:
        rows = [t for t in self._tenants.values() if partner_id in (None, t.partner_id)]
        return sorted(rows, key=lambda t: t.name)

    async def save(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant

    async def get_partner(self, partner_id: UUID) -> Partner | None:
        return self._partners.get(partner_id)

    async def list_partners(self) -> list[Partner]:
        return sorted(self._partners.values(), key=lambda p: p.name)

    async def save_partner(self, partner: Partner) -> None:
        self._partners[partner.id] = partner

    async def get_provisioning(self, idempotency_key: str) -> ProvisioningRecord | None:
        record = self._records.get(idempotency_key)
        # a provisioning record belongs to its tenant, like any tenant row
        if record is None or not (is_system() or record.tenant_id == current_tenant()):
            return None
        return record

    async def save_provisioning(self, record: ProvisioningRecord) -> None:
        self._records[record.idempotency_key] = record
