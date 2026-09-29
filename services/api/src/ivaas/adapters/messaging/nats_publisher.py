from __future__ import annotations

import json

from nats.aio.client import Client as NATS

from ivaas.tenancy import require_tenant


def tenant_subject(subject: str) -> str:
    """`ivaas.session.opened` -> `ivaas.t.<tenant>.session.opened` (proposal §3.2).

    The tenant is part of the subject, so a consumer subscribes to one tenant's
    events and a broker ACL can confine it there. Events are never published
    without a tenant: one would belong to nobody.
    """
    head, _, rest = subject.partition(".")
    return f"{head}.t.{require_tenant()}.{rest}"


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
        await self._nc.jetstream().publish(tenant_subject(subject), json.dumps(payload).encode())
