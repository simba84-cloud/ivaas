"""Outbound port for alert acknowledgements."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ivaas.domain.alerts import AlertAcknowledgement


class AcknowledgementStore(Protocol):
    async def get(self, key: str) -> AlertAcknowledgement | None: ...

    async def add(self, ack: AlertAcknowledgement) -> AlertAcknowledgement:
        """Store it unless the key is already acknowledged; return whichever is kept.
        The first acknowledgement stands: a second person agreeing is not news."""
        ...

    async def list_since(self, since: datetime) -> list[AlertAcknowledgement]: ...
