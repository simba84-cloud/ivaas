"""Turn noisy per-frame OCR reads into one confirmed plate per truck.

Two defences, both learned from running the reader on site footage:
  1. Format + confidence gate. Logos and signs come back as short, odd strings at
     0.4-0.7; real plates match the national format at 0.9+.
  2. Temporal vote. One frame can be confidently *wrong* (a 0 read as 9 at 0.96).
     A plate is emitted only after `min_reads` agreeing reads inside `window`, and
     then not again for `cooldown` so a truck idling at the bay is one event, not 200.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Sequence
from datetime import datetime, timedelta

from ivaas_pipeline.types import Frame, PlateCandidate, PlateEvent

# Default: three letters, four digits (ABC 1234). Override per site/country.
ZW_PLATE = re.compile(r"^([A-Z]{3})(\d{4})$")


class PlateNormaliser:
    def __init__(self, pattern: re.Pattern[str] = ZW_PLATE, min_confidence: float = 0.85) -> None:
        self._pattern = pattern
        self._min_conf = min_confidence

    def normalise(self, candidate: PlateCandidate) -> str | None:
        if candidate.confidence < self._min_conf:
            return None
        compact = re.sub(r"[^A-Z0-9]", "", candidate.text.upper())
        m = self._pattern.match(compact)
        if m is None:
            return None
        return " ".join(g for g in m.groups() if g) if m.groups() else compact


class PlateVoter:
    def __init__(
        self,
        normaliser: PlateNormaliser | None = None,
        *,
        min_reads: int = 3,
        window: timedelta = timedelta(seconds=5),
        cooldown: timedelta = timedelta(minutes=2),
    ) -> None:
        self._norm = normaliser or PlateNormaliser()
        self._min_reads, self._window, self._cooldown = min_reads, window, cooldown
        self._recent: deque[tuple[datetime, str, float]] = deque()
        self._emitted: dict[str, datetime] = {}

    def update(self, frame: Frame, candidates: Sequence[PlateCandidate]) -> list[PlateEvent]:
        now = frame.captured_at
        for c in candidates:
            plate = self._norm.normalise(c)
            if plate is not None:
                self._recent.append((now, plate, c.confidence))
        while self._recent and now - self._recent[0][0] > self._window:
            self._recent.popleft()

        counts = Counter(p for _, p, _ in self._recent)
        out: list[PlateEvent] = []
        for plate, n in counts.most_common():
            if n < self._min_reads:
                break
            last = self._emitted.get(plate)
            if last is not None and now - last < self._cooldown:
                continue
            confs = [c for _, p, c in self._recent if p == plate]
            self._emitted[plate] = now
            out.append(PlateEvent(frame.camera_id, plate, sum(confs) / len(confs), n, now))
        # forget cooldowns that have expired so memory stays bounded
        for plate in [p for p, t in self._emitted.items() if now - t > self._cooldown]:
            del self._emitted[plate]
        return out
