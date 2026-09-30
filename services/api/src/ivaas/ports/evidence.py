"""Port for evidence clips."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ivaas.domain.evidence import EvidenceClip


class EvidenceStore(Protocol):
    async def save(self, clip: EvidenceClip) -> None: ...

    async def for_session(self, session_id: UUID) -> list[EvidenceClip]: ...

    async def expired(self, now: datetime, limit: int = 500) -> list[EvidenceClip]: ...

    async def delete(self, clip_id: UUID) -> None: ...
