"""Dispatch manifests, exceptions and balances (proposal M5, T5.3-T5.4).

A manifest says what a truck was meant to carry: on this day, this truck, on this
route, loads (or returns) this many crates. Each line is matched to the load the
cameras counted, and where the two disagree, or a manifest truck never came, or a
truck came that no manifest expected, an exception is raised for a person to look
at, with the load's video beside it.

Balances say, per truck, route and day, how many crates went out, how many came
back, and how many are still outstanding: the leakage figure the customer is after.
They use each load's count of record (a person's correction where there is one).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from ivaas.domain.fleet import MATCH_THRESHOLD
from ivaas.domain.models import LoadingSession, SessionDirection, SessionStatus
from ivaas.domain.plates import plate_similarity


class ManifestError(ValueError):
    pass


class LineStatus(StrEnum):
    PENDING = "pending"  # no load matched yet
    MATCHED = "matched"
    NOT_SEEN = "not_seen"  # its day ended and no load matched


class ExceptionKind(StrEnum):
    COUNT_MISMATCH = "count_mismatch"  # the load's count and the manifest disagree
    NOT_SEEN = "not_seen"  # a manifest truck that never came
    UNEXPECTED = "unexpected"  # a load no manifest line expected


class ExceptionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


@dataclass
class ManifestLine:
    reference: str
    day: date
    plate: str
    direction: SessionDirection
    expected: int
    site_id: UUID
    route: str = ""
    session_id: UUID | None = None
    status: LineStatus = LineStatus.PENDING
    imported_by: str = ""
    imported_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.reference.strip():
            raise ManifestError("a manifest line needs a reference")
        if self.expected < 0:
            raise ManifestError("expected crates cannot be negative")
        self.plate = " ".join(self.plate.upper().split())
        if len(self.plate.replace(" ", "")) < 2:
            raise ManifestError("a manifest line needs the truck's plate")


@dataclass
class ManifestException:
    kind: ExceptionKind
    day: date
    raised_at: datetime
    plate: str | None = None
    route: str = ""
    session_id: UUID | None = None
    line_id: UUID | None = None
    expected: int | None = None
    counted: int | None = None
    status: ExceptionStatus = ExceptionStatus.OPEN
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    id: UUID = field(default_factory=uuid4)

    @property
    def difference(self) -> int | None:
        """counted - expected: negative means crates the manifest says went and the
        cameras did not see."""
        if self.expected is None or self.counted is None:
            return None
        return self.counted - self.expected

    def resolve(self, by: str, at: datetime, note: str) -> None:
        if self.status is ExceptionStatus.RESOLVED:
            raise ManifestError("this exception is already resolved")
        if not note.strip():
            raise ManifestError("say how it was resolved")
        self.status = ExceptionStatus.RESOLVED
        self.resolved_by, self.resolved_at, self.resolution_note = by, at, note.strip()


def local_day(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def match_line(
    line: ManifestLine,
    sessions: list[LoadingSession],
    *,
    tz: ZoneInfo,
    taken: set[UUID],
) -> LoadingSession | None:
    """The load this manifest line describes: that day (site time), that direction,
    that truck. A load with no plate cannot be matched to a named truck; that is
    for an operator to identify first. The closest plate wins, then the earliest."""
    best, best_key = None, None
    for s in sessions:
        if s.id in taken or s.direction is not line.direction or not s.plate:
            continue
        if local_day(s.opened_at, tz) != line.day:
            continue
        score = plate_similarity(line.plate, s.plate)
        if score < MATCH_THRESHOLD:
            continue
        key = (score, -s.opened_at.timestamp())
        if best_key is None or key > best_key:
            best, best_key = s, key
    return best


def settled(session: LoadingSession) -> bool:
    """A load whose count will not change any more by itself."""
    return session.status is not SessionStatus.OPEN


@dataclass
class Balance:
    """Crates out, crates back, and what is still out, for one truck, route or day."""

    key: str
    dispatched: int = 0
    returned: int = 0
    loads_out: int = 0
    loads_back: int = 0
    #: loads still being counted; not in the totals until they close
    in_progress: int = 0
    corrected: int = 0

    @property
    def outstanding(self) -> int:
        return self.dispatched - self.returned


def balances(
    sessions: list[LoadingSession],
    key_of,
) -> list[Balance]:
    """Group loads by `key_of(session)` and sum their counts of record. Loads with
    no key (no plate, no route) are grouped under "" and shown as such."""
    table: dict[str, Balance] = defaultdict(lambda: Balance(""))
    for s in sessions:
        k = key_of(s) or ""
        b = table[k]
        b.key = k
        if not settled(s):
            b.in_progress += 1
            continue
        if s.override_count is not None:
            b.corrected += 1
        if s.direction is SessionDirection.LOADING:
            b.dispatched += s.count_of_record
            b.loads_out += 1
        else:
            b.returned += s.count_of_record
            b.loads_back += 1
    return sorted(table.values(), key=lambda b: (-b.outstanding, b.key))
