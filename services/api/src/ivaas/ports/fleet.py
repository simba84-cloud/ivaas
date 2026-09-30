"""Port for the fleet register."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ivaas.domain.fleet import Vehicle


class FleetStore(Protocol):
    async def save(self, vehicle: Vehicle) -> None: ...

    async def get(self, vehicle_id: UUID) -> Vehicle | None: ...

    async def list_all(self) -> list[Vehicle]: ...
