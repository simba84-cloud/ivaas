"""The fleet register on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.fleet import Vehicle

_FIELDS = ("id", "plate", "fleet_number", "operator", "notes", "active", "created_at")


class VehicleRow(Base):
    __tablename__ = "vehicles"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    plate: Mapped[str] = mapped_column(String(16))
    plate_key: Mapped[str] = mapped_column(String(16))
    fleet_number: Mapped[str] = mapped_column(String(40))
    operator: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostgresFleetStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, vehicle: Vehicle) -> None:
        values = {f: getattr(vehicle, f) for f in _FIELDS} | {"plate_key": vehicle.key}
        stmt = insert(VehicleRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[VehicleRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get(self, vehicle_id: UUID) -> Vehicle | None:
        async with self._sm() as db:
            r = await db.get(VehicleRow, vehicle_id)
        return Vehicle(**{f: getattr(r, f) for f in _FIELDS}) if r else None

    async def list_all(self) -> list[Vehicle]:
        async with self._sm() as db:
            rows = (await db.scalars(select(VehicleRow).order_by(VehicleRow.plate))).all()
        return [Vehicle(**{f: getattr(r, f) for f in _FIELDS}) for r in rows]


class InMemoryFleetStore:
    def __init__(self) -> None:
        self._vehicles: dict[UUID, Vehicle] = {}

    async def save(self, vehicle: Vehicle) -> None:
        self._vehicles[vehicle.id] = vehicle

    async def get(self, vehicle_id: UUID) -> Vehicle | None:
        return self._vehicles.get(vehicle_id)

    async def list_all(self) -> list[Vehicle]:
        return sorted(self._vehicles.values(), key=lambda v: v.plate)
