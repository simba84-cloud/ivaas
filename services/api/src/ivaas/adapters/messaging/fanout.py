"""Event fan-out. New sinks (webhooks, ERP integration, ...) are added by
appending another EventPublisher here, not by editing the use cases.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from uuid import UUID

from fastapi import WebSocket

from ivaas.ports.repositories import EventPublisher
from ivaas.tenancy import current_tenant

log = logging.getLogger(__name__)


class FanoutEventPublisher:
    def __init__(self, sinks: Sequence[EventPublisher]) -> None:
        self._sinks = list(sinks)

    async def publish(self, subject: str, payload: dict) -> None:
        results = await asyncio.gather(
            *(s.publish(subject, payload) for s in self._sinks), return_exceptions=True
        )
        for sink, result in zip(self._sinks, results, strict=True):
            if isinstance(result, Exception):
                # one failing sink must not lose the event for the others
                log.error("event sink %s failed for %s: %s", type(sink).__name__, subject, result)


class WebSocketHub:
    """EventPublisher that pushes each event to the portal clients of its tenant.

    Every socket is registered with the tenant of the person who opened it, and an
    event goes only to sockets of the tenant it was published in. An event with no
    tenant in context reaches no one: broadcasting it would show it to everyone.
    """

    def __init__(self) -> None:
        self._clients: dict[WebSocket, UUID | None] = {}

    async def connect(self, ws: WebSocket, tenant_id: UUID | None) -> None:
        await ws.accept()
        self._clients[ws] = tenant_id

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    async def publish(self, subject: str, payload: dict) -> None:
        tenant = current_tenant()
        if tenant is None:
            log.warning("event %s published with no tenant; not delivered", subject)
            return
        message = {"subject": subject, "data": payload}
        for ws, owner in list(self._clients.items()):
            if owner != tenant:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)
