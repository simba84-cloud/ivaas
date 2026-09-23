"""One behaviour suite for every repository, run against BOTH backends.

The in-memory fakes define the contract the use cases rely on; Postgres has to match
it exactly. Every test below is parametrised over the two, so a divergence fails once
per backend and names it. Postgres comes from Testcontainers and gets the real Alembic
migrations, so the schema under test is the schema that ships.

Run with `-m "not postgres"` to skip the container (no Docker available).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from ivaas.adapters.persistence.memory import (
    InMemoryBayRepository,
    InMemoryCameraRepository,
    InMemorySessionRepository,
)
from ivaas.adapters.persistence.secrets import SecretBox
from ivaas.domain.analysis import AnalysisJob, DetectedLoad, JobStatus, TimelineEvent
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

pytestmark = pytest.mark.asyncio

BAY = Bay(id=UUID("0bc39dce-7ea1-5331-b0dc-4ffcd94bbfd3"), site_id=uuid4(), name="Bay")


# --- backends ----------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_url():
    if os.environ.get("IVAAS_TEST_DATABASE_URL"):
        yield os.environ["IVAAS_TEST_DATABASE_URL"]
        return
    from testcontainers.community.postgres import PostgresContainer

    # Colima/rootless Docker cannot run the ryuk reaper sidecar; the context manager
    # removes the container itself anyway.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
        host, port = pg.get_container_host_ip(), pg.get_exposed_port(5432)
        yield f"postgresql+asyncpg://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"


@pytest_asyncio.fixture
async def backend(request):
    """-> (bays, cameras, sessions, jobs) for the requested backend, empty."""
    if request.param == "memory":
        import tempfile

        from ivaas.adapters.persistence.jobs import PersistentJobStore
        from ivaas.adapters.storage.objects import LocalObjectStore

        tmp = tempfile.mkdtemp()
        yield (
            InMemoryBayRepository([BAY]),
            InMemoryCameraRepository(),
            InMemorySessionRepository(),
            PersistentJobStore(LocalObjectStore(tmp)),
        )
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.jobs_postgres import PostgresJobStore
    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")  # only started for postgres runs
    bays, cameras, sessions, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:  # each test starts clean
        for table in ("analysis_jobs", "loading_sessions", "cameras"):
            await db.execute(text(f"DELETE FROM {table}"))
    yield bays, cameras, sessions, PostgresJobStore(sm)
    await dispose()


both = pytest.mark.parametrize(
    "backend", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)


# --- cameras -------------------------------------------------------------------


def cam(name="Cam", role=CameraRole.OVERHEAD, url="rtsp://admin:pw@10.0.0.1/s"):
    return Camera(uuid4(), BAY.id, name, role, f"bay/{name.lower()}", source=StreamSource(url))


@both
async def test_camera_round_trip_keeps_credentials_and_status(backend):
    _, cameras, _, _ = backend
    c = cam()
    c.mark_seen(datetime(2026, 9, 23, 8, 0, tzinfo=UTC))
    await cameras.save(c)
    got = await cameras.get(c.id)
    assert got.source.url == "rtsp://admin:pw@10.0.0.1/s"  # decrypted on the way out
    assert got.status is CameraStatus.ONLINE and got.last_seen_at == c.last_seen_at
    assert (await cameras.get_by_stream_path("bay/cam")).id == c.id
    assert [x.id for x in await cameras.list_for_bay(BAY.id)] == [c.id]


@both
async def test_camera_save_is_an_upsert_and_delete_is_idempotent(backend):
    _, cameras, _, _ = backend
    c = cam()
    await cameras.save(c)
    c.status = CameraStatus.OFFLINE
    await cameras.save(c)
    assert len(await cameras.list_for_bay(BAY.id)) == 1
    assert (await cameras.get(c.id)).status is CameraStatus.OFFLINE
    await cameras.delete(c.id)
    await cameras.delete(c.id)
    assert await cameras.get(c.id) is None


# --- sessions ------------------------------------------------------------------


def session(**over):
    s = LoadingSession(
        bay_id=BAY.id, direction=SessionDirection.LOADING, opened_at=datetime.now(UTC)
    )
    for k, v in over.items():
        setattr(s, k, v)
    return s


@both
async def test_session_round_trip_including_plate_last_seen(backend):
    _, _, sessions, _ = backend
    s = session(plate="ABC 1234", ai_count=27, plate_last_seen_at=datetime.now(UTC))
    await sessions.save(s)
    got = await sessions.get(s.id)
    assert (got.plate, got.ai_count, got.status) == ("ABC 1234", 27, SessionStatus.OPEN)
    assert got.plate_last_seen_at == s.plate_last_seen_at  # the column that broke prod
    assert (await sessions.get_open_for_bay(BAY.id)).id == s.id


@both
async def test_list_recent_filters_and_orders_newest_first(backend):
    _, _, sessions, _ = backend
    t0 = datetime.now(UTC)
    old = session(opened_at=t0 - timedelta(days=3), status=SessionStatus.CLOSED)
    new = session(opened_at=t0 - timedelta(hours=1))
    await sessions.save(old)
    await sessions.save(new)
    rows = await sessions.list_recent()
    assert [r.id for r in rows] == [new.id, old.id]
    assert [r.id for r in await sessions.list_recent(status=SessionStatus.CLOSED)] == [old.id]
    assert [r.id for r in await sessions.list_recent(since=t0 - timedelta(days=1))] == [new.id]
    assert len(await sessions.list_recent(limit=1)) == 1
    assert await sessions.get_open_for_bay(uuid4()) is None


# --- analysis jobs -------------------------------------------------------------


def job(**over):
    j = AnalysisJob(BAY.id, "clip.mp4", "uploads/x/clip.mp4", "op", datetime.now(UTC))
    for k, v in over.items():
        setattr(j, k, v)
    return j


@both
async def test_job_round_trip_with_loads_and_timeline(backend):
    _, _, _, jobs = backend
    j = job()
    j.finish(
        datetime.now(UTC),
        [DetectedLoad(10, 40, 2, 27, 1, "ABC 1234")],
        [TimelineEvent(12, "stack_counted", "Stack of 14 crates", "frames/x/a.jpg")],
        60.0,
    )
    j.summary = "Two stacks."
    await jobs.save(j)
    got = await jobs.get(j.id)
    assert got.status is JobStatus.DONE and got.total_crates == 27
    assert got.loads[0].plate == "ABC 1234" and got.timeline[0].frame_key == "frames/x/a.jpg"
    assert got.summary == "Two stacks." and got.duration_s == 60.0
    assert [x.id for x in await jobs.list_recent()] == [j.id]


@both
async def test_next_queued_is_oldest_first_and_skips_finished(backend):
    _, _, _, jobs = backend
    t0 = datetime.now(UTC)
    done = job(created_at=t0 - timedelta(hours=2))
    done.finish(t0, [], [], 1.0)
    second = job(created_at=t0 - timedelta(minutes=1))
    first = job(created_at=t0 - timedelta(minutes=5))
    for j in (done, second, first):
        await jobs.save(j)
    assert (await jobs.next_queued()).id == first.id
    first.start(t0)
    await jobs.save(first)
    nxt = await jobs.next_queued()
    assert nxt is None or nxt.id == second.id  # a running job is never handed out twice


@both
async def test_interrupted_job_is_requeued_on_reload(backend):
    _, _, _, jobs = backend
    j = job()
    j.start(datetime.now(UTC))
    j.progress = 0.6
    await jobs.save(j)
    # simulate a fresh process: drop any in-memory cache the store keeps
    if hasattr(jobs, "_live"):
        jobs._live.clear()
    if hasattr(jobs, "_jobs"):
        jobs._jobs.clear()
        await jobs.load_all()
    got = await jobs.get(j.id)
    assert got.status is JobStatus.QUEUED and got.progress == 0.0
