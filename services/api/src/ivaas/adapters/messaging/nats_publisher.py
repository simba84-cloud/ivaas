from __future__ import annotations

import json

from nats.aio.client import Client as NATS


class NatsEventPublisher:
    """EventPublisher port backed by NATS JetStream."""

    STREAM = "IVAAS"

    def __init__(self, url: str) -> None:
        self._url = url
        self._nc: NATS | None = None

    async def connect(self) -> None:
        self._nc = NATS()
        await self._nc.connect(self._url, max_reconnect_attempts=-1)
        js = self._nc.jetstream()
        await js.add_stream(name=self.STREAM, subjects=["ivaas.>"])

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()

    async def publish(self, subject: str, payload: dict) -> None:
        if self._nc is None:
            raise RuntimeError("NatsEventPublisher.connect() was not called")
        await self._nc.jetstream().publish(subject, json.dumps(payload).encode())
