"""Evidence clips: what the camera saw when a load was counted (proposal M2, M4, M5).

A count that money depends on is challenged weeks later ("the truck left with 40
fewer crates"). The answer is the video of each crossing, kept with the load it
belongs to for the tenant's retention period (90 days for Bakers Inn) and then
deleted. A clip is recorded at the edge, where the video is, and only the few
seconds around each count travel to the cloud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

#: longer than any single crossing or plate read needs; a longer upload is a mistake
MAX_CLIP_SECONDS = 120
MAX_CLIP_BYTES = 256 * 1024 * 1024


class EvidenceKind(StrEnum):
    CROSSING = "crossing"  # a dolly or stack crossing the chokepoint
    PLATE = "plate"  # the truck's plate being read


class EvidenceError(ValueError):
    pass


@dataclass
class EvidenceClip:
    bay_id: UUID
    camera_id: UUID
    kind: EvidenceKind
    started_at: datetime
    ended_at: datetime
    object_key: str
    size_bytes: int
    sha256: str
    created_at: datetime
    expires_at: datetime
    #: the load it shows; None when no session covered that moment (still kept)
    session_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        span = self.ended_at - self.started_at
        if span <= timedelta(0):
            raise EvidenceError("a clip must end after it starts")
        if span > timedelta(seconds=MAX_CLIP_SECONDS):
            raise EvidenceError(f"a clip is at most {MAX_CLIP_SECONDS} seconds")

    @property
    def seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()
