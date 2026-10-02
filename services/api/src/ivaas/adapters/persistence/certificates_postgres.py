"""Deletion certificates: platform records that outlive the tenant they certify."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.lifecycle import DeletionCertificate

_FIELDS = (
    "id",
    "purged_tenant_id",
    "tenant_slug",
    "tenant_name",
    "purged_at",
    "purged_by",
    "body",
    "signature",
)


class CertificateRow(Base):
    __tablename__ = "deletion_certificates"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    purged_tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tenant_slug: Mapped[str] = mapped_column(String(64))
    tenant_name: Mapped[str] = mapped_column(String(120))
    purged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purged_by: Mapped[str] = mapped_column(String(120))
    body: Mapped[Any] = mapped_column(JSONB)
    signature: Mapped[str] = mapped_column(String(64))


class PostgresCertificateStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, cert: DeletionCertificate) -> None:
        async with self._sm.begin() as db:  # insert only: a certificate is never changed
            db.add(CertificateRow(**{f: getattr(cert, f) for f in _FIELDS}))

    async def list_all(self) -> list[DeletionCertificate]:
        async with self._sm() as db:
            stmt = select(CertificateRow).order_by(CertificateRow.purged_at.desc())
            rows = (await db.scalars(stmt)).all()
        return [DeletionCertificate(**{f: getattr(r, f) for f in _FIELDS}) for r in rows]


class InMemoryCertificateStore:
    def __init__(self) -> None:
        self._certs: list[DeletionCertificate] = []

    async def save(self, cert: DeletionCertificate) -> None:
        self._certs.append(cert)

    async def list_all(self) -> list[DeletionCertificate]:
        return sorted(self._certs, key=lambda c: c.purged_at, reverse=True)
