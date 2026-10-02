"""Port for break-glass grants."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ivaas.domain.break_glass import BreakGlassGrant


class BreakGlassStore(Protocol):
    async def save(self, grant: BreakGlassGrant) -> None: ...

    #: by id across tenants: authentication reads it before any tenant is known
    async def get(self, grant_id: UUID) -> BreakGlassGrant | None: ...

    #: every grant visible in context, newest first
    async def list_all(self) -> list[BreakGlassGrant]: ...
