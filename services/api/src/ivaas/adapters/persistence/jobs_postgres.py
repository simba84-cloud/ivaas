"""AnalysisJobStore on Postgres. Loads and timeline are JSONB: they are read whole,
never queried by field, and their shape is owned by the domain."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, and_, or_, select
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
    # touched on every save, including each progress tick: the worker's heartbeat
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
        "updated_at": datetime.now(UTC),
    }


class PostgresJobStore:
    """The table is the only source of truth for job state.

    This used to keep queued and running jobs in a dict, because the worker ran in
    the API process and mutated the very object the API served. The worker is now a
    separate process, so that cache made the API serve its own stale copy forever:
    a job the worker had finished still read as queued. Progress is written on every
    tick, so reading the row costs nothing and is always current.
    """

    # a worker that has not written progress for this long is presumed dead
    DEFAULT_STALE_AFTER = timedelta(minutes=20)

    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        stale_after: timedelta | None = None,
    ) -> None:
        self._sm = sm
        self._stale_after = stale_after or self.DEFAULT_STALE_AFTER

    async def get(self, job_id: UUID) -> AnalysisJob | None:
        async with self._sm() as db:
            r = await db.get(AnalysisJobRow, job_id)
            return _to_domain(r) if r else None

    async def list_recent(self, limit: int = 50) -> list[AnalysisJob]:
        stmt = select(AnalysisJobRow).order_by(AnalysisJobRow.created_at.desc()).limit(limit)
        async with self._sm() as db:
            rows = (await db.scalars(stmt)).all()
        return [_to_domain(r) for r in rows]

    async def save(self, job: AnalysisJob) -> None:
        values = _values(job)
        stmt = insert(AnalysisJobRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[AnalysisJobRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)

    async def next_queued(self) -> AnalysisJob | None:
        """Claim one job, atomically.

        Several workers poll this table, so choosing a row and then marking it in a
        second statement would hand the same video to two of them. One UPDATE picks
        and claims in a single step; SKIP LOCKED lets the others move straight on to
        the next row instead of queueing behind this one.

        A job whose heartbeat has gone quiet for longer than `stale_after` is taken
        to belong to a worker that died, and is picked up again. Every progress tick
        touches the heartbeat, so a long analysis is never mistaken for a dead one.
        """
        now = datetime.now(UTC)
        stale_before = now - self._stale_after
        claim = (
            AnalysisJobRow.__table__.update()
            .where(
                AnalysisJobRow.id.in_(
                    select(AnalysisJobRow.id)
                    .where(
                        or_(
                            AnalysisJobRow.status == JobStatus.QUEUED.value,
                            and_(
                                AnalysisJobRow.status == JobStatus.RUNNING.value,
                                AnalysisJobRow.updated_at < stale_before,
                            ),
                        )
                    )
                    .order_by(AnalysisJobRow.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                    .scalar_subquery()
                )
            )
            .values(status=JobStatus.RUNNING.value, started_at=now, updated_at=now, progress=0.0)
            .returning(AnalysisJobRow)
        )
        async with self._sm.begin() as db:
            r = (await db.execute(claim)).mappings().first()
        if r is None:
            return None
        return _to_domain(SimpleNamespace(**r))
