"""Controlled bays: crates presented in standard units, validated, and traced to a
driver (ADR 0004).

At a controlled bay the question changes from "how many crates are there?" to
"is this what the driver declared, in the standard the business agreed?". A driver
checks in with a badge, declares a number of standard units (say 2 units of
5 columns x 5 crates), and the cameras then check each column they count against
the standard height. Every column either reaches the standard, falls short of it,
or could not be judged; one column that could not be judged makes the whole
transaction unverified rather than guessed.

The ledger then says, per driver, how many crates went out and came back, and how
many transactions could not be verified. It never folds those into the totals as
zero: a figure the cameras could not support is reported as missing, not as none.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from ivaas.domain.models import SessionDirection

#: share of a column's per-frame height estimates that must agree with its median
#: before the column's height is taken as measured rather than guessed
MIN_AGREEMENT = 0.6
#: a standard unit no bigger than a loaded truck bay could plausibly present
MAX_COLUMNS, MAX_LAYERS, MAX_UNITS = 20, 40, 50


class CrateControlError(ValueError):
    pass


@dataclass(frozen=True)
class BayStandard:
    """What one standard unit is at this bay: `columns` side by side, each
    `layers` crates high. A bay without a standard cannot be controlled."""

    bay_id: UUID
    columns: int
    layers: int
    controlled: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.columns <= MAX_COLUMNS:
            raise CrateControlError(f"a standard unit has 1 to {MAX_COLUMNS} columns")
        if not 1 <= self.layers <= MAX_LAYERS:
            raise CrateControlError(f"a standard column is 1 to {MAX_LAYERS} crates high")

    @property
    def crates_per_unit(self) -> int:
        return self.columns * self.layers


@dataclass(frozen=True)
class StackObservation:
    """One column the cameras counted at a controlled bay, as the edge reported it."""

    session_id: UUID
    track_id: int
    camera_id: UUID
    direction: SessionDirection
    crates: int
    confidence: float
    #: share of per-frame estimates that agreed with the reported height; None when
    #: the edge node is too old to say, which means the height cannot be vouched for
    agreement: float | None
    at: datetime
    id: UUID = field(default_factory=uuid4)

    @property
    def sound(self) -> bool:
        """Whether this column's height was measured well enough to judge."""
        # confidence 0 is the edge's own "too few frames, counted as 1" fallback
        return (
            self.confidence > 0 and self.agreement is not None and (self.agreement >= MIN_AGREEMENT)
        )


class ColumnVerdict(StrEnum):
    FULL = "full"  # exactly the standard height
    NON_STANDARD = "non_standard"  # measured, and not the standard height
    UNSURE = "unsure"  # not measured well enough to say


class TransactionStatus(StrEnum):
    OPEN = "open"  # checked in; the cameras are still counting
    VERIFIED = "verified"  # exactly the declared standard units
    SHORT = "short"  # fewer crates than declared
    OVER = "over"  # more crates than declared
    #: the right number of crates, but not in standard columns, so the standard
    #: could not be used to vouch for the count
    NON_STANDARD = "non_standard"
    #: at least one column could not be judged, or nothing was seen at all
    UNVERIFIED = "unverified"
    #: a supervisor looked at an exception and accepted it, on the record
    ACCEPTED = "accepted"


EXCEPTIONS = frozenset(
    {
        TransactionStatus.SHORT,
        TransactionStatus.OVER,
        TransactionStatus.NON_STANDARD,
        TransactionStatus.UNVERIFIED,
    }
)


@dataclass(frozen=True)
class Validation:
    status: TransactionStatus
    expected_columns: int
    expected_crates: int
    full_columns: int
    non_standard_columns: int
    unsure_columns: int
    #: crates in the columns the cameras measured; None when any column could not
    #: be judged, because a partial sum would understate what was there
    counted_crates: int | None
    columns: tuple[tuple[int, ColumnVerdict], ...] = ()


def net_columns(observations: list[StackObservation], direction: SessionDirection):
    """The columns that stayed: each column carried back the other way cancels the
    most recent one counted before it."""
    kept: list[StackObservation] = []
    for o in sorted(observations, key=lambda o: o.at):
        if o.direction is direction:
            kept.append(o)
        elif kept:
            kept.pop()
    return kept


def validate(
    observations: list[StackObservation],
    standard: BayStandard,
    declared_units: int,
    direction: SessionDirection,
) -> Validation:
    if declared_units < 1:
        raise CrateControlError("declare at least one standard unit")
    expected_columns = declared_units * standard.columns
    expected_crates = declared_units * standard.crates_per_unit
    columns: list[tuple[int, ColumnVerdict]] = []
    for o in net_columns(observations, direction):
        if not o.sound:
            verdict = ColumnVerdict.UNSURE
        elif o.crates == standard.layers:
            verdict = ColumnVerdict.FULL
        else:
            verdict = ColumnVerdict.NON_STANDARD
        columns.append((o.crates, verdict))

    full = sum(v is ColumnVerdict.FULL for _, v in columns)
    odd = sum(v is ColumnVerdict.NON_STANDARD for _, v in columns)
    unsure = sum(v is ColumnVerdict.UNSURE for _, v in columns)
    counted = None if unsure or not columns else sum(c for c, _ in columns)

    if counted is None:
        # nothing seen at all is not "zero crates": the camera may have been blocked
        status = TransactionStatus.UNVERIFIED
    elif counted < expected_crates:
        status = TransactionStatus.SHORT
    elif counted > expected_crates:
        status = TransactionStatus.OVER
    elif odd or full != expected_columns:
        status = TransactionStatus.NON_STANDARD
    else:
        status = TransactionStatus.VERIFIED
    return Validation(
        status=status,
        expected_columns=expected_columns,
        expected_crates=expected_crates,
        full_columns=full,
        non_standard_columns=odd,
        unsure_columns=unsure,
        counted_crates=counted,
        columns=tuple(columns),
    )


