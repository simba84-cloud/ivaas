"""Use cases: submit a video for analysis, run queued jobs, build the report."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from ivaas.domain.analysis import AnalysisJob, DetectedLoad, JobStatus, TimelineEvent
from ivaas.domain.models import NotFoundError
from ivaas.ports.analysis import AnalysisJobStore, ObjectStore, VideoAnalyser
from ivaas.ports.repositories import BayReader, Clock, EventPublisher

log = logging.getLogger(__name__)
SUBJECT_ANALYSIS = "ivaas.analysis.updated"
ALLOWED_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/x-matroska",
    "video/webm",
    "application/octet-stream",
}


def job_payload(job: AnalysisJob) -> dict:
    return {
        "id": str(job.id),
        "status": job.status.value,
        "progress": round(job.progress, 3),
        "filename": job.filename,
        "loads": len(job.loads),
        "crates": job.total_crates,
        "error": job.error,
    }


@dataclass
class SubmitVideo:
    bays: BayReader
    jobs: AnalysisJobStore
    objects: ObjectStore
    events: EventPublisher
    clock: Clock

    async def __call__(
        self,
        bay_id: UUID,
        filename: str,
        content_type: str,
        data: AsyncIterator[bytes],
        created_by: str,
    ) -> AnalysisJob:
        if await self.bays.get(bay_id) is None:
            raise NotFoundError(f"bay {bay_id} not found")
        if content_type not in ALLOWED_TYPES:
            raise ValueError(f"unsupported content type {content_type}")
        job_id = uuid4()
        safe_name = Path(filename).name[:120] or "upload.mp4"
        key = f"uploads/{job_id}/{safe_name}"
        await self.objects.put(key, data, content_type)
        job = AnalysisJob(
            bay_id=bay_id,
            filename=safe_name,
            object_key=key,
            created_by=created_by,
            created_at=self.clock.now(),
            id=job_id,
        )
        await self.jobs.save(job)
        await self.events.publish(SUBJECT_ANALYSIS, job_payload(job))
        return job


@dataclass
class RunNextJob:
    """Worker step: take one queued job and run it to completion."""

    jobs: AnalysisJobStore
    objects: ObjectStore
    analyser: VideoAnalyser
    events: EventPublisher
    clock: Clock

    async def __call__(self) -> AnalysisJob | None:
        job = await self.jobs.next_queued()
        if job is None:
            return None
        job.start(self.clock.now())
        await self.jobs.save(job)
        await self.events.publish(SUBJECT_ANALYSIS, job_payload(job))

        async def save_frame(name: str, jpeg: bytes) -> str:
            key = f"frames/{job.id}/{name}"
            await self.objects.put(key, jpeg, "image/jpeg")
            return key

        async def on_progress(p: float) -> None:
            job.progress = p
            await self.jobs.save(job)
            await self.events.publish(SUBJECT_ANALYSIS, job_payload(job))

        try:
            with tempfile.TemporaryDirectory() as tmp:
                local = str(Path(tmp) / job.filename)
                await self.objects.download_to(job.object_key, local)
                loads, timeline, duration = await self.analyser.analyse(
                    job, local, save_frame, on_progress
                )
            job.finish(self.clock.now(), loads, timeline, duration)
        except Exception as exc:
            log.exception("analysis job %s failed", job.id)
            job.fail(self.clock.now(), f"{type(exc).__name__}: {exc}")
        await self.jobs.save(job)
        await self.events.publish(SUBJECT_ANALYSIS, job_payload(job))
        return job


async def job_worker(get_run_next: Callable[[], RunNextJob], poll_s: float = 2.0) -> None:
    """Resolves the use case on every pass so swapped components (a reloaded model,
    a test double) take effect without a restart."""
    while True:
        try:
            if await get_run_next()() is None:
                await asyncio.sleep(poll_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("job worker error")
            await asyncio.sleep(poll_s)


__all__ = [
    "SubmitVideo",
    "RunNextJob",
    "job_worker",
    "DetectedLoad",
    "TimelineEvent",
    "JobStatus",
    "job_payload",
]
