"""PostgreSQL adapters (SQLAlchemy 2 async). Mapping between rows and domain
entities stays inside this module so the domain never sees an ORM type.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, delete, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ivaas.adapters.persistence.secrets import SecretBox
from ivaas.domain.models import (
    Bay,
    Camera,
    CameraRole,
    CameraStatus,
    LoadingSession,
    SessionDirection,
    SessionStatus,
    StreamSource,
)


class Base(DeclarativeBase):
    pass


class BayRow(Base):
    __tablename__ = "bays"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    site_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(120))
    height_m: Mapped[float] = mapped_column(Float)
    width_m: Mapped[float] = mapped_column(Float)


class CameraRow(Base):
    __tablename__ = "cameras"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    bay_id: Mapped[UUID] = mapped_column(ForeignKey("bays.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32))
    stream_path: Mapped[str] = mapped_column(String(255), unique=True)
    # Camera credentials live in here: sealed by SecretBox before it reaches the row.
    source_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionRow(Base):
    __tablename__ = "loading_sessions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    bay_id: Mapped[UUID] = mapped_column(ForeignKey("bays.id"), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    plate: Mapped[str | None] = mapped_column(String(16), index=True)
    ai_count: Mapped[int] = mapped_column(Integer)
    manual_count: Mapped[int | None] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _session_to_domain(r: SessionRow) -> LoadingSession:
    return LoadingSession(
        id=r.id,
        bay_id=r.bay_id,
        direction=SessionDirection(r.direction),
        status=SessionStatus(r.status),
        plate=r.plate,
        ai_count=r.ai_count,
        manual_count=r.manual_count,
        opened_at=r.opened_at,
        closed_at=r.closed_at,
    )


def _camera_to_domain(r: CameraRow, box: SecretBox) -> Camera:
    return Camera(
        id=r.id,
        bay_id=r.bay_id,
        name=r.name,
        role=CameraRole(r.role),
        stream_path=r.stream_path,
        status=CameraStatus(r.status),
        last_seen_at=r.last_seen_at,
        source=StreamSource(box.open(r.source_url)),
    )


def _camera_values(c: Camera, box: SecretBox) -> dict:
    return {
        "id": c.id,
        "bay_id": c.bay_id,
        "name": c.name,
        "role": c.role.value,
        "stream_path": c.stream_path,
        "source_url": box.seal(c.source.url),
        "status": c.status.value,
        "last_seen_at": c.last_seen_at,
    }


class PostgresBayRepository:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def get(self, bay_id: UUID) -> Bay | None:
        async with self._sm() as db:
            r = await db.get(BayRow, bay_id)
            return Bay(r.id, r.site_id, r.name, r.height_m, r.width_m) if r else None

    async def list_all(self) -> list[Bay]:
        async with self._sm() as db:
            rows = (await db.scalars(select(BayRow).order_by(BayRow.name))).all()
            return [Bay(r.id, r.site_id, r.name, r.height_m, r.width_m) for r in rows]


class PostgresCameraRepository:
    def __init__(self, sm: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self._sm = sm
        self._box = box

    async def get(self, camera_id: UUID) -> Camera | None:
        async with self._sm() as db:
            r = await db.get(CameraRow, camera_id)
            return _camera_to_domain(r, self._box) if r else None

    async def list_for_bay(self, bay_id: UUID) -> list[Camera]:
        async with self._sm() as db:
            stmt = select(CameraRow).where(CameraRow.bay_id == bay_id).order_by(CameraRow.name)
            return [_camera_to_domain(r, self._box) for r in (await db.scalars(stmt)).all()]

    async def get_by_stream_path(self, stream_path: str) -> Camera | None:
        async with self._sm() as db:
            stmt = select(CameraRow).where(CameraRow.stream_path == stream_path)
            r = (await db.scalars(stmt)).first()
            return _camera_to_domain(r, self._box) if r else None

    async def delete(self, camera_id: UUID) -> None:
        async with self._sm.begin() as db:
            await db.execute(delete(CameraRow).where(CameraRow.id == camera_id))

    async def save(self, camera: Camera) -> None:
        values = _camera_values(camera, self._box)
        stmt = insert(CameraRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[CameraRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)


class PostgresSessionRepository:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def get(self, session_id: UUID) -> LoadingSession | None:
        async with self._sm() as db:
            r = await db.get(SessionRow, session_id)
            return _session_to_domain(r) if r else None

    async def get_open_for_bay(self, bay_id: UUID) -> LoadingSession | None:
        stmt = (
            select(SessionRow)
            .where(SessionRow.bay_id == bay_id, SessionRow.status == SessionStatus.OPEN.value)
            .order_by(SessionRow.opened_at.desc())
            .limit(1)
        )
        async with self._sm() as db:
            r = (await db.scalars(stmt)).first()
            return _session_to_domain(r) if r else None

    async def list_recent(
        self,
        *,
        bay_id: UUID | None = None,
        status: SessionStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[LoadingSession]:
        stmt = select(SessionRow).order_by(SessionRow.opened_at.desc()).limit(limit)
        if bay_id is not None:
            stmt = stmt.where(SessionRow.bay_id == bay_id)
        if status is not None:
            stmt = stmt.where(SessionRow.status == status.value)
        if since is not None:
            stmt = stmt.where(SessionRow.opened_at >= since)
        async with self._sm() as db:
            return [_session_to_domain(r) for r in (await db.scalars(stmt)).all()]

    async def save(self, session: LoadingSession) -> None:
        values = {
            "id": session.id,
            "bay_id": session.bay_id,
            "direction": session.direction.value,
            "status": session.status.value,
            "plate": session.plate,
            "ai_count": session.ai_count,
            "manual_count": session.manual_count,
            "opened_at": session.opened_at,
            "closed_at": session.closed_at,
        }
        stmt = insert(SessionRow).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[SessionRow.id], set_=values)
        async with self._sm.begin() as db:
            await db.execute(stmt)


async def build_postgres_repositories(
    url: str, seed: tuple[Bay, list[Camera]] | None, box: SecretBox
) -> tuple[
    PostgresBayRepository,
    PostgresCameraRepository,
    PostgresSessionRepository,
    Callable[[], Awaitable[None]],
]:
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as conn:
        # POC convenience; production schema changes go through Alembic migrations.
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    if seed is not None:
        bay, cameras = seed
        async with sm.begin() as db:
            await db.execute(
                insert(BayRow)
                .values(
                    id=bay.id,
                    site_id=bay.site_id,
                    name=bay.name,
                    height_m=bay.height_m,
                    width_m=bay.width_m,
                )
                .on_conflict_do_nothing()
            )
            for cam in cameras:
                await db.execute(
                    insert(CameraRow).values(**_camera_values(cam, box)).on_conflict_do_nothing()
                )

    return (
        PostgresBayRepository(sm),
        PostgresCameraRepository(sm, box),
        PostgresSessionRepository(sm),
        engine.dispose,
    )
