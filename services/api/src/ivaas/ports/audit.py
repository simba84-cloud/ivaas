"""Outbound port for the audit trail."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ivaas.domain.audit import AuditAction, AuditEntry


class AuditLog(Protocol):
    async def record(self, entry: AuditEntry) -> None:
        """Append one entry. Never raises into the caller's path: an action that
        succeeded must not be reported as failed because its audit write did."""
        ...

    async def list_recent(
        self,
        *,
        since: datetime | None = None,
        actor: str | None = None,
        action: AuditAction | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]: ...
