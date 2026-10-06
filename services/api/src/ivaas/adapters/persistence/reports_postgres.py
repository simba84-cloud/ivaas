"""Filed daily reports on Postgres (tenant-owned, row-level secured), and in memory."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Integer, String, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.application.reports import StoredReport

_FIELDS = ("id", "site_id", "day", "pdf_key", "csv_key", "xlsx_key", "loads", "generated_at")


class ReportRow(Base):
    __tablename__ = "daily_reports"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    site_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    day: Mapped[date] = mapped_column(Date)
    pdf_key: Mapped[str] = mapped_column(String(300))
    csv_key: Mapped[str] = mapped_column(String(300))
    xlsx_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    loads: Mapped[int] = mapped_column(Integer)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PostgresReportStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def save(self, r: StoredReport) -> None:
        values = {f: getattr(r, f) for f in _FIELDS}
        stmt = insert(ReportRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "site_id", "day"],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def get(self, site_id: UUID, day: date) -> StoredReport | None:
        stmt = select(ReportRow).where(ReportRow.site_id == site_id, ReportRow.day == day)
        async with self._sm() as db:
            r = (await db.scalars(stmt)).first()
        return StoredReport(**{f: getattr(r, f) for f in _FIELDS}) if r else None

    async def since(self, day: date) -> list[StoredReport]:
        stmt = select(ReportRow).where(ReportRow.day >= day).order_by(ReportRow.day.desc())
        async with self._sm() as db:
            return [
                StoredReport(**{f: getattr(r, f) for f in _FIELDS})
                for r in (await db.scalars(stmt)).all()
            ]


class InMemoryReportStore:
    def __init__(self) -> None:
        self._items: dict[tuple[UUID, date], StoredReport] = {}

    async def save(self, r: StoredReport) -> None:
        self._items[(r.site_id, r.day)] = r

    async def get(self, site_id: UUID, day: date) -> StoredReport | None:
        return self._items.get((site_id, day))

    async def since(self, day: date) -> list[StoredReport]:
        return sorted(
            (r for r in self._items.values() if r.day >= day), key=lambda r: r.day, reverse=True
        )
