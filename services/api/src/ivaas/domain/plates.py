"""Number plates as cameras and people actually write them.

An LPR read and a hand-written tally sheet disagree about spacing and about the
characters OCR and handwriting confuse (O/0, B/8, S/5...). Comparisons happen in
one canonical alphabet so "ABC 1234", "abc1234" and "A8C I234" are the same truck.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_CONFUSABLE = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
)


def normalize_plate(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def canonical(text: str | None) -> str:
    """The form two reads of one plate share, confusable characters folded together."""
    return normalize_plate(text).translate(_CONFUSABLE)


def plate_similarity(a: str | None, b: str | None) -> float:
    a, b = canonical(a), canonical(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()
