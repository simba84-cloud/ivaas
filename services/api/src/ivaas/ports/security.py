"""Outbound ports for site security: zones, incidents, the badge log, enrolled people."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ivaas.domain.security import (
    BadgeEvent,
    EnrolledPerson,
    Incident,
    IncidentKind,
    IncidentStatus,
    Zone,
)


class ZoneStore(Protocol):
    async def get(self, zone_id: UUID) -> Zone | None: ...

    async def for_cameras(self, camera_ids: list[UUID]) -> list[Zone]: ...

    async def save(self, zone: Zone) -> None: ...

    async def delete(self, zone_id: UUID) -> None: ...


class IncidentStore(Protocol):
    async def get(self, incident_id: UUID) -> Incident | None: ...

    async def save(self, incident: Incident) -> None: ...

    async def list(
        self,
        *,
        bay_id: UUID | None = None,
        since: datetime | None = None,
        status: IncidentStatus | None = None,
        kind: IncidentKind | None = None,
        limit: int = 200,
    ) -> list[Incident]: ...


class BadgeLog(Protocol):
    async def add(self, event: BadgeEvent) -> None: ...

    async def between(self, start: datetime, end: datetime) -> list[BadgeEvent]: ...


class PeopleStore(Protocol):
    async def list(self) -> list[EnrolledPerson]: ...

    async def save(self, person: EnrolledPerson) -> None: ...

    async def delete(self, person_id: UUID) -> EnrolledPerson | None: ...


class FaceEncoder(Protocol):
    """Turns a photo into a face embedding. Raises ValueError when the photo does not
    show exactly one clear face, so enrolment never stores a guess."""

    def embed(self, image_bytes: bytes) -> tuple[float, ...]: ...
