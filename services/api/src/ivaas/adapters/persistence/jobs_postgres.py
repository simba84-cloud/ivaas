"""AnalysisJobStore on Postgres. Loads and timeline are JSONB: they are read whole,
never queried by field, and their shape is owned by the domain."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from ivaas.adapters.persistence.postgres import Base
from ivaas.domain.analysis import AnalysisJob, DetectedLoad, JobStatus, TimelineEvent


class AnalysisJobRow(Base):
    __tablename__ = "analysis_jobs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    bay_id: Mapped[UUID] = mapped_column(ForeignKey("bays.id"), index=True)
    filename: Mapped[str] = mapped_column(String(120))
    object_key: Mapped[str] = mapped_column(String(512))
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    loads: Mapped[list] = mapped_column(JSONB, default=list)
    timeline: Mapped[list] = mapped_column(JSONB, default=list)
    summary: Mapped[str | None] = mapped_column(Text)


def _to_domain(r: AnalysisJobRow) -> AnalysisJob:
    job = AnalysisJob(
        bay_id=r.bay_id,
        filename=r.filename,
        object_key=r.object_key,
        created_by=r.created_by,
        created_at=r.created_at,
        id=r.id,
        status=JobStatus(r.status),
        progress=r.progress,
        error=r.error,
        started_at=r.started_at,
        finished_at=r.finished_at,
        duration_s=r.duration_s,
        loads=[DetectedLoad(**ld) for ld in r.loads],
        timeline=[TimelineEvent(**e) for e in r.timeline],
        summary=r.summary,
    )
    if job.status is JobStatus.RUNNING:  # the API stopped mid-run: run it again
        job.status, job.progress = JobStatus.QUEUED, 0.0
    return job


def _values(j: AnalysisJob) -> dict:
    return {
        "id": j.id,
        "bay_id": j.bay_id,
        "filename": j.filename,
        "object_key": j.object_key,
        "created_by": j.created_by,
        "created_at": j.created_at,
        "status": j.status.value,
        "progress": j.progress,
        "error": j.error,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "duration_s": j.duration_s,
        "loads": [asdict(ld) for ld in j.loads],
        "timeline": [asdict(e) for e in j.timeline],
        "summary": j.summary,
    }


class PostgresJobStore:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm
        self._live: dict[UUID, AnalysisJob] = {}  # the worker mutates the object it holds

    async def get(self, job_id: UUID) -> AnalysisJob | None:
        if job_id in self._live:
            return self._live[job_id]
        async with self._sm() as db:
            r = await db.get(AnalysisJobRow, job_id)
            return _to_domain(r) if r else None

    async def list_recent(self, limit: int = 50) -> list[AnalysisJob]:
        stmt = select(AnalysisJobRow).order_by(AnalysisJobRow.created_at.desc()).limit(limit)
        async with self._sm() as db:
            rows = (await db.scalars(stmt)).all()
        return [self._live.get(r.id) or _to_domain(r) for r in rows]

    async def save(self, job: AnalysisJob) -> None:
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            self._live[job.id] = job
        else:
            self._live.pop(job.id, None)
        values = _values(job)
        stmt = insert(AnalysisJobRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[AnalysisJobRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def next_queued(self) -> AnalysisJob | None:
        stmt = (
            select(AnalysisJobRow)
            .where(AnalysisJobRow.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
            .order_by(AnalysisJobRow.created_at)
            .limit(1)
        )
        async with self._sm() as db:
            r = (await db.scalars(stmt)).first()
        if r is None:
            return None
        job = self._live.get(r.id) or _to_domain(r)
        if job.status is JobStatus.RUNNING:
            return None  # already being worked on in this process
        return job
