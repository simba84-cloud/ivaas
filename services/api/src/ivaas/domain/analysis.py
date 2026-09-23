"""Offline analysis of an uploaded video: the job and its report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class DetectedLoad:
    """One truck load found in the video."""

    start_s: float
    end_s: float
    stacks: int
    crates: int
    low_confidence: int
    plate: str | None = None


@dataclass(frozen=True)
class TimelineEvent:
    at_s: float
    kind: str  # stack_counted | plate_read | load_started | load_ended
    detail: str
    frame_key: str | None = None  # object key of a saved frame, if one was captured


@dataclass
class AnalysisJob:
    bay_id: UUID
    filename: str
    object_key: str  # where the upload lives in object storage
    created_by: str
    created_at: datetime
    id: UUID = field(default_factory=uuid4)
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0  # 0..1
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    loads: list[DetectedLoad] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    summary: str | None = None  # written by the assistant once analysis is done

    @property
    def total_crates(self) -> int:
        return sum(ld.crates for ld in self.loads)

    def start(self, at: datetime) -> None:
        self.status, self.started_at, self.progress = JobStatus.RUNNING, at, 0.0

    def finish(
        self,
        at: datetime,
        loads: list[DetectedLoad],
        timeline: list[TimelineEvent],
        duration_s: float,
    ) -> None:
        self.status, self.finished_at, self.progress = JobStatus.DONE, at, 1.0
        self.loads, self.timeline, self.duration_s = list(loads), list(timeline), duration_s

    def fail(self, at: datetime, error: str) -> None:
        self.status, self.finished_at, self.error = JobStatus.FAILED, at, error[:500]
