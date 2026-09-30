"""The fleet register: the trucks a tenant loads (proposal M5, T5.1-T5.2).

A plate read is matched against the register, forgiving spacing and the characters
OCR confuses, so "A8C I234" at the gate is truck ABC 1234 in the books. A read
that matches no registered truck is kept as read and flagged; a load whose plate
could not be read at all is "unidentified" until an operator assigns it. Nothing
is guessed: a match below the threshold is not a match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from ivaas.domain.plates import canonical, normalize_plate, plate_similarity

#: Close enough to be the same plate after folding confusable characters; one wrong
#: character in a 7-character plate scores about 0.86.
MATCH_THRESHOLD = 0.85


class FleetError(ValueError):
    pass


class Identification(StrEnum):
    """How sure the platform is which truck a load was."""

    REGISTERED = "registered"  # matched a truck in the fleet register
    UNREGISTERED = "unregistered"  # a plate was read, but it is not in the register
    UNIDENTIFIED = "unidentified"  # no plate read at all
    UNCHECKED = "unchecked"  # a plate, but no register to check it against


@dataclass
class Vehicle:
    plate: str
    fleet_number: str = ""
    operator: str = ""
    notes: str = ""
    active: bool = True
    created_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if len(normalize_plate(self.plate)) < 2:
            raise FleetError("a plate needs at least two letters or digits")
        self.plate = " ".join(self.plate.upper().split())

    @property
    def key(self) -> str:
        """What makes two entries the same truck."""
        return canonical(self.plate)


@dataclass(frozen=True)
class Match:
    vehicle: Vehicle
    score: float
    exact: bool


def match_vehicle(read: str | None, fleet: list[Vehicle]) -> Match | None:
    """The registered truck this read is, or None if no truck is close enough.

    An exact match (after normalising) always wins. Otherwise the best score at or
    above the threshold wins, and a tie between two trucks is no match at all:
    picking one would be a guess.
    """
    if not read:
        return None
    active = [v for v in fleet if v.active]
    key = canonical(read)
    exact = [v for v in active if v.key == key]
    if exact:
        return Match(exact[0], 1.0, True)
    scored = sorted(((plate_similarity(read, v.plate), v) for v in active), key=lambda x: -x[0])
    if not scored or scored[0][0] < MATCH_THRESHOLD:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return Match(scored[0][1], round(scored[0][0], 3), False)


def identification(
    plate: str | None, vehicle_id: UUID | None, has_register: bool
) -> Identification:
    if vehicle_id is not None:
        return Identification.REGISTERED
    if not plate:
        return Identification.UNIDENTIFIED
    return Identification.UNREGISTERED if has_register else Identification.UNCHECKED