class AcceptReason(StrEnum):
    """Why a supervisor accepted an exception. Fixed so the reasons can be counted."""

    CAMERA_BLOCKED = "camera_blocked"
    DAMAGED_CRATES = "damaged_crates"
    COUNTED_BY_HAND = "counted_by_hand"
    DECLARATION_ERROR = "declaration_error"
    OTHER = "other"


@dataclass
class CrateTransaction:
    """One driver's dispatch or return at a controlled bay."""

    bay_id: UUID
    driver_id: UUID
    session_id: UUID
    direction: SessionDirection
    declared_units: int
    #: the standard in force at check-in, kept so a later change to the bay's
    #: standard cannot rewrite what this transaction was checked against
    standard_columns: int
    standard_layers: int
    opened_at: datetime
    checked_in_by: str
    status: TransactionStatus = TransactionStatus.OPEN
    closed_at: datetime | None = None
    full_columns: int | None = None
    non_standard_columns: int | None = None
    unsure_columns: int | None = None
    counted_crates: int | None = None
    #: what the validation said, kept when a supervisor accepts the exception
    validated_status: TransactionStatus | None = None
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    accept_reason: AcceptReason | None = None
    accept_note: str | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not 1 <= self.declared_units <= MAX_UNITS:
            raise CrateControlError(f"declare 1 to {MAX_UNITS} standard units")

    @property
    def standard(self) -> BayStandard:
        return BayStandard(self.bay_id, self.standard_columns, self.standard_layers)

    @property
    def expected_crates(self) -> int:
        return self.declared_units * self.standard.crates_per_unit

    def close(self, result: Validation, at: datetime) -> None:
        if self.status is not TransactionStatus.OPEN:
            raise CrateControlError("this transaction is already closed")
        self.status = self.validated_status = result.status
        self.closed_at = at
        self.full_columns = result.full_columns
        self.non_standard_columns = result.non_standard_columns
        self.unsure_columns = result.unsure_columns
        self.counted_crates = result.counted_crates

    def accept(
        self, *, by: str, at: datetime, reason: AcceptReason, note: str | None = None
    ) -> None:
        """Accept an exception, on the record. The counted crates stay exactly as
        the cameras measured them: accepting says a person looked, not that the
        shortfall did not happen."""
        if self.status not in EXCEPTIONS:
            raise CrateControlError(
                "only a short, over, non-standard or unverified transaction can be accepted"
            )
        if reason is AcceptReason.OTHER and not (note or "").strip():
            raise CrateControlError("say what happened when the reason is 'other'")
        self.status = TransactionStatus.ACCEPTED
        self.accepted_by, self.accepted_at = by, at
        self.accept_reason, self.accept_note = reason, (note or "").strip() or None


@dataclass
class DriverBalance:
    """Crates out, crates back and outstanding for one driver, from measured counts
    only. Transactions the cameras could not measure are counted beside the totals,
    with what the driver declared, never added in as if measured."""

    driver_id: UUID
    issued: int = 0
    returned: int = 0
    dispatches: int = 0
    returns: int = 0
    in_progress: int = 0
    exceptions: int = 0
    unmeasured: int = 0
    unmeasured_declared_out: int = 0
    unmeasured_declared_back: int = 0
    last_at: datetime | None = None

    @property
    def outstanding(self) -> int:
        return self.issued - self.returned


def driver_balances(transactions: list[CrateTransaction]) -> list[DriverBalance]:
    table: dict[UUID, DriverBalance] = defaultdict(lambda: DriverBalance(UUID(int=0)))
    for t in transactions:
        b = table[t.driver_id]
        b.driver_id = t.driver_id
        b.last_at = max(filter(None, (b.last_at, t.opened_at)))
        out = t.direction is SessionDirection.LOADING
        if t.status is TransactionStatus.OPEN:
            b.in_progress += 1
            continue
        if t.status in EXCEPTIONS:
            b.exceptions += 1
        if t.counted_crates is None:
            b.unmeasured += 1
            if out:
                b.unmeasured_declared_out += t.expected_crates
            else:
                b.unmeasured_declared_back += t.expected_crates
            continue
        if out:
            b.issued += t.counted_crates
            b.dispatches += 1
        else:
            b.returned += t.counted_crates
            b.returns += 1
    return sorted(table.values(), key=lambda b: (-b.outstanding, -b.unmeasured))
