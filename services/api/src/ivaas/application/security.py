"""Site security use cases.

The pipeline proposes incidents; this layer decides whether they stand. It refuses a
kind the zone does not watch for, a report from an ignored area, an unknown-face
report while face recognition is off, and an "unbadged" report the door's log
accounts for. Evidence is stored before the incident, so an incident never points
at a snapshot that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from ivaas.domain.models import NotFoundError
from ivaas.domain.security import (
    RULE_OF,
    BadgeEvent,
    EnrolledPerson,
    Incident,
    IncidentKind,
    SecurityError,
    Zone,
    admitted,
)
from ivaas.ports.repositories import CameraReader, Clock, EventPublisher
from ivaas.ports.security import BadgeLog, FaceEncoder, IncidentStore, PeopleStore, ZoneStore

SUBJECT_INCIDENT_CREATED = "ivaas.incident.created"
SUBJECT_INCIDENT_UPDATED = "ivaas.incident.updated"

#: a repeat of the same kind in the same zone within this long is the same incident
DUPLICATE_WINDOW = timedelta(seconds=60)
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def incident_payload(i: Incident) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "bay_id": str(i.bay_id),
        "camera_id": str(i.camera_id),
        "kind": i.kind.value,
        "zone_name": i.zone_name,
        "status": i.status.value,
        "confidence": i.confidence,
        "detected_at": i.detected_at.isoformat(),
    }


@dataclass
class SaveZone:
    zones: ZoneStore
    cameras: CameraReader

    async def __call__(self, zone: Zone) -> Zone:
        if await self.cameras.get(zone.camera_id) is None:
            raise NotFoundError(f"camera {zone.camera_id} not found")
        existing = await self.zones.get(zone.id)
        if existing is not None and existing.camera_id != zone.camera_id:
            raise SecurityError("a zone cannot move to another camera; draw a new one")
        await self.zones.save(zone)
        return zone


@dataclass
class ReportIncident:
    zones: ZoneStore
    incidents: IncidentStore
    badges: BadgeLog
    objects: Any
    events: EventPublisher
    face_recognition_on: bool
    badge_grace: timedelta

    async def __call__(
        self,
        *,
        bay_id: UUID,
        camera_id: UUID,
        zone_id: UUID,
        kind: IncidentKind,
        confidence: float,
        detected_at: datetime,
        snapshot: bytes | None = None,
        detail: dict | None = None,
    ) -> Incident | None:
        """Returns the incident, or None when the report does not stand."""
        zone = await self.zones.get(zone_id)
        if zone is None or zone.camera_id != camera_id:
            raise NotFoundError(f"zone {zone_id} not found on this camera")
        if zone.exclude or RULE_OF[kind] not in zone.rules:
            raise SecurityError(f"zone '{zone.name}' does not watch for {kind.value}")
        if kind is IncidentKind.UNKNOWN_FACE and not self.face_recognition_on:
            raise SecurityError("face recognition is switched off")
        if kind is IncidentKind.UNBADGED:
            assert zone.badge_door  # guaranteed by Zone for badge zones
            recent = await self.badges.between(detected_at - self.badge_grace, detected_at)
            if admitted(recent, zone.badge_door, detected_at, self.badge_grace):
                return None  # the door's log accounts for someone entering
        if snapshot is not None and len(snapshot) > MAX_SNAPSHOT_BYTES:
            raise SecurityError("snapshot is larger than 2 MB")

        recent_same = await self.incidents.list(
            bay_id=bay_id, since=detected_at - DUPLICATE_WINDOW, kind=kind, limit=20
        )
        for prior in recent_same:
            if prior.zone_id == zone.id:
                return prior  # still the same event: one incident, not a flood

        incident = Incident(
            bay_id=bay_id,
            camera_id=camera_id,
            kind=kind,
            detected_at=detected_at,
            confidence=round(confidence, 4),
            zone_id=zone.id,
            zone_name=zone.name,
            detail=dict(detail or {}),
        )
        if snapshot:
            key = f"incidents/{incident.id}.jpg"
            await self.objects.put(key, snapshot, "image/jpeg")
            incident.snapshot_key = key
        await self.incidents.save(incident)
        await self.events.publish(SUBJECT_INCIDENT_CREATED, incident_payload(incident))
        return incident


@dataclass
class UpdateIncident:
    incidents: IncidentStore
    events: EventPublisher
    clock: Clock

    async def acknowledge(self, incident_id: UUID, by: str) -> Incident:
        incident = await self._get(incident_id)
        incident.acknowledge(by, self.clock.now())
        return await self._saved(incident)

    async def resolve(self, incident_id: UUID, by: str, note: str) -> Incident:
        incident = await self._get(incident_id)
        incident.resolve(by, self.clock.now(), note)
        return await self._saved(incident)

    async def _get(self, incident_id: UUID) -> Incident:
        incident = await self.incidents.get(incident_id)
        if incident is None:
            raise NotFoundError(f"incident {incident_id} not found")
        return incident

    async def _saved(self, incident: Incident) -> Incident:
        await self.incidents.save(incident)
        await self.events.publish(SUBJECT_INCIDENT_UPDATED, incident_payload(incident))
        return incident


@dataclass
class RecordBadge:
    badges: BadgeLog

    async def __call__(self, event: BadgeEvent) -> BadgeEvent:
        if not event.badge_id.strip() or not event.door.strip():
            raise SecurityError("a badge event needs a badge id and a door")
        await self.badges.add(event)
        return event


@dataclass
class EnrolPerson:
    people: PeopleStore
    encoder: FaceEncoder | None
    clock: Clock
    face_recognition_on: bool

    async def __call__(
        self, *, name: str, employee_ref: str, consent_reference: str, photo: bytes, by: str
    ) -> EnrolledPerson:
        if not self.face_recognition_on:
            raise SecurityError(
                "face recognition is switched off; an admin records its legal basis and "
                "switches it on under Settings first"
            )
        if self.encoder is None:
            raise SecurityError("face models are not installed on this server")
        try:
            embedding = self.encoder.embed(photo)  # the photo goes no further than this line
        except ValueError as exc:
            raise SecurityError(str(exc)) from exc
        person = EnrolledPerson(
            name=name.strip(),
            employee_ref=employee_ref.strip(),
            consent_reference=consent_reference.strip(),
            enrolled_by=by,
            enrolled_at=self.clock.now(),
            embedding=embedding,
        )
        await self.people.save(person)
        return person
