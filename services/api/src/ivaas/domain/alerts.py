"""A person saying "I have seen this".

Alerts themselves are not stored: they are conditions (a camera with no signal, a
disputed load) worked out from current state, so they clear when the condition does.
What is stored is the acknowledgement, keyed by the occurrence it answers. The key
carries whatever makes an occurrence distinct, so a camera that recovers and fails
again raises a new alert rather than hiding behind yesterday's acknowledgement.

Acknowledging changes nothing else. It does not resolve the condition, and it never
touches a count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

#: letters, digits and the separators alert keys are built from; bounded so a key
#: cannot be used to store arbitrary text
KEY_PATTERN = re.compile(r"^[A-Za-z0-9:._@+-]{1,200}$")


class InvalidAlertKeyError(ValueError):
    pass


def check_key(key: str) -> str:
    if not KEY_PATTERN.fullmatch(key):
        raise InvalidAlertKeyError("alert key must be 1-200 letters, digits or : . _ @ + -")
    return key


@dataclass(frozen=True)
class AlertAcknowledgement:
    key: str
    acknowledged_by: str
    acknowledged_at: datetime
    note: str | None = None
