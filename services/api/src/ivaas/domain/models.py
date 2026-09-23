"""Core domain entities for the IVaaS platform.

Pure Python: no framework, database or transport imports are allowed in this
package. Everything outside the domain depends on it, never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4


class CameraRole(StrEnum):
    OVERHEAD = "overhead"
    SIDE_HIGH = "side_high"
    SIDE_MID = "side_mid"
    SIDE_LOW = "side_low"
    CHOKEPOINT = "chokepoint"
    LPR = "lpr"


class CameraStatus(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class SessionDirection(StrEnum):
    LOADING = "loading"
    OFFLOADING = "offloading"


class SessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    RECONCILED = "reconciled"
    DISPUTED = "disputed"


@dataclass(frozen=True)
class Site:
    id: UUID
    name: str
    timezone: str = "UTC"


@dataclass(frozen=True)
class Bay:
    id: UUID
    site_id: UUID
    name: str
    height_m: float = 4.0
    width_m: float = 3.0


# Every transport the media gateway can pull. Vendor never matters: any camera, NVR
# or encoder that speaks one of these is supported, and anything that can only
# *push* (or a recorded file being replayed) publishes to the gateway instead.
PULL_SCHEMES = frozenset(
    {"rtsp", "rtsps", "rtmp", "rtmps", "srt", "http", "https", "udp", "whep", "wheps"}
)


@dataclass(frozen=True)
class StreamSource:
    """Where a camera's video comes from. `url=None` means the camera pushes to us."""

    url: str | None = None

    def __post_init__(self) -> None:
        if self.url is None:
            return
        parts = urlsplit(self.url)
        if parts.scheme.lower() not in PULL_SCHEMES:
            raise InvalidStreamSourceError(
                f"unsupported scheme '{parts.scheme}'; expected one of {sorted(PULL_SCHEMES)}"
            )
        if not parts.hostname and parts.scheme.lower() != "udp":
            raise InvalidStreamSourceError("stream URL has no host")

    @property
    def is_push(self) -> bool:
        return self.url is None

    @property
    def protocol(self) -> str:
        return "push" if self.url is None else urlsplit(self.url).scheme.lower()

    @property
    def redacted(self) -> str | None:
        """URL safe to show in a UI or log: the password never leaves the backend."""
        if self.url is None:
            return None
        parts = urlsplit(self.url)
        if parts.password is None:
            return self.url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit(parts._replace(netloc=f"{parts.username}:***@{host}"))


@dataclass
class Camera:
    id: UUID
    bay_id: UUID
    name: str
    role: CameraRole
    stream_path: str
    status: CameraStatus = CameraStatus.OFFLINE
    last_seen_at: datetime | None = None
    source: StreamSource = field(default_factory=StreamSource)

    def mark_seen(self, at: datetime) -> None:
        self.status = CameraStatus.ONLINE
        self.last_seen_at = at


@dataclass(frozen=True)
class PlateRead:
    plate: str
    confidence: float
    camera_id: UUID
    read_at: datetime


@dataclass(frozen=True)
class CrateCrossing:
    """A single tracked crate crossing the entry/exit chokepoint line."""

    track_id: int
    camera_id: UUID
    direction: SessionDirection
    confidence: float
    crossed_at: datetime
    crates: int = 1  # >1 when a whole stack crossed as one tracked object

    def __post_init__(self) -> None:
        if self.crates < 1:
            raise ValueError("a crossing must represent at least one crate")


@dataclass
class LoadingSession:
    """One truck at one bay: the unit that gets reconciled."""

    bay_id: UUID
    direction: SessionDirection
    opened_at: datetime
    id: UUID = field(default_factory=uuid4)
    plate: str | None = None
    status: SessionStatus = SessionStatus.OPEN
    ai_count: int = 0
    manual_count: int | None = None
    closed_at: datetime | None = None
    plate_last_seen_at: datetime | None = None  # drives auto-close: the truck has left

    def attach_plate(self, read: PlateRead) -> None:
        if self.status is not SessionStatus.OPEN:
            raise SessionClosedError(self.id)
        self.plate = read.plate
        self.plate_last_seen_at = read.read_at

    def idle_since(self, now: datetime) -> timedelta:
        """How long since the truck was last seen (or since opening, if never seen)."""
        return now - (self.plate_last_seen_at or self.opened_at)

    def record_crossing(self, crossing: CrateCrossing) -> None:
        if self.status is not SessionStatus.OPEN:
            raise SessionClosedError(self.id)
        if crossing.direction is self.direction:
            self.ai_count += crossing.crates
        else:
            # crates carried back across the line undo their count, never below zero
            self.ai_count = max(0, self.ai_count - crossing.crates)

    def close(self, at: datetime) -> None:
        if self.status is not SessionStatus.OPEN:
            raise SessionClosedError(self.id)
        self.status = SessionStatus.CLOSED
        self.closed_at = at

    def reconcile(self, manual_count: int, tolerance: float) -> None:
        """Compare the AI count against a manual verification count."""
        if self.status is SessionStatus.OPEN:
            raise SessionStillOpenError(self.id)
        if manual_count < 0:
            raise ValueError("manual_count must be non-negative")
        self.manual_count = manual_count
        self.status = (
            SessionStatus.RECONCILED
            if self.accuracy is not None and self.accuracy >= tolerance
            else SessionStatus.DISPUTED
        )

    @property
    def variance(self) -> int | None:
        if self.manual_count is None:
            return None
        return self.ai_count - self.manual_count

    @property
    def accuracy(self) -> float | None:
        """1 - |error| / truth, floored at 0. None until manually verified."""
        if self.manual_count is None:
            return None
        if self.manual_count == 0:
            return 1.0 if self.ai_count == 0 else 0.0
        return max(0.0, 1.0 - abs(self.ai_count - self.manual_count) / self.manual_count)


class DomainError(Exception):
    pass


class SessionClosedError(DomainError):
    def __init__(self, session_id: UUID) -> None:
        super().__init__(f"session {session_id} is not open")


class SessionStillOpenError(DomainError):
    def __init__(self, session_id: UUID) -> None:
        super().__init__(f"session {session_id} must be closed before reconciliation")


class NotFoundError(DomainError):
    pass


class InvalidStreamSourceError(DomainError, ValueError):
    pass


class DuplicateStreamPathError(DomainError):
    def __init__(self, stream_path: str) -> None:
        super().__init__(f"stream path '{stream_path}' is already in use")
