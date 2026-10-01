"""Ports for webhooks: where endpoints and deliveries are kept, and how one is sent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ivaas.domain.webhooks import Delivery, WebhookEndpoint


class WebhookStore(Protocol):
    """Tenant-scoped, like every store."""

    async def save_endpoint(self, endpoint: WebhookEndpoint) -> None: ...

    async def endpoints(self) -> list[WebhookEndpoint]: ...

    async def endpoint(self, endpoint_id: UUID) -> WebhookEndpoint | None: ...

    async def delete_endpoint(self, endpoint_id: UUID) -> bool:
        """Remove it and its deliveries; False if there was none."""
        ...

    async def save_delivery(self, delivery: Delivery) -> None: ...

    async def delivery(self, delivery_id: UUID) -> Delivery | None: ...

    async def deliveries(self, endpoint_id: UUID, limit: int = 50) -> list[Delivery]:
        """Newest first."""
        ...

    async def claim_due(self, now: datetime, limit: int = 20) -> list[Delivery]:
        """Pending deliveries due by `now`, each claimed (`Delivery.claim`) so that a
        second sender running at the same moment does not take it too."""
        ...


@dataclass(frozen=True)
class SendResult:
    status_code: int | None  # None: nothing came back (refused, timed out, unreachable)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


class WebhookSender(Protocol):
    async def send(self, url: str, headers: dict[str, str], body: bytes) -> SendResult: ...
