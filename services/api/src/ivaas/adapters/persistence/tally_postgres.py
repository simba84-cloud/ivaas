"""Tally sheets on Postgres, and the in-memory store that defines the contract."""

from __future__ import annotations

import copy
import datetime as dt
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, Time, delete, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.models import SessionDirection
from ivaas.domain.tally import TallyLine, TallySheet, TallyStatus

UNRESOLVED = (TallyStatus.PENDING, TallyStatus.MATCHED, TallyStatus.UNMATCHED)


class TallySheetRow(Base):
    __tablename__ = "tally_sheets"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    sheet_id: Mapped[str] = mapped_column(String(80), unique=True)
    bay_id: Mapped[UUID] = mapped_column(ForeignKey("bays.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    plate: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    start_time: Mapped[dt.time | None] = mapped_column(Time)
    end_time: Mapped[dt.time | None] = mapped_column(Time)
    total_on_paper: Mapped[int | None] = mapped_column(Integer)
    pages: Mapped[int | None] = mapped_column(Integer)
    counted_by: Mapped[str | None] = mapped_column(String(120))
    verified_by: Mapped[str | None] = mapped_column(String(120))
    entered_by: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    entered_by_user: Mapped[str] = mapped_column(String(120))
    entered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("loading_sessions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)


class TallyLineRow(Base):
    __tablename__ = "tally_lines"
    sheet_pk: Mapped[UUID] = mapped_column(
        ForeignKey("tally_sheets.id", ondelete="CASCADE"), primary_key=True
    )
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    crates: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(40))


_FIELDS = (
    "sheet_id",
    "bay_id",
    "date",
    "plate",
    "start_time",
    "end_time",
    "total_on_paper",
    "pages",
    "counted_by",
    "verified_by",
    "entered_by",
    "notes",
    "entered_by_user",
    "entered_at",
    "session_id",
)


def _values(s: TallySheet) -> dict:
    return {
        "id": s.id,
        **{f: getattr(s, f) for f in _FIELDS},
        "direction": s.direction.value,
        "status": s.status.value,
    }


def _of(r: TallySheetRow, lines: list[TallyLineRow]) -> TallySheet:
    return TallySheet(
        id=r.id,
        **{f: getattr(r, f) for f in _FIELDS},
        direction=SessionDirection(r.direction),
        status=TallyStatus(r.status),
        lines=[TallyLine(ln.line_no, ln.crates, ln.note) for ln in lines],
    )


class PostgresTallySheetStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, sheet: TallySheet) -> None:
        values = _values(sheet)
        update = {k: v for k, v in values.items() if k != "id"}
        async with self._sm.begin() as db:
            stmt = (
                insert(TallySheetRow)
                .values(**values)
                .on_conflict_do_update(index_elements=[TallySheetRow.sheet_id], set_=update)
                .returning(TallySheetRow.id)
            )
            pk = (await db.execute(stmt)).scalar_one()
            sheet.id = pk  # the row keeps the id it was first stored with
            await db.execute(delete(TallyLineRow).where(TallyLineRow.sheet_pk == pk))
            for ln in sheet.lines:
                db.add(
                    TallyLineRow(sheet_pk=pk, line_no=ln.line_no, crates=ln.crates, note=ln.note)
                )

    async def _load(self, db: AsyncSession, rows: list[TallySheetRow]) -> list[TallySheet]:
        if not rows:
            return []
        lines = (
            await db.scalars(
                select(TallyLineRow).where(TallyLineRow.sheet_pk.in_([r.id for r in rows]))
            )
        ).all()
        by_sheet: dict[UUID, list[TallyLineRow]] = {}
        for ln in lines:
            by_sheet.setdefault(ln.sheet_pk, []).append(ln)
        return [_of(r, by_sheet.get(r.id, [])) for r in rows]

    async def get_by_sheet_id(self, sheet_id: str) -> TallySheet | None:
        async with self._sm() as db:
            row = (
                await db.scalars(select(TallySheetRow).where(TallySheetRow.sheet_id == sheet_id))
            ).first()
            found = await self._load(db, [row] if row else [])
        return found[0] if found else None

    async def list_recent(self, *, limit: int = 200) -> list[TallySheet]:
        stmt = (
            select(TallySheetRow)
            .order_by(TallySheetRow.entered_at.desc())
            .limit(max(1, min(limit, 1000)))
        )
        async with self._sm() as db:
            return await self._load(db, list((await db.scalars(stmt)).all()))

    async def list_unresolved(self) -> list[TallySheet]:
        stmt = (
            select(TallySheetRow)
            .where(TallySheetRow.status.in_([s.value for s in UNRESOLVED]))
            .order_by(TallySheetRow.entered_at)
        )
        async with self._sm() as db:
            return await self._load(db, list((await db.scalars(stmt)).all()))

    async def session_ids_taken(self) -> set[UUID]:
        stmt = select(TallySheetRow.session_id).where(TallySheetRow.session_id.is_not(None))
        async with self._sm() as db:
            return set((await db.scalars(stmt)).all())


class InMemoryTallySheetStore:
    def __init__(self) -> None:
        self._sheets: dict[str, TallySheet] = {}

    async def save(self, sheet: TallySheet) -> None:
        existing = self._sheets.get(sheet.sheet_id)
        if existing is not None:
            sheet.id = existing.id
        self._sheets[sheet.sheet_id] = copy.deepcopy(sheet)

    async def get_by_sheet_id(self, sheet_id: str) -> TallySheet | None:
        found = self._sheets.get(sheet_id)
        return copy.deepcopy(found) if found else None

    async def list_recent(self, *, limit: int = 200) -> list[TallySheet]:
        rows = sorted(self._sheets.values(), key=lambda s: s.entered_at, reverse=True)
        return [copy.deepcopy(s) for s in rows[: max(1, min(limit, 1000))]]

    async def list_unresolved(self) -> list[TallySheet]:
        rows = sorted(
            (s for s in self._sheets.values() if s.status in UNRESOLVED), key=lambda s: s.entered_at
        )
        return [copy.deepcopy(s) for s in rows]

    async def session_ids_taken(self) -> set[UUID]:
        return {s.session_id for s in self._sheets.values() if s.session_id is not None}
