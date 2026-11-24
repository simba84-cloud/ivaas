"""The driver register: who took crates out and who brought them back (ADR 0004).

A driver is identified at a controlled bay by a badge (a QR code or the number
printed under it), and, where the driver has one, a PIN typed at the kiosk. The
badge names the driver; the PIN proves the badge is in its owner's hands. The PIN
is held only as an Argon2id hash and never leaves the API.

Face recognition is a later phase (ADR 0004): it will only ever suggest a driver,
and the badge will still confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

#: what a badge code may contain once normalised: printable on a card, typeable
BADGE_PATTERN = re.compile(r"^[A-Z0-9]{3,32}$")
PIN_PATTERN = re.compile(r"^\d{4,8}$")


class DriverError(ValueError):
    pass


def normalize_badge(code: str) -> str:
    """Badge codes are kept and compared without case, spaces or dashes, so
    "bi 0042" and "BI-0042" typed at a kiosk are the badge "BI0042"."""
    return "".join(code.upper().split()).replace("-", "")


def check_pin(pin: str) -> str:
    if not PIN_PATTERN.match(pin):
        raise DriverError("a PIN is 4 to 8 digits")
    return pin


@dataclass
class Driver:
    name: str
    badge_code: str
    employee_ref: str = ""
    phone: str = ""
    active: bool = True
    #: Argon2id hash of the driver's PIN, or None when the driver has no PIN.
    #: Never returned, logged or exported.
    pin_hash: str | None = None
    created_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.name = " ".join(self.name.split())
        if len(self.name) < 2:
            raise DriverError("a driver needs a name")
        self.badge_code = normalize_badge(self.badge_code)
        if not BADGE_PATTERN.match(self.badge_code):
            raise DriverError("a badge code is 3 to 32 letters or digits")

    @property
    def has_pin(self) -> bool:
        return self.pin_hash is not None


def match_badge(code: str, drivers: list[Driver]) -> Driver | None:
    """The active driver holding this badge, or None. Exact after normalising:
    a badge is scanned or typed from the card, so nothing is fuzzy-matched."""
    key = normalize_badge(code)
    return next((d for d in drivers if d.active and d.badge_code == key), None)
