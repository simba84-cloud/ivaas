"""AnalysisJobStore that keeps jobs in memory and mirrors each one to object storage
as JSON, reloading them at startup, so reports survive an API restart. A Postgres
table is the eventual home; this keeps reports safe until then."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from ivaas.domain.analysis import AnalysisJob, DetectedLoad, JobStatus, TimelineEvent
from ivaas.ports.analysis import ObjectStore

log = logging.getLogger(__name__)
PREFIX = "jobs/"


def _dump(job: AnalysisJob) -> bytes:
    d = asdict(job)
    for k in ("created_at", "started_at", "finished_at"):
        d[k] = d[k].isoformat() if d[k] else None
    d["id"], d["bay_id"], d["status"] = str(job.id), str(job.bay_id), job.status.value
    return json.dumps(d).encode()


def _load(raw: bytes) -> AnalysisJob:
    d = json.loads(raw)
    dt = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
    job = AnalysisJob(
        bay_id=UUID(d["bay_id"]),
        filename=d["filename"],
        object_key=d["object_key"],
        created_by=d["created_by"],
        created_at=dt(d["created_at"]),
        id=UUID(d["id"]),
        status=JobStatus(d["status"]),
        progress=d.get("progress", 0.0),
        error=d.get("error"),
        started_at=dt(d.get("started_at")),
        finished_at=dt(d.get("finished_at")),
        duration_s=d.get("duration_s"),
        loads=[DetectedLoad(**ld) for ld in d.get("loads", [])],
        timeline=[TimelineEvent(**e) for e in d.get("timeline", [])],
        summary=d.get("summary"),
    )
    if job.status is JobStatus.RUNNING:
        job.status = JobStatus.QUEUED  # was mid-run when the API stopped: run it again
        job.progress = 0.0
    return job


class PersistentJobStore:
    def __init__(self, objects: ObjectStore) -> None:
        self._objects = objects
        self._jobs: dict[UUID, AnalysisJob] = {}

    async def load_all(self) -> int:
        lister = getattr(self._objects, "list_keys", None)
        if lister is None:
            return 0
        n = 0
        for key in await lister(PREFIX):
            try:
                job = _load(await self._objects.read(key))
                self._jobs[job.id] = job
                n += 1
            except Exception:
                log.exception("could not load job %s", key)
        return n

    async def get(self, job_id: UUID) -> AnalysisJob | None:
        return self._jobs.get(job_id)

    async def list_recent(self, limit: int = 50) -> list[AnalysisJob]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]

    async def save(self, job: AnalysisJob) -> None:
        self._jobs[job.id] = job
        try:
            await self._objects.put(f"{PREFIX}{job.id}.json", _dump(job), "application/json")
        except Exception:
            log.exception("could not persist job %s", job.id)

    async def next_queued(self) -> AnalysisJob | None:
        queued = [j for j in self._jobs.values() if j.status is JobStatus.QUEUED]
        return min(queued, key=lambda j: j.created_at) if queued else None


InMemoryJobStore = PersistentJobStore  # keeps the old name working
