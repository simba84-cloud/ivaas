"""The model registry on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.ml_models import ModelVersion

_FIELDS = (
    "id",
    "name",
    "version",
    "sha256",
    "size_bytes",
    "object_key",
    "meta",
    "notes",
    "created_by",
    "created_at",
)


class ModelVersionRow(Base):
    __tablename__ = "model_versions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))  # column default
    name: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    object_key: Mapped[str] = mapped_column(String(300))
    meta: Mapped[Any] = mapped_column(JSONB)
    notes: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _of(r: ModelVersionRow) -> ModelVersion:
    return ModelVersion(
        **{f: getattr(r, f) for f in _FIELDS if f != "meta"},
        meta=dict(r.meta or {}),
        tenant_id=r.tenant_id,
    )


class PostgresModelRegistry:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, model: ModelVersion) -> None:
        values = {f: getattr(model, f) for f in _FIELDS}
        stmt = insert(ModelVersionRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[ModelVersionRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get(self, model_id: UUID) -> ModelVersion | None:
        async with self._sm() as db:
            r = await db.get(ModelVersionRow, model_id)
        return _of(r) if r else None

    async def find(self, name: str, version: str) -> ModelVersion | None:
        stmt = select(ModelVersionRow).where(
            ModelVersionRow.name == name, ModelVersionRow.version == version
        )
        async with self._sm() as db:
            r = (await db.scalars(stmt)).first()
        return _of(r) if r else None

    async def list_all(self) -> list[ModelVersion]:
        stmt = select(ModelVersionRow).order_by(
            ModelVersionRow.name, ModelVersionRow.created_at.desc()
        )
        async with self._sm() as db:
            return [_of(r) for r in (await db.scalars(stmt)).all()]


class InMemoryModelRegistry:
    def __init__(self) -> None:
        self._models: dict[UUID, ModelVersion] = {}

    async def save(self, model: ModelVersion) -> None:
        self._models[model.id] = model

    async def get(self, model_id: UUID) -> ModelVersion | None:
        return self._models.get(model_id)

    async def find(self, name: str, version: str) -> ModelVersion | None:
        return next(
            (m for m in self._models.values() if (m.name, m.version) == (name, version)), None
        )

    async def list_all(self) -> list[ModelVersion]:
        return sorted(self._models.values(), key=lambda m: (m.name, -m.created_at.timestamp()))
