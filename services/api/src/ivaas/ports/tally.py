"""Outbound port for tally sheets (the paper ground truth)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ivaas.domain.tally import TallySheet


class TallySheetStore(Protocol):
    async def save(self, sheet: TallySheet) -> None:
        """Upsert by `sheet_id`, replacing its lines. The stored row keeps its first `id`."""
        ...

    async def get_by_sheet_id(self, sheet_id: str) -> TallySheet | None: ...

    async def list_recent(self, *, limit: int = 200) -> list[TallySheet]:
        """Newest entry first."""
        ...

    async def list_unresolved(self) -> list[TallySheet]:
        """Sheets that could still reconcile: pending, matched or unmatched."""
        ...

    async def session_ids_taken(self) -> set[UUID]:
        """Sessions already claimed by a sheet."""
        ...
