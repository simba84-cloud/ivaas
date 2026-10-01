"""Webhooks over HTTP: a tenant's endpoints, their deliveries, a test and a replay (T6.4).

Managed with `apikey.manage`: an integration is a way out of the platform for the
tenant's data, like an API key. The signing secret is returned when the endpoint is
created and never again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ivaas.adapters.http.auth import current_principal, require
from ivaas.domain.audit import AuditAction
from ivaas.domain.rbac import Permission as P
from ivaas.domain.webhooks import EVENTS, Delivery, WebhookEndpoint, WebhookError
from ivaas.ports.auth import Principal

Audit = Callable[..., Awaitable[None]]


class WebhookIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    events: list[str] = Field(min_length=1, max_length=len(EVENTS))
    description: str = Field(default="", max_length=200)


class WebhookOut(BaseModel):
    id: UUID
    url: str
    events: list[str]
    description: str
    created_by: str
    created_at: datetime

    @classmethod
    def of(cls, e: WebhookEndpoint) -> WebhookOut:
        return cls(
            id=e.id,
            url=e.url,
            events=list(e.events),
            description=e.description,
            created_by=e.created_by,
            created_at=e.created_at,
        )


class WebhookCreatedOut(WebhookOut):
    #: shown once: the receiver needs it to check signatures; it is not shown again
    secret: str


class DeliveryOut(BaseModel):
    id: UUID
    event_id: UUID
    event: str
    status: str
    attempts: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    replay_of: UUID | None
    created_at: datetime

    @classmethod
    def of(cls, d: Delivery) -> DeliveryOut:
        return cls(
            id=d.id,
            event_id=d.event_id,
            event=d.event,
            status=d.status.value,
            attempts=d.attempts,
            next_attempt_at=d.next_attempt_at,
            last_status_code=d.last_status_code,
            last_error=d.last_error,
            delivered_at=d.delivered_at,
            replay_of=d.replay_of,
            created_at=d.created_at,
        )


class WebhookEventsOut(BaseModel):
    events: list[str]


def add_webhook_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    manage = [Depends(require(P.APIKEY_MANAGE))]

    @app.get("/api/v1/webhooks/events", response_model=WebhookEventsOut, dependencies=manage)
    async def webhook_events() -> WebhookEventsOut:
        """What an endpoint can subscribe to."""
        return WebhookEventsOut(events=sorted(EVENTS))

    @app.get("/api/v1/webhooks", response_model=list[WebhookOut], dependencies=manage)
    async def list_webhooks(c: Any = Depends(get_container)) -> list[WebhookOut]:
        return [WebhookOut.of(e) for e in await c.webhooks.endpoints()]

    @app.post(
        "/api/v1/webhooks",
        response_model=WebhookCreatedOut,
        status_code=201,
        dependencies=manage,
    )
    async def create_webhook(
        body: WebhookIn,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> WebhookCreatedOut:
        try:
            endpoint, secret = await c.manage_webhooks().create(
                body.url, body.events, body.description, principal.name
            )
        except WebhookError as exc:
            raise HTTPException(422, str(exc)) from exc
        await audit(
            c, principal.name, AuditAction.WEBHOOK_CREATED, endpoint.url, events=body.events
        )
        return WebhookCreatedOut(**WebhookOut.of(endpoint).model_dump(), secret=secret)

    @app.delete("/api/v1/webhooks/{endpoint_id}", status_code=204, dependencies=manage)
    async def delete_webhook(
        endpoint_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> Response:
        endpoint = await c.webhooks.endpoint(endpoint_id)
        if endpoint is None or not await c.webhooks.delete_endpoint(endpoint_id):
            raise HTTPException(404, "webhook not found")
        await audit(c, principal.name, AuditAction.WEBHOOK_DELETED, endpoint.url)
        return Response(status_code=204)

    @app.get(
        "/api/v1/webhooks/{endpoint_id}/deliveries",
        response_model=list[DeliveryOut],
        dependencies=manage,
    )
    async def list_deliveries(
        endpoint_id: UUID, limit: int = 50, c: Any = Depends(get_container)
    ) -> list[DeliveryOut]:
        if await c.webhooks.endpoint(endpoint_id) is None:
            raise HTTPException(404, "webhook not found")
        rows = await c.webhooks.deliveries(endpoint_id, limit=max(1, min(limit, 200)))
        return [DeliveryOut.of(d) for d in rows]

    @app.post(
        "/api/v1/webhooks/{endpoint_id}/test",
        response_model=DeliveryOut,
        status_code=202,
        dependencies=manage,
    )
    async def test_webhook(
        endpoint_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> DeliveryOut:
        """Queue a `webhook.test` event, to check a receiver before a real one comes."""
        return DeliveryOut.of(await c.manage_webhooks().send_test(endpoint_id, principal.name))

    @app.post(
        "/api/v1/webhooks/deliveries/{delivery_id}/replay",
        response_model=DeliveryOut,
        status_code=202,
        dependencies=manage,
    )
    async def replay_delivery(
        delivery_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> DeliveryOut:
        again = await c.manage_webhooks().replay(delivery_id)
        await audit(
            c,
            principal.name,
            AuditAction.WEBHOOK_REPLAYED,
            again.event,
            delivery_id=str(delivery_id),
            event_id=str(again.event_id),
        )
        return DeliveryOut.of(again)
