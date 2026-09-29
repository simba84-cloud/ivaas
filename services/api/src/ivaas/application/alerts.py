"""Acknowledging an alert: record who saw it, and tell every open screen."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ivaas.domain.alerts import AlertAcknowledgement, check_key
from ivaas.ports.alerts import AcknowledgementStore
from ivaas.ports.repositories import Clock, EventPublisher

SUBJECT_ALERT_ACKNOWLEDGED = "ivaas.alert.acknowledged"

#: how far back the portal needs acknowledgements; older keys belong to
#: conditions long since cleared
ACK_WINDOW = timedelta(days=30)


def ack_payload(a: AlertAcknowledgement) -> dict:
    return {
        "key": a.key,
        "acknowledged_by": a.acknowledged_by,
        "acknowledged_at": a.acknowledged_at.isoformat(),
        "note": a.note,
    }


@dataclass
class AcknowledgeAlert:
    store: AcknowledgementStore
    events: EventPublisher
    clock: Clock

    async def __call__(
        self, key: str, by: str, note: str | None = None
    ) -> tuple[AlertAcknowledgement, bool]:
        """Returns the acknowledgement kept, and whether this call created it."""
        key = check_key(key)
        note = (note or "").strip()[:500] or None
        attempt = AlertAcknowledgement(
            key=key, acknowledged_by=by, acknowledged_at=self.clock.now(), note=note
        )
        kept = await self.store.add(attempt)
        # the store keeps the first; ours won only if what came back is ours
        created = (kept.acknowledged_by, kept.acknowledged_at) == (by, attempt.acknowledged_at)
        if created:
            await self.events.publish(SUBJECT_ALERT_ACKNOWLEDGED, ack_payload(kept))
        return kept, created


@dataclass
class ListAcknowledgements:
    store: AcknowledgementStore
    clock: Clock

    async def __call__(self) -> list[AlertAcknowledgement]:
        return await self.store.list_since(self.clock.now() - ACK_WINDOW)
