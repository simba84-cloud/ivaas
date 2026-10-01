"""Webhooks: a tenant's systems told when a load closes or a manifest disagrees (T6.4).

Signed the Standard Webhooks way (an open specification), so a receiver can use any
library that speaks it, or twenty lines of its own:

    webhook-id:        the event's id; the same on every retry and replay
    webhook-timestamp: Unix seconds when this attempt was signed
    webhook-signature: "v1," + base64(HMAC-SHA256(key, f"{id}.{timestamp}.{body}"))

where `key` is the base64 after "whsec_" in the endpoint's secret. Delivery is at
least once: a receiver that has seen a webhook-id has seen that event.

A delivery that fails is tried again on a fixed backoff, then given up as failed. A
replay is a new delivery of the same event, so the first one's history stays.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

#: what a tenant can subscribe to, and the platform events they come from
EVENTS = {
    "session.closed": "ivaas.session.closed",
    "exception.raised": "ivaas.exception.raised",
}
#: sent by "send a test"; not subscribable, always delivered to the endpoint asked
TEST_EVENT = "webhook.test"

#: seconds to wait after each failed attempt; when they run out, the delivery fails
BACKOFF_S = (30, 120, 600, 1800, 7200, 21600, 43200)
MAX_ATTEMPTS = len(BACKOFF_S) + 1
#: a claimed delivery not finished by then is someone's crash: send it again
LEASE_S = 60
#: a receiver should refuse a signature older than this (replayed by an attacker)
TOLERANCE_S = 300


class WebhookError(ValueError):
    pass


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"  # every attempt in the schedule failed


def new_secret() -> str:
    return "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()


def _key(secret: str) -> bytes:
    return base64.b64decode(secret.removeprefix("whsec_"))


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def verify(secret: str, headers: dict[str, str], body: bytes, now: int) -> bool:
    """What a receiver does. Here for tests and as the reference for the docs."""
    msg_id, stamp = headers.get("webhook-id", ""), headers.get("webhook-timestamp", "")
    if not msg_id or not stamp.isdigit() or abs(now - int(stamp)) > TOLERANCE_S:
        return False
    expected = sign(secret, msg_id, int(stamp), body).split(",", 1)[1]
    offered = [s.split(",", 1)[1] for s in headers.get("webhook-signature", "").split() if "," in s]
    return any(hmac.compare_digest(expected, s) for s in offered)


def _private(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host.lower() in ("localhost", "localhost.localdomain") or host.endswith(
            (".localhost", ".local", ".internal")
        )
    return not ip.is_global


def check_url(url: str, *, allow_private: bool) -> str:
    """A tenant's URL is somewhere the platform will make requests to. Public HTTPS
    only, unless the deployment allows receivers on its own network (an ERP on the
    site LAN). Names are checked again when sent, after they resolve."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise WebhookError("the URL must be http(s)://host/...")
    if parts.username or parts.password:
        raise WebhookError("put credentials in your receiver, not in the URL")
    if not allow_private:
        if parts.scheme != "https":
            raise WebhookError("the URL must use https")
        if _private(parts.hostname):
            raise WebhookError("the URL must be a public address")
    return url.strip()


def address_allowed(ip: str, *, allow_private: bool) -> bool:
    """For the address a name resolved to, at the moment of sending."""
    return allow_private or ipaddress.ip_address(ip).is_global


@dataclass
class WebhookEndpoint:
    url: str
    events: tuple[str, ...]
    secret: str
    created_by: str
    created_at: datetime
    description: str = ""
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        unknown = set(self.events) - set(EVENTS)
        if unknown or not self.events:
            raise WebhookError(f"subscribe to one or more of {sorted(EVENTS)}")
        self.events = tuple(sorted(set(self.events)))

    def wants(self, event: str) -> bool:
        return event in self.events


@dataclass
class Delivery:
    endpoint_id: UUID
    event_id: UUID  # the webhook-id: the same for every attempt and replay of an event
    event: str
    payload: dict[str, Any]
    created_at: datetime
    next_attempt_at: datetime | None = None
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    last_status_code: int | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    replay_of: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.next_attempt_at is None and self.status is DeliveryStatus.PENDING:
            self.next_attempt_at = self.created_at

    def claim(self, now: datetime) -> None:
        """Taken for sending; if the sender dies, it is due again after the lease."""
        self.next_attempt_at = now + timedelta(seconds=LEASE_S)

    def succeeded(self, now: datetime, status_code: int) -> None:
        self.attempts += 1
        self.status, self.delivered_at = DeliveryStatus.DELIVERED, now
        self.last_status_code, self.last_error, self.next_attempt_at = status_code, None, None

    def failed(self, now: datetime, status_code: int | None, error: str) -> None:
        self.attempts += 1
        self.last_status_code, self.last_error = status_code, error[:300]
        if self.attempts >= MAX_ATTEMPTS:
            self.status, self.next_attempt_at = DeliveryStatus.FAILED, None
        else:
            self.next_attempt_at = now + timedelta(seconds=BACKOFF_S[self.attempts - 1])

    def replay(self, now: datetime) -> Delivery:
        return Delivery(
            endpoint_id=self.endpoint_id,
            event_id=self.event_id,
            event=self.event,
            payload=self.payload,
            created_at=now,
            replay_of=self.id,
        )


def body(event: str, event_id: UUID, at: datetime, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(event_id), "type": event, "timestamp": at.isoformat(), "data": data}
