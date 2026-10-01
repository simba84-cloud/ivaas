"""Webhooks: queued when an event happens, sent (and sent again) until they land (T6.4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from ivaas.domain.models import NotFoundError
from ivaas.domain.webhooks import (
    EVENTS,
    TEST_EVENT,
    Delivery,
    WebhookEndpoint,
    body,
    check_url,
    new_secret,
    sign,
)
from ivaas.ports.webhooks import WebhookSender, WebhookStore
from ivaas.tenancy import current_tenant

_EVENT_OF = {subject: event for event, subject in EVENTS.items()}


@dataclass
class QueueWebhooks:
    """An event sink: each event a tenant's endpoints subscribe to becomes one
    delivery per endpoint, all with the same event id. Sending happens elsewhere, so a
    slow receiver never slows the load that caused the event."""

    store: WebhookStore
    clock: Any

    async def publish(self, subject: str, payload: dict) -> None:
        event = _EVENT_OF.get(subject)
        if event is None or current_tenant() is None:
            return
        endpoints = [e for e in await self.store.endpoints() if e.wants(event)]
        if not endpoints:
            return
        event_id, now = uuid4(), self.clock.now()
        data = body(event, event_id, now, payload)
        for endpoint in endpoints:
            await self.store.save_delivery(Delivery(endpoint.id, event_id, event, data, now))


def _wire(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), default=str).encode()


@dataclass
class DeliverWebhooks:
    store: WebhookStore
    sender: WebhookSender
    clock: Any

    async def __call__(self) -> list[Delivery]:
        sent = []
        for delivery in await self.store.claim_due(self.clock.now()):
            endpoint = await self.store.endpoint(delivery.endpoint_id)
            if endpoint is None:
                continue  # removed with its endpoint
            raw = _wire(delivery.payload)
            stamp = int(self.clock.now().timestamp())
            msg_id = str(delivery.event_id)
            headers = {
                "content-type": "application/json",
                "user-agent": "IVaaS-Webhooks/1",
                "webhook-id": msg_id,
                "webhook-timestamp": str(stamp),
                "webhook-signature": sign(endpoint.secret, msg_id, stamp, raw),
            }
            result = await self.sender.send(endpoint.url, headers, raw)
            if result.ok:
                delivery.succeeded(self.clock.now(), result.status_code or 0)
            else:
                code = result.status_code
                delivery.failed(self.clock.now(), code, result.error or f"answered {code}")
            await self.store.save_delivery(delivery)
            sent.append(delivery)
        return sent


@dataclass
class ManageWebhooks:
    store: WebhookStore
    clock: Any
    allow_private: bool = False

    async def create(
        self, url: str, events: list[str], description: str, by: str
    ) -> tuple[WebhookEndpoint, str]:
        """The secret is returned here, once; afterwards it is only used to sign."""
        secret = new_secret()
        endpoint = WebhookEndpoint(
            url=check_url(url, allow_private=self.allow_private),
            events=tuple(events),
            secret=secret,
            created_by=by,
            created_at=self.clock.now(),
            description=description.strip(),
        )
        await self.store.save_endpoint(endpoint)
        return endpoint, secret

    async def _endpoint(self, endpoint_id: UUID) -> WebhookEndpoint:
        endpoint = await self.store.endpoint(endpoint_id)
        if endpoint is None:
            raise NotFoundError(f"webhook {endpoint_id} not found")
        return endpoint

    async def send_test(self, endpoint_id: UUID, by: str) -> Delivery:
        endpoint = await self._endpoint(endpoint_id)
        event_id, now = uuid4(), self.clock.now()
        data = body(TEST_EVENT, event_id, now, {"sent_by": by, "endpoint": str(endpoint.id)})
        delivery = Delivery(endpoint.id, event_id, TEST_EVENT, data, now)
        await self.store.save_delivery(delivery)
        return delivery

    async def replay(self, delivery_id: UUID) -> Delivery:
        """The same event, sent again from the start of the schedule; the receiver
        sees the same webhook-id, so it can tell it already has it."""
        original = await self.store.delivery(delivery_id)
        if original is None:
            raise NotFoundError(f"delivery {delivery_id} not found")
        again = original.replay(self.clock.now())
        await self.store.save_delivery(again)
        return again
