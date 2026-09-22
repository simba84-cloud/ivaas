"""Event fan-out. New sinks (webhooks, ERP integration, ...) are added by
appending another EventPublisher here, not by editing the use cases.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from fastapi import WebSocket

from ivaas.ports.repositories import EventPublisher

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
    """EventPublisher that pushes every event to connected portal clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def publish(self, subject: str, payload: dict) -> None:
        message = {"subject": subject, "data": payload}
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)
