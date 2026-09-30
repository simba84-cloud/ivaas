"""Tally sheets: the paper count the AI is judged against.

Bakers Inn staff count every dolly or stack as it crosses the chokepoint and write it
on a paper sheet, one sheet per truck per session. The sheets are entered the next
morning, without sight of the AI count, and each is matched to the loading session it
describes. The sheet's figure then becomes that session's manual count, so accuracy,
disputes and approvals all run through `LoadingSession.reconcile` unchanged.

Pure Python, like the rest of the domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from ivaas.domain.models import LoadingSession, SessionDirection

#: the paper form says LOAD / RETURN; sessions say loading / offloading
SHEET_DIRECTIONS = {"LOAD": SessionDirection.LOADING, "RETURN": SessionDirection.OFFLOADING}
EXAMPLE_PREFIX = "EXAMPLE"
PLATE_MATCH = 0.8


class TallyStatus(StrEnum):
    PENDING = "pending"  # saved, not yet matched
    MATCHED = "matched"  # linked to a session that is still open
    RECONCILED = "reconciled"  # its figure is now the session's manual count
    CONFLICT = "conflict"  # the session already holds a different manual count
    UNMATCHED = "unmatched"  # no session fits: check plate, bay and times


class InvalidTallySheetError(ValueError):
    pass


@dataclass(frozen=True)
class TallyLine:
    line_no: int
    #: negative for a stack carried back off the truck (note code X on the form)
    crates: int
    note: str | None = None


@dataclass
class TallySheet:
    sheet_id: str
    bay_id: UUID
    date: date
    plate: str
    direction: SessionDirection
    entered_by_user: str
    entered_at: datetime
    start_time: time | None = None
    end_time: time | None = None
    lines: list[TallyLine] = field(default_factory=list)
    total_on_paper: int | None = None
    pages: int | None = None
    counted_by: str | None = None
    verified_by: str | None = None
    entered_by: str | None = None
    notes: str | None = None
    session_id: UUID | None = None
    status: TallyStatus = TallyStatus.PENDING
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.sheet_id = self.sheet_id.strip()
        # the order written on the paper, whatever order they were typed or stored in
        self.lines = sorted(self.lines, key=lambda ln: ln.line_no)
        if not self.sheet_id:
            raise InvalidTallySheetError("sheet_id is required")
        if self.sheet_id.upper().startswith(EXAMPLE_PREFIX):
            raise InvalidTallySheetError(f"{self.sheet_id} is an example row, not a real sheet")
        if not normalize_plate(self.plate):
            raise InvalidTallySheetError("truck plate is required")
        numbers = [ln.line_no for ln in self.lines]
        if len(numbers) != len(set(numbers)):
            raise InvalidTallySheetError("line numbers must be unique on a sheet")
        if self.total_on_paper is not None and self.total_on_paper < 0:
            raise InvalidTallySheetError("total on paper cannot be negative")
        if self.truth is None:
            raise InvalidTallySheetError("a sheet needs its stack lines or a total")
        if self.truth < 0:
            raise InvalidTallySheetError("the lines add up to fewer than zero crates")

    @property
    def line_total(self) -> int | None:
        return sum(ln.crates for ln in self.lines) if self.lines else None

    @property
    def truth(self) -> int | None:
        """The count of record from this sheet: its lines if typed in, else its total."""
        return self.line_total if self.lines else self.total_on_paper

    @property
    def transcription_mismatch(self) -> bool:
        """The typed lines do not add up to the total written on the paper."""
        return (
            self.line_total is not None
            and self.total_on_paper is not None
            and self.line_total != self.total_on_paper
        )

    def window(self, tz: ZoneInfo) -> tuple[datetime, datetime]:
        """When the truck was being loaded, per the sheet, as aware datetimes."""
        start = datetime.combine(self.date, self.start_time or time(0, 0), tz)
        end = datetime.combine(self.date, self.end_time or time(23, 59), tz)
        if end < start:  # counted across midnight
            end += timedelta(days=1)
        return start, end


def parse_direction(value: str) -> SessionDirection:
    try:
        return SHEET_DIRECTIONS[value.strip().upper()]
    except KeyError:
        raise InvalidTallySheetError("direction must be LOAD or RETURN") from None


# OCR and handwriting confuse these; compare in one canonical alphabet.
_CONFUSABLE = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
)


def normalize_plate(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def plate_similarity(a: str | None, b: str | None) -> float:
    a, b = normalize_plate(a).translate(_CONFUSABLE), normalize_plate(b).translate(_CONFUSABLE)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def match_session(
    sheet: TallySheet,
    candidates: list[LoadingSession],
    *,
    tz: ZoneInfo,
    now: datetime,
    taken: set[UUID] = frozenset(),
    tolerance: timedelta = timedelta(minutes=15),
) -> LoadingSession | None:
    """The session this sheet describes, or None when nothing fits.

    A candidate must be at the sheet's bay, in the sheet's direction, overlap the
    sheet's time window (widened by `tolerance` for clocks and late starts), and not
    already belong to another sheet. If the session has a plate it must agree with
    the sheet's; a session with no plate read can still match on time alone, but
    loses to any session whose plate agrees.
    """
    start, end = sheet.window(tz)
    start, end = start - tolerance, end + tolerance
    best, best_key = None, None
    for s in candidates:
        if s.bay_id != sheet.bay_id or s.direction is not sheet.direction or s.id in taken:
            continue
        s_end = s.closed_at or now
        overlap = (min(end, s_end) - max(start, s.opened_at)).total_seconds()
        if overlap <= 0:
            continue
        similarity = plate_similarity(sheet.plate, s.plate)
        if s.plate and similarity < PLATE_MATCH:
            continue
        key = (similarity >= PLATE_MATCH, overlap)
        if best_key is None or key > best_key:
            best, best_key = s, key
    return best
