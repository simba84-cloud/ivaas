"""Controlled bays on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.crate_control import (
    AcceptReason,
    BayStandard,
    CrateTransaction,
    StackObservation,
    TransactionStatus,
)
from ivaas.domain.drivers import Driver
from ivaas.domain.models import SessionDirection


class DriverRow(Base):
    __tablename__ = "drivers"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    badge_code: Mapped[str] = mapped_column(String(32))
    employee_ref: Mapped[str] = mapped_column(String(40))
    phone: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean)
    pin_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BayStandardRow(Base):
    __tablename__ = "bay_standards"
    bay_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    columns_per_unit: Mapped[int] = mapped_column(Integer)
    layers: Mapped[int] = mapped_column(Integer)
    controlled: Mapped[bool] = mapped_column(Boolean)


class CrateTransactionRow(Base):
    __tablename__ = "crate_transactions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    bay_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    driver_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    direction: Mapped[str] = mapped_column(String(16))
    declared_units: Mapped[int] = mapped_column(Integer)
    standard_columns: Mapped[int] = mapped_column(Integer)
    standard_layers: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_in_by: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_columns: Mapped[int | None] = mapped_column(Integer)
    non_standard_columns: Mapped[int | None] = mapped_column(Integer)
    unsure_columns: Mapped[int | None] = mapped_column(Integer)
    counted_crates: Mapped[int | None] = mapped_column(Integer)
    validated_status: Mapped[str | None] = mapped_column(String(16))
    accepted_by: Mapped[str | None] = mapped_column(String(128))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accept_reason: Mapped[str | None] = mapped_column(String(32))
    accept_note: Mapped[str | None] = mapped_column(String(280))


class StackObservationRow(Base):
    __tablename__ = "stack_observations"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    track_id: Mapped[int] = mapped_column(Integer)
    camera_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    direction: Mapped[str] = mapped_column(String(16))
    crates: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    agreement: Mapped[float | None] = mapped_column(Float)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_DRIVER = ("id", "name", "badge_code", "employee_ref", "phone", "active", "pin_hash", "created_at")
_TXN = tuple(c.name for c in CrateTransactionRow.__table__.columns)
_OBS = tuple(c.name for c in StackObservationRow.__table__.columns)


def _txn_values(t: CrateTransaction) -> dict:
    v = {f: getattr(t, f) for f in _TXN}
    for k in ("direction", "status", "validated_status", "accept_reason"):
        v[k] = v[k].value if v[k] is not None else None
    return v


def _txn_of(r: CrateTransactionRow) -> CrateTransaction:
    v = {f: getattr(r, f) for f in _TXN}
    v["direction"] = SessionDirection(v["direction"])
    v["status"] = TransactionStatus(v["status"])
    if v["validated_status"]:
        v["validated_status"] = TransactionStatus(v["validated_status"])
    if v["accept_reason"]:
        v["accept_reason"] = AcceptReason(v["accept_reason"])
    return CrateTransaction(**v)


def _obs_of(r: StackObservationRow) -> StackObservation:
    v = {f: getattr(r, f) for f in _OBS}
    v["direction"] = SessionDirection(v["direction"])
    return StackObservation(**v)


async def _upsert(sm, row_type, values: dict, key) -> None:
    stmt = insert(row_type).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=[key], set_=values)
    async with sm.begin() as db:
        await db.execute(stmt)


class PostgresCrateControlStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    # drivers
    async def save_driver(self, driver: Driver) -> None:
        await _upsert(self._sm, DriverRow, {f: getattr(driver, f) for f in _DRIVER}, DriverRow.id)

    async def get_driver(self, driver_id: UUID) -> Driver | None:
        async with self._sm() as db:
            r = await db.get(DriverRow, driver_id)
        return Driver(**{f: getattr(r, f) for f in _DRIVER}) if r else None

    async def list_drivers(self) -> list[Driver]:
        async with self._sm() as db:
            rows = (await db.scalars(select(DriverRow).order_by(DriverRow.name))).all()
        return [Driver(**{f: getattr(r, f) for f in _DRIVER}) for r in rows]

    # standards
    async def save_standard(self, standard: BayStandard) -> None:
        values = {
            "bay_id": standard.bay_id,
            "columns_per_unit": standard.columns,
            "layers": standard.layers,
            "controlled": standard.controlled,
        }
        await _upsert(self._sm, BayStandardRow, values, BayStandardRow.bay_id)

    @staticmethod
    def _standard_of(r: BayStandardRow) -> BayStandard:
        return BayStandard(r.bay_id, r.columns_per_unit, r.layers, r.controlled)

    async def get_standard(self, bay_id: UUID) -> BayStandard | None:
        async with self._sm() as db:
            r = await db.get(BayStandardRow, bay_id)
        return self._standard_of(r) if r else None

    async def list_standards(self) -> list[BayStandard]:
        async with self._sm() as db:
            rows = (await db.scalars(select(BayStandardRow))).all()
        return [self._standard_of(r) for r in rows]

    # transactions
    async def save_transaction(self, txn: CrateTransaction) -> None:
        await _upsert(self._sm, CrateTransactionRow, _txn_values(txn), CrateTransactionRow.id)

    async def get_transaction(self, txn_id: UUID) -> CrateTransaction | None:
        async with self._sm() as db:
            r = await db.get(CrateTransactionRow, txn_id)
        return _txn_of(r) if r else None

    async def open_transaction_for_bay(self, bay_id: UUID) -> CrateTransaction | None:
        q = select(CrateTransactionRow).where(
            CrateTransactionRow.bay_id == bay_id,
            CrateTransactionRow.status == TransactionStatus.OPEN.value,
        )
        async with self._sm() as db:
            r = (await db.scalars(q.limit(1))).first()
        return _txn_of(r) if r else None

    async def transaction_for_session(self, session_id: UUID) -> CrateTransaction | None:
        q = select(CrateTransactionRow).where(CrateTransactionRow.session_id == session_id)
        async with self._sm() as db:
            r = (await db.scalars(q.limit(1))).first()
        return _txn_of(r) if r else None

    async def list_transactions(self, since: datetime, limit: int = 1000) -> list[CrateTransaction]:
        q = (
            select(CrateTransactionRow)
            .where(CrateTransactionRow.opened_at >= since)
            .order_by(CrateTransactionRow.opened_at.desc())
            .limit(limit)
        )
        async with self._sm() as db:
            rows = (await db.scalars(q)).all()
        return [_txn_of(r) for r in rows]

    # observations
    async def add_observation(self, obs: StackObservation) -> None:
        values = {f: getattr(obs, f) for f in _OBS}
        values["direction"] = obs.direction.value
        async with self._sm.begin() as db:
            await db.execute(insert(StackObservationRow).values(**values))

    async def observations_for(self, session_id: UUID) -> list[StackObservation]:
        q = (
            select(StackObservationRow)
            .where(StackObservationRow.session_id == session_id)
            .order_by(StackObservationRow.at)
        )
        async with self._sm() as db:
            rows = (await db.scalars(q)).all()
        return [_obs_of(r) for r in rows]


class InMemoryCrateControlStore:
    def __init__(self) -> None:
        self._drivers: dict[UUID, Driver] = {}
        self._standards: dict[UUID, BayStandard] = {}
        self._txns: dict[UUID, CrateTransaction] = {}
        self._obs: list[StackObservation] = []

    async def save_driver(self, driver: Driver) -> None:
        self._drivers[driver.id] = driver

    async def get_driver(self, driver_id: UUID) -> Driver | None:
        return self._drivers.get(driver_id)

    async def list_drivers(self) -> list[Driver]:
        return sorted(self._drivers.values(), key=lambda d: d.name)

    async def save_standard(self, standard: BayStandard) -> None:
        self._standards[standard.bay_id] = standard

    async def get_standard(self, bay_id: UUID) -> BayStandard | None:
        return self._standards.get(bay_id)

    async def list_standards(self) -> list[BayStandard]:
        return list(self._standards.values())

    async def save_transaction(self, txn: CrateTransaction) -> None:
        self._txns[txn.id] = txn

    async def get_transaction(self, txn_id: UUID) -> CrateTransaction | None:
        return self._txns.get(txn_id)

    async def open_transaction_for_bay(self, bay_id: UUID) -> CrateTransaction | None:
        return next(
            (
                t
                for t in self._txns.values()
                if t.bay_id == bay_id and t.status is TransactionStatus.OPEN
            ),
            None,
        )

    async def transaction_for_session(self, session_id: UUID) -> CrateTransaction | None:
        return next((t for t in self._txns.values() if t.session_id == session_id), None)

    async def list_transactions(self, since: datetime, limit: int = 1000) -> list[CrateTransaction]:
        found = [t for t in self._txns.values() if t.opened_at >= since]
        return sorted(found, key=lambda t: t.opened_at, reverse=True)[:limit]

    async def add_observation(self, obs: StackObservation) -> None:
        self._obs.append(obs)

    async def observations_for(self, session_id: UUID) -> list[StackObservation]:
        return sorted((o for o in self._obs if o.session_id == session_id), key=lambda o: o.at)
