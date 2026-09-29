"""Site security storage: Postgres, and in-memory twins with the same behaviour.

Face embeddings are sealed with the platform's secrets key before they reach the
database, the same way camera credentials are: a copy of the table on its own
recognises nobody.
"""

from __future__ import annotations

import base64
import struct
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, String, Text, Uuid, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.security import (
    BadgeEvent,
    EnrolledPerson,
    Incident,
    IncidentKind,
    IncidentStatus,
    Window,
    Zone,
    ZoneRule,
)

# --- rows ------------------------------------------------------------------------


class ZoneRow(Base):
    __tablename__ = "security_zones"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    camera_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    name: Mapped[str] = mapped_column(String(120))
    polygon: Mapped[Any] = mapped_column(JSONB)
    rules: Mapped[Any] = mapped_column(JSONB)
    schedule: Mapped[Any] = mapped_column(JSONB)
    min_dwell_s: Mapped[float] = mapped_column(Float)
    exclude: Mapped[bool] = mapped_column(Boolean)
    badge_door: Mapped[str | None] = mapped_column(String(120), nullable=True)


class IncidentRow(Base):
    __tablename__ = "security_incidents"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    bay_id: Mapped[UUID] = mapped_column(Uuid)
    camera_id: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(32))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float)
    zone_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    zone_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    snapshot_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    detail: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class BadgeRow(Base):
    __tablename__ = "badge_events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    badge_id: Mapped[str] = mapped_column(String(120))
    door: Mapped[str] = mapped_column(String(120))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted: Mapped[bool] = mapped_column(Boolean)
    holder: Mapped[str | None] = mapped_column(String(160), nullable=True)


class PersonRow(Base):
    __tablename__ = "enrolled_people"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    employee_ref: Mapped[str] = mapped_column(String(80))
    consent_reference: Mapped[str] = mapped_column(String(300))
    enrolled_by: Mapped[str] = mapped_column(String(120))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[str] = mapped_column(Text)  # sealed


# --- mapping ---------------------------------------------------------------------


def zone_to_dict(z: Zone) -> dict[str, Any]:
    return {
        "polygon": [list(p) for p in z.polygon],
        "rules": sorted(r.value for r in z.rules),
        "schedule": [{"days": list(w.days), "start": w.start, "end": w.end} for w in z.schedule],
    }


def zone_of(r: ZoneRow) -> Zone:
    return Zone(
        id=r.id,
        camera_id=r.camera_id,
        name=r.name,
        polygon=tuple((float(x), float(y)) for x, y in r.polygon),
        rules=frozenset(ZoneRule(v) for v in r.rules),
        schedule=tuple(Window(tuple(w["days"]), w["start"], w["end"]) for w in r.schedule),
        min_dwell_s=r.min_dwell_s,
        exclude=r.exclude,
        badge_door=r.badge_door,
    )


def _incident_values(i: Incident) -> dict[str, Any]:
    return {
        "id": i.id,
        "bay_id": i.bay_id,
        "camera_id": i.camera_id,
        "kind": i.kind.value,
        "detected_at": i.detected_at,
        "confidence": i.confidence,
        "zone_id": i.zone_id,
        "zone_name": i.zone_name,
        "snapshot_key": i.snapshot_key,
        "detail": i.detail,
        "status": i.status.value,
        "acknowledged_by": i.acknowledged_by,
        "acknowledged_at": i.acknowledged_at,
        "resolved_by": i.resolved_by,
        "resolved_at": i.resolved_at,
        "resolution_note": i.resolution_note,
    }


def incident_of(r: IncidentRow) -> Incident:
    return Incident(
        id=r.id,
        bay_id=r.bay_id,
        camera_id=r.camera_id,
        kind=IncidentKind(r.kind),
        detected_at=r.detected_at,
        confidence=r.confidence,
        zone_id=r.zone_id,
        zone_name=r.zone_name,
        snapshot_key=r.snapshot_key,
        detail=dict(r.detail or {}),
        status=IncidentStatus(r.status),
        acknowledged_by=r.acknowledged_by,
        acknowledged_at=r.acknowledged_at,
        resolved_by=r.resolved_by,
        resolved_at=r.resolved_at,
        resolution_note=r.resolution_note,
    )


def pack(embedding: tuple[float, ...]) -> str:
    return base64.b64encode(struct.pack(f"<{len(embedding)}f", *embedding)).decode()


def unpack(text: str) -> tuple[float, ...]:
    raw = base64.b64decode(text)
    return struct.unpack(f"<{len(raw) // 4}f", raw)


# --- postgres --------------------------------------------------------------------


class PostgresZoneStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def get(self, zone_id: UUID) -> Zone | None:
        async with self._sm() as db:
            row = await db.get(ZoneRow, zone_id)
        return zone_of(row) if row else None

    async def for_cameras(self, camera_ids: list[UUID]) -> list[Zone]:
        if not camera_ids:
            return []
        async with self._sm() as db:
            rows = (
                await db.scalars(
                    select(ZoneRow).where(ZoneRow.camera_id.in_(camera_ids)).order_by(ZoneRow.name)
                )
            ).all()
        return [zone_of(r) for r in rows]

    async def save(self, zone: Zone) -> None:
        d = zone_to_dict(zone)
        async with self._sm.begin() as db:
            await db.merge(
                ZoneRow(
                    id=zone.id,
                    camera_id=zone.camera_id,
                    name=zone.name,
                    polygon=d["polygon"],
                    rules=d["rules"],
                    schedule=d["schedule"],
                    min_dwell_s=zone.min_dwell_s,
                    exclude=zone.exclude,
                    badge_door=zone.badge_door,
                )
            )

    async def delete(self, zone_id: UUID) -> None:
        async with self._sm.begin() as db:
            await db.execute(delete(ZoneRow).where(ZoneRow.id == zone_id))


class PostgresIncidentStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def get(self, incident_id: UUID) -> Incident | None:
        async with self._sm() as db:
            row = await db.get(IncidentRow, incident_id)
        return incident_of(row) if row else None

    async def save(self, incident: Incident) -> None:
        async with self._sm.begin() as db:
            await db.merge(IncidentRow(**_incident_values(incident)))

    async def list(
        self,
        *,
        bay_id: UUID | None = None,
        since: datetime | None = None,
        status: IncidentStatus | None = None,
        kind: IncidentKind | None = None,
        limit: int = 200,
    ) -> list[Incident]:
        q = select(IncidentRow)
        if bay_id is not None:
            q = q.where(IncidentRow.bay_id == bay_id)
        if since is not None:
            q = q.where(IncidentRow.detected_at >= since)
        if status is not None:
            q = q.where(IncidentRow.status == status.value)
        if kind is not None:
            q = q.where(IncidentRow.kind == kind.value)
        q = q.order_by(IncidentRow.detected_at.desc()).limit(limit)
        async with self._sm() as db:
            rows = (await db.scalars(q)).all()
        return [incident_of(r) for r in rows]


class PostgresBadgeLog:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def add(self, event: BadgeEvent) -> None:
        async with self._sm.begin() as db:
            db.add(
                BadgeRow(
                    id=event.id,
                    badge_id=event.badge_id,
                    door=event.door,
                    at=event.at,
                    granted=event.granted,
                    holder=event.holder,
                )
            )

    async def between(self, start: datetime, end: datetime) -> list[BadgeEvent]:
        async with self._sm() as db:
            rows = (
                await db.scalars(
                    select(BadgeRow)
                    .where(BadgeRow.at >= start, BadgeRow.at <= end)
                    .order_by(BadgeRow.at.desc())
                )
            ).all()
        return [BadgeEvent(r.badge_id, r.door, r.at, r.granted, r.holder, r.id) for r in rows]


class PostgresPeopleStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession], box: Any) -> None:
        self._sm = sm
        self._box = box

    async def list(self) -> list[EnrolledPerson]:
        async with self._sm() as db:
            rows = (await db.scalars(select(PersonRow).order_by(PersonRow.name))).all()
        return [
            EnrolledPerson(
                id=r.id,
                name=r.name,
                employee_ref=r.employee_ref,
                consent_reference=r.consent_reference,
                enrolled_by=r.enrolled_by,
                enrolled_at=r.enrolled_at,
                embedding=unpack(self._box.open(r.embedding)),
            )
            for r in rows
        ]

    async def save(self, person: EnrolledPerson) -> None:
        async with self._sm.begin() as db:
            await db.merge(
                PersonRow(
                    id=person.id,
                    name=person.name,
                    employee_ref=person.employee_ref,
                    consent_reference=person.consent_reference,
                    enrolled_by=person.enrolled_by,
                    enrolled_at=person.enrolled_at,
                    embedding=self._box.seal(pack(person.embedding)),
                )
            )

    async def delete(self, person_id: UUID) -> EnrolledPerson | None:
        gone = next((p for p in await self.list() if p.id == person_id), None)
        if gone:
            async with self._sm.begin() as db:
                await db.execute(delete(PersonRow).where(PersonRow.id == person_id))
        return gone


# --- in memory -------------------------------------------------------------------


class InMemoryZoneStore:
    def __init__(self) -> None:
        self._zones: dict[UUID, Zone] = {}

    async def get(self, zone_id: UUID) -> Zone | None:
        return self._zones.get(zone_id)

    async def for_cameras(self, camera_ids: list[UUID]) -> list[Zone]:
        wanted = set(camera_ids)
        return sorted(
            (z for z in self._zones.values() if z.camera_id in wanted), key=lambda z: z.name
        )

    async def save(self, zone: Zone) -> None:
        self._zones[zone.id] = zone

    async def delete(self, zone_id: UUID) -> None:
        self._zones.pop(zone_id, None)


class InMemoryIncidentStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, Incident] = {}

    async def get(self, incident_id: UUID) -> Incident | None:
        return self._rows.get(incident_id)

    async def save(self, incident: Incident) -> None:
        self._rows[incident.id] = incident

    async def list(
        self,
        *,
        bay_id: UUID | None = None,
        since: datetime | None = None,
        status: IncidentStatus | None = None,
        kind: IncidentKind | None = None,
        limit: int = 200,
    ) -> list[Incident]:
        rows = [
            i
            for i in self._rows.values()
            if (bay_id is None or i.bay_id == bay_id)
            and (since is None or i.detected_at >= since)
            and (status is None or i.status is status)
            and (kind is None or i.kind is kind)
        ]
        return sorted(rows, key=lambda i: i.detected_at, reverse=True)[:limit]


class InMemoryBadgeLog:
    def __init__(self) -> None:
        self._events: list[BadgeEvent] = []

    async def add(self, event: BadgeEvent) -> None:
        self._events.append(event)

    async def between(self, start: datetime, end: datetime) -> list[BadgeEvent]:
        rows = [e for e in self._events if start <= e.at <= end]
        return sorted(rows, key=lambda e: e.at, reverse=True)


class InMemoryPeopleStore:
    def __init__(self) -> None:
        self._people: dict[UUID, EnrolledPerson] = {}

    async def list(self) -> list[EnrolledPerson]:
        return sorted(self._people.values(), key=lambda p: p.name)

    async def save(self, person: EnrolledPerson) -> None:
        self._people[person.id] = person

    async def delete(self, person_id: UUID) -> EnrolledPerson | None:
        return self._people.pop(person_id, None)
