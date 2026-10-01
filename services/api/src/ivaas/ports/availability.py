"""Port for edge availability history (domain/availability.py)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ivaas.domain.availability import Period


class AvailabilityStore(Protocol):
    """Tenant-scoped, like every store."""

    async def latest(self, node_id: UUID) -> dict[UUID | None, Period]:
        """Each subject's most recent period for this node: the node (key None) and
        each of its cameras."""
        ...

    async def save_all(self, periods: list[Period]) -> None: ...

    async def between(self, node_ids: list[UUID], start: datetime, end: datetime) -> list[Period]:
        """Periods of these nodes (and their cameras) that overlap [start, end)."""
        ...
