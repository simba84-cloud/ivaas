"""Site security: zones drawn on a camera's view, and incidents raised inside them.

Unlike an alert (a condition worked out from current state), an incident is an event:
something was seen at a time, with evidence. It is stored, acknowledged by a person
and resolved with a note. Resolving never deletes it.

What each incident kind needs before it can be raised at all:

    intrusion     a person detector (pretrained; works out of the box)
    no_ppe        a uniform/PPE classifier trained on this site's staff
    unknown_face  face recognition switched on, with its legal basis recorded
    unbadged      the access-control system posting badge events
    fire, smoke   a fire/smoke detector trained with this site's ovens in view

Camera fire detection supplements the certified fire alarm; it never replaces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from uuid import UUID, uuid4


class ZoneRule(StrEnum):
    INTRUSION = "intrusion"  # anyone here while the zone is armed
    PPE = "ppe"  # staff here must be in uniform / PPE
    FACE = "face"  # only enrolled people may be here
    BADGE = "badge"  # anyone here must have badged in
    FIRE = "fire"  # watch for flame and smoke


class IncidentKind(StrEnum):
    INTRUSION = "intrusion"
    NO_PPE = "no_ppe"
    UNKNOWN_FACE = "unknown_face"
    UNBADGED = "unbadged"
    FIRE = "fire"
    SMOKE = "smoke"


#: which rule a kind is raised under; a pipeline cannot report a kind its zone does not watch for
RULE_OF: dict[IncidentKind, ZoneRule] = {
    IncidentKind.INTRUSION: ZoneRule.INTRUSION,
    IncidentKind.NO_PPE: ZoneRule.PPE,
    IncidentKind.UNKNOWN_FACE: ZoneRule.FACE,
    IncidentKind.UNBADGED: ZoneRule.BADGE,
    IncidentKind.FIRE: ZoneRule.FIRE,
    IncidentKind.SMOKE: ZoneRule.FIRE,
}


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class SecurityError(ValueError):
    """A request that breaks a security rule; reported to the caller as a 4xx."""


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Window:
    """Armed from `start` to `end` on `days` (0 = Monday). An end before the start runs
    past midnight into the next day, as a night shift does."""

    days: tuple[int, ...]
    start: str  # "HH:MM"
    end: str

    def __post_init__(self) -> None:
        if not self.days or any(d not in range(7) for d in self.days):
            raise SecurityError("a schedule window needs days between 0 (Mon) and 6 (Sun)")
        if not (_HHMM.match(self.start) and _HHMM.match(self.end)):
            raise SecurityError("schedule times must be HH:MM, 24-hour")

    def contains(self, local: datetime) -> bool:
        start = time.fromisoformat(self.start)
        end = time.fromisoformat(self.end)
        now = local.time()
        if start <= end:
            return local.weekday() in self.days and start <= now < end
        # overnight: the part before midnight belongs to today, the rest to yesterday
        if now >= start:
            return local.weekday() in self.days
        return now < end and (local - timedelta(days=1)).weekday() in self.days


@dataclass(frozen=True)
class Zone:
    camera_id: UUID
    name: str
    #: normalised to the frame, 0..1, at least three points
    polygon: tuple[tuple[float, float], ...]
    rules: frozenset[ZoneRule] = frozenset()
    #: empty means always armed
    schedule: tuple[Window, ...] = ()
    #: seconds a person must stay before it counts: a passer-by is not an intrusion
    min_dwell_s: float = 3.0
    #: an area to ignore (the ovens, for fire detection)
    exclude: bool = False
    #: the door whose badge readers admit people to this zone
    badge_door: str | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SecurityError("a zone needs a name")
        if len(self.polygon) < 3:
            raise SecurityError("a zone needs at least three points")
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in self.polygon):
            raise SecurityError("zone points are fractions of the frame, between 0 and 1")
        if not 0 <= self.min_dwell_s <= 600:
            raise SecurityError("dwell time must be between 0 and 600 seconds")
        if ZoneRule.BADGE in self.rules and not (self.badge_door or "").strip():
            raise SecurityError("a badge-required zone must name the door its readers are on")
        if self.exclude and self.rules:
            raise SecurityError("an ignored area cannot also raise incidents")

    def armed(self, local: datetime) -> bool:
        return not self.schedule or any(w.contains(local) for w in self.schedule)

    def contains(self, x: float, y: float) -> bool:
        """Point in polygon (ray casting), coordinates normalised like the polygon."""
        inside = False
        pts = self.polygon
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside


@dataclass
class Incident:
    bay_id: UUID
    camera_id: UUID
    kind: IncidentKind
    detected_at: datetime
    confidence: float
    zone_id: UUID | None = None
    zone_name: str | None = None
    snapshot_key: str | None = None
    detail: dict = field(default_factory=dict)
    status: IncidentStatus = IncidentStatus.OPEN
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    id: UUID = field(default_factory=uuid4)

    def acknowledge(self, by: str, at: datetime) -> None:
        if self.status is not IncidentStatus.OPEN:
            raise SecurityError(f"incident is already {self.status.value}")
        self.status = IncidentStatus.ACKNOWLEDGED
        self.acknowledged_by, self.acknowledged_at = by, at

    def resolve(self, by: str, at: datetime, note: str) -> None:
        """Closes it with what was found. A resolution without a note says nothing to
        whoever reads it later, so one is required."""
        if self.status is IncidentStatus.RESOLVED:
            raise SecurityError("incident is already resolved")
        if not note.strip():
            raise SecurityError("say what was found when resolving an incident")
        if self.status is IncidentStatus.OPEN:
            self.acknowledged_by, self.acknowledged_at = by, at
        self.status = IncidentStatus.RESOLVED
        self.resolved_by, self.resolved_at = by, at
        self.resolution_note = note.strip()[:1000]


@dataclass(frozen=True)
class BadgeEvent:
    """One swipe at a door, as the access-control system reports it."""

    badge_id: str
    door: str
    at: datetime
    granted: bool
    holder: str | None = None
    id: UUID = field(default_factory=uuid4)


def admitted(events: list[BadgeEvent], door: str, seen_at: datetime, grace: timedelta) -> bool:
    """Did anyone badge in at `door` in the `grace` before someone was seen inside?

    Only granted swipes count. This cannot tell *who* was seen; it says whether the
    door's log accounts for somebody entering. One swipe admitting two people shows as
    admitted: tailgating needs a count of swipes against people, which is later work.
    """
    door = door.strip().lower()
    return any(
        e.granted and e.door.strip().lower() == door and seen_at - grace <= e.at <= seen_at
        for e in events
    )


@dataclass(frozen=True)
class EnrolledPerson:
    """Someone face recognition may recognise. Only the face embedding is kept, sealed;
    the photo it came from is discarded at enrolment."""

    name: str
    employee_ref: str
    #: where the lawful basis for processing this person's face is recorded
    consent_reference: str
    enrolled_by: str
    enrolled_at: datetime
    embedding: tuple[float, ...]
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.employee_ref.strip():
            raise SecurityError("an enrolled person needs a name and an employee reference")
        if not self.consent_reference.strip():
            raise SecurityError("record where this person's consent or lawful basis is kept")
        if len(self.embedding) < 64:
            raise SecurityError("face embedding is missing or truncated")
