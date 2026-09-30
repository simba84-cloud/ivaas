"""Manifest lines and exceptions on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.manifests import (
    ExceptionKind,
    ExceptionStatus,
    LineStatus,
    ManifestException,
    ManifestLine,
)
from ivaas.domain.models import SessionDirection


class ManifestLineRow(Base):
    __tablename__ = "manifest_lines"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    reference: Mapped[str] = mapped_column(String(80))
    day: Mapped[date] = mapped_column(Date)
    plate: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(16))
    expected: Mapped[int] = mapped_column(Integer)
    site_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    route: Mapped[str] = mapped_column(String(80))
    session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16))
    imported_by: Mapped[str] = mapped_column(String(120))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExceptionRow(Base):
    __tablename__ = "manifest_exceptions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    day: Mapped[date] = mapped_column(Date)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    plate: Mapped[str | None] = mapped_column(String(16))
    route: Mapped[str] = mapped_column(String(80))
    session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    line_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    expected: Mapped[int | None] = mapped_column(Integer)
    counted: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    resolved_by: Mapped[str | None] = mapped_column(String(120))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)


_LINE = (
    "id",
    "reference",
    "day",
    "plate",
    "expected",
    "site_id",
    "route",
    "session_id",
    "imported_by",
    "imported_at",
)
_EXC = (
    "id",
    "day",
    "raised_at",
    "plate",
    "route",
    "session_id",
    "line_id",
    "expected",
    "counted",
    "resolved_by",
    "resolved_at",
    "resolution_note",
)


def _line(r: ManifestLineRow) -> ManifestLine:
    return ManifestLine(
        direction=SessionDirection(r.direction),
        status=LineStatus(r.status),
        **{f: getattr(r, f) for f in _LINE},
    )


def _exc(r: ExceptionRow) -> ManifestException:
    return ManifestException(
        kind=ExceptionKind(r.kind),
        status=ExceptionStatus(r.status),
        **{f: getattr(r, f) for f in _EXC},
    )


class PostgresManifestStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, line: ManifestLine) -> None:
        values = {f: getattr(line, f) for f in _LINE} | {
            "direction": line.direction.value,
            "status": line.status.value,
        }
        stmt = insert(ManifestLineRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[ManifestLineRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def find(self, reference: str, direction: SessionDirection) -> ManifestLine | None:
        stmt = select(ManifestLineRow).where(
            ManifestLineRow.reference == reference,
            ManifestLineRow.direction == direction.value,
        )
        async with self._sm() as db:
            r = (await db.scalars(stmt)).first()
        return _line(r) if r else None

    async def since(self, day: date) -> list[ManifestLine]:
        stmt = select(ManifestLineRow).where(ManifestLineRow.day >= day)
        async with self._sm() as db:
            return [_line(r) for r in (await db.scalars(stmt)).all()]


class PostgresExceptionStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, e: ManifestException) -> None:
        values = {f: getattr(e, f) for f in _EXC} | {"kind": e.kind.value, "status": e.status.value}
        stmt = insert(ExceptionRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[ExceptionRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get(self, exception_id: UUID) -> ManifestException | None:
        async with self._sm() as db:
            r = await db.get(ExceptionRow, exception_id)
        return _exc(r) if r else None

    async def find(
        self,
        *,
        kind: ExceptionKind,
        line_id: UUID | None = None,
        session_id: UUID | None = None,
    ) -> ManifestException | None:
        stmt = select(ExceptionRow).where(ExceptionRow.kind == kind.value)
        if line_id is not None:
            stmt = stmt.where(ExceptionRow.line_id == line_id)
        if session_id is not None:
            stmt = stmt.where(ExceptionRow.session_id == session_id)
        async with self._sm() as db:
            r = (await db.scalars(stmt.limit(1))).first()
        return _exc(r) if r else None

    async def list(
        self, *, status: ExceptionStatus | None = None, since: date | None = None
    ) -> list[ManifestException]:
        stmt = select(ExceptionRow).order_by(ExceptionRow.raised_at.desc()).limit(1000)
        if status is not None:
            stmt = stmt.where(ExceptionRow.status == status.value)
        if since is not None:
            stmt = stmt.where(ExceptionRow.day >= since)
        async with self._sm() as db:
            return [_exc(r) for r in (await db.scalars(stmt)).all()]


class InMemoryManifestStore:
    def __init__(self) -> None:
        self._lines: dict[UUID, ManifestLine] = {}

    async def save(self, line: ManifestLine) -> None:
        self._lines[line.id] = line

    async def find(self, reference: str, direction: SessionDirection) -> ManifestLine | None:
        return next(
            (
                x
                for x in self._lines.values()
                if (x.reference, x.direction) == (reference, direction)
            ),
            None,
        )

    async def since(self, day: date) -> list[ManifestLine]:
        return [x for x in self._lines.values() if x.day >= day]


class InMemoryExceptionStore:
    def __init__(self) -> None:
        self._items: dict[UUID, ManifestException] = {}

    async def save(self, e: ManifestException) -> None:
        self._items[e.id] = e

    async def get(self, exception_id: UUID) -> ManifestException | None:
        return self._items.get(exception_id)

    async def find(self, *, kind, line_id=None, session_id=None) -> ManifestException | None:
        for e in self._items.values():
            if (
                e.kind is kind
                and (line_id is None or e.line_id == line_id)
                and (session_id is None or e.session_id == session_id)
            ):
                return e
        return None

    async def list(self, *, status=None, since=None) -> list[ManifestException]:
        rows = [
            e
            for e in self._items.values()
            if (status is None or e.status is status) and (since is None or e.day >= since)
        ]
        return sorted(rows, key=lambda e: e.raised_at, reverse=True)
