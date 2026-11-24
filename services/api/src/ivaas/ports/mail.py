"""Port for sending email, and for keeping the alert emails waiting to go."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ivaas.domain.alert_email import AlertEmail


@dataclass(frozen=True)
class OutgoingMail:
    to: tuple[str, ...]
    subject: str
    text: str
    html: str | None = None


@dataclass(frozen=True)
class MailResult:
    ok: bool
    #: why it was not sent, or, when sent, the recipients the relay refused
    error: str | None = None


class MailSender(Protocol):
    #: False when the deployment has no relay: nothing should be queued for it
    configured: bool

    async def send(self, mail: OutgoingMail) -> MailResult: ...


class AlertEmailStore(Protocol):
    """Tenant-scoped, like every store."""

    async def queue(self, email: AlertEmail) -> bool:
        """Keep it to be sent. False, and nothing kept, if one with the same dedupe
        key already exists: the same alert is never emailed twice."""
        ...

    async def exists(self, dedupe_key: str) -> bool: ...

    async def save(self, email: AlertEmail) -> None: ...

    async def recent(self, limit: int = 20) -> list[AlertEmail]:
        """Newest first."""
        ...

    async def claim_due(self, now: datetime, limit: int = 20) -> list[AlertEmail]:
        """Pending emails due by `now`, each claimed so that a second sender running at
        the same moment does not take it too."""
        ...
