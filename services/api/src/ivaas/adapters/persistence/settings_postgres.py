"""Runtime setting overrides, stored so they survive a restart."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base


class SettingRow(Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str] = mapped_column(String(120))


class PostgresSettingsStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def all(self) -> dict[str, Any]:
        async with self._sm() as db:
            rows = (await db.scalars(select(SettingRow))).all()
        return {r.key: r.value for r in rows}

    async def set(self, key: str, value: Any, by: str, at: datetime) -> None:
        values = {"key": key, "value": value, "updated_at": at, "updated_by": by}
        stmt = insert(SettingRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[SettingRow.key], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)


class InMemorySettingsStore:
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    async def all(self) -> dict[str, Any]:
        return dict(self._values)

    async def set(self, key: str, value: Any, by: str, at: datetime) -> None:
        self._values[key] = value
