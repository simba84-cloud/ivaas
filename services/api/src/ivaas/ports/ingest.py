"""Port for the ingest ledger: which edge events have already been applied.

The edge node spools every event to disk and removes it only once the API has
acknowledged it. If the node crashes between the API applying an event and the
node removing it, the event is sent again after the restart. Without a ledger
those crates would be counted twice; with it, a repeat is recognised and skipped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID


class IngestLedger(Protocol):
    async def claim(self, event_id: UUID, kind: str, at: datetime) -> bool:
        """True the first time an event is seen (and records it); False for a repeat."""
        ...

    async def release(self, event_id: UUID) -> None:
        """Forget a claim whose event could not be applied, so a retry is not refused."""
        ...

    async def prune(self, before: datetime) -> int:
        """Forget events older than any node could still be replaying."""
        ...
