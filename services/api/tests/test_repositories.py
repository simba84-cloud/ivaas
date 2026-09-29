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
    Site,
    StreamSource,
)

pytestmark = pytest.mark.asyncio

SITE = Site(id=uuid4(), name="Test Site")
BAY = Bay(id=UUID("0bc39dce-7ea1-5331-b0dc-4ffcd94bbfd3"), site_id=SITE.id, name="Bay")


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
    _sites, bays, cameras, sessions, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
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
async def test_interrupted_job_is_recovered(backend):
    """A job whose worker died must be picked up again, without disturbing live ones.

    The JSON store recovers on reload, because it only ever has one process. The
    Postgres store cannot assume that: a RUNNING row usually means a worker is busy
    with it right now, so it waits for the heartbeat to go quiet instead.
    """
    _, _, _, jobs = backend
    j = job()
    j.start(datetime.now(UTC))
    j.progress = 0.6
    await jobs.save(j)

    if hasattr(jobs, "_jobs"):  # JSON store: a fresh process reloads from storage
        jobs._jobs.clear()
        await jobs.load_all()
        got = await jobs.get(j.id)
        assert got.status is JobStatus.QUEUED and got.progress == 0.0
        return

    # Postgres: still running, so it stays running and is not handed to another worker
    assert (await jobs.get(j.id)).status is JobStatus.RUNNING
    assert await jobs.next_queued() is None

    jobs._stale_after = timedelta(seconds=0)  # the heartbeat has gone quiet
    reclaimed = await jobs.next_queued()
    assert reclaimed is not None and reclaimed.id == j.id
    assert reclaimed.progress == 0.0


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_job_state_is_visible_across_processes(request):
    """The API and the analysis worker are separate processes over one table.

    The store used to cache queued and running jobs in a dict, which was correct
    only while the worker ran inside the API. Two stores over the same database
    stand in for the two processes here.
    """
    from sqlalchemy import text

    from ivaas.adapters.persistence.jobs_postgres import PostgresJobStore
    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    url = request.getfixturevalue("postgres_url")
    _s, _b, _c, _se, dispose, sm = await build_postgres_repositories(
        url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM analysis_jobs"))

    api_side = PostgresJobStore(sm)
    worker_side = PostgresJobStore(sm)

    job = AnalysisJob(
        bay_id=BAY.id,
        filename="a.mp4",
        object_key="k",
        created_by="admin",
        created_at=datetime.now(UTC),
    )
    await api_side.save(job)  # the API queues it

    claimed = await worker_side.next_queued()  # the worker takes it
    assert claimed is not None
    claimed.start(datetime.now(UTC))
    claimed.progress = 0.5
    await worker_side.save(claimed)

    seen = await api_side.get(job.id)
    assert seen is not None
    assert seen.status is JobStatus.RUNNING, "the API must not serve its own stale copy"
    assert seen.progress == 0.5

    claimed.finish(datetime.now(UTC), loads=[], timeline=[], duration_s=1.0)
    await worker_side.save(claimed)
    done = await api_side.get(job.id)
    assert done is not None and done.status is JobStatus.DONE

    await dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_workers_never_claim_the_same_job(request):
    """docker compose up --scale worker=3 must not analyse one video three times."""
    from sqlalchemy import text

    from ivaas.adapters.persistence.jobs_postgres import PostgresJobStore
    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    url = request.getfixturevalue("postgres_url")
    *_, dispose, sm = await build_postgres_repositories(
        url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM analysis_jobs"))

    queued = [job(), job()]
    store = PostgresJobStore(sm)
    for j in queued:
        await store.save(j)

    # three workers race for two jobs
    workers = [PostgresJobStore(sm) for _ in range(3)]
    claimed = [await w.next_queued() for w in workers]

    got = [c.id for c in claimed if c is not None]
    assert len(got) == 2, "each queued job should be claimed exactly once"
    assert len(set(got)) == 2, "the same job was handed to two workers"
    assert sorted(got) == sorted(j.id for j in queued)

    await dispose()


# --- alert acknowledgements ------------------------------------------------------


@pytest_asyncio.fixture
async def acks(request):
    """-> an empty acknowledgement store for the requested backend."""
    from ivaas.adapters.persistence.alerts_postgres import (
        InMemoryAcknowledgementStore,
        PostgresAcknowledgementStore,
    )

    if request.param == "memory":
        yield InMemoryAcknowledgementStore()
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")
    *_, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM alert_acknowledgements"))
    yield PostgresAcknowledgementStore(sm)
    await dispose()


both_acks = pytest.mark.parametrize(
    "acks", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)


@both_acks
async def test_first_acknowledgement_is_kept_and_listed_newest_first(acks):
    from ivaas.domain.alerts import AlertAcknowledgement

    t0 = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
    first = AlertAcknowledgement("cam-a@never", "admin", t0, "on it")
    assert await acks.add(first) == first
    later = AlertAcknowledgement("cam-a@never", "operator", t0 + timedelta(minutes=5))
    assert await acks.add(later) == first  # the first stands
    await acks.add(AlertAcknowledgement("disputed-x", "operator", t0 + timedelta(hours=1)))

    listed = await acks.list_since(t0 - timedelta(days=1))
    assert [a.key for a in listed] == ["disputed-x", "cam-a@never"]
    assert await acks.list_since(t0 + timedelta(minutes=30)) == [listed[0]]
    assert await acks.get("nothing") is None


# --- site security ---------------------------------------------------------------


@pytest_asyncio.fixture
async def security(request):
    """-> (zones, incidents, badges, people, raw) for the requested backend, empty.
    `raw` reads a person's stored embedding column, or None in memory."""
    from ivaas.adapters.persistence import security_postgres as sp

    if request.param == "memory":
        yield (
            sp.InMemoryZoneStore(),
            sp.InMemoryIncidentStore(),
            sp.InMemoryBadgeLog(),
            sp.InMemoryPeopleStore(),
            None,
        )
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")
    box = SecretBox([SecretBox.generate_key()])
    *_, dispose, sm = await build_postgres_repositories(postgres_url, seed=(SITE, BAY, []), box=box)
    async with sm.begin() as db:
        for table in ("security_zones", "security_incidents", "badge_events", "enrolled_people"):
            await db.execute(text(f"DELETE FROM {table}"))

    async def raw():
        async with sm() as db:
            return (await db.execute(text("SELECT embedding FROM enrolled_people"))).scalar_one()

    yield (
        sp.PostgresZoneStore(sm),
        sp.PostgresIncidentStore(sm),
        sp.PostgresBadgeLog(sm),
        sp.PostgresPeopleStore(sm, box),
        raw,
    )
    await dispose()


both_security = pytest.mark.parametrize(
    "security", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)


@both_security
async def test_zones_incidents_badges_and_people_round_trip(security):
    from ivaas.domain.security import (
        BadgeEvent,
        EnrolledPerson,
        Incident,
        IncidentKind,
        IncidentStatus,
        Window,
        Zone,
        ZoneRule,
    )

    zones, incidents, badges, people, raw = security
    cam = uuid4()
    t0 = datetime(2026, 9, 28, 22, 0, tzinfo=UTC)

    zone = Zone(
        cam,
        "Store",
        ((0.1, 0.1), (0.9, 0.1), (0.5, 0.9)),
        frozenset({ZoneRule.BADGE, ZoneRule.INTRUSION}),
        (Window((0, 1), "22:00", "05:00"),),
        5.0,
        False,
        "Store door",
    )
    await zones.save(zone)
    assert await zones.get(zone.id) == zone
    assert await zones.for_cameras([cam]) == [zone]
    assert await zones.for_cameras([uuid4()]) == []

    i = Incident(
        BAY.id,
        cam,
        IncidentKind.INTRUSION,
        t0,
        0.9,
        zone.id,
        "Store",
        "incidents/x.jpg",
        {"track_id": 3},
    )
    await incidents.save(i)
    i.acknowledge("operator", t0 + timedelta(minutes=1))
    await incidents.save(i)
    stored = await incidents.get(i.id)
    assert stored.status is IncidentStatus.ACKNOWLEDGED and stored.detail == {"track_id": 3}
    assert await incidents.list(status=IncidentStatus.OPEN) == []
    assert [x.id for x in await incidents.list(bay_id=BAY.id, kind=IncidentKind.INTRUSION)] == [
        i.id
    ]

    await badges.add(BadgeEvent("B1", "Store door", t0 - timedelta(minutes=2), True, "T. Moyo"))
    await badges.add(BadgeEvent("B2", "Store door", t0 - timedelta(hours=2), True))
    assert [e.badge_id for e in await badges.between(t0 - timedelta(minutes=10), t0)] == ["B1"]

    person = EnrolledPerson(
        "Tendai Moyo", "E-1042", "HR/118", "admin", t0, tuple(0.25 * (k % 7) for k in range(128))
    )
    await people.save(person)
    [back] = await people.list()
    assert back.embedding == person.embedding  # float32 round trip of exact values
    if raw is not None:
        stored_text = await raw()
        assert stored_text.startswith("enc:")  # sealed: the table alone recognises nobody
    assert (await people.delete(person.id)).name == "Tendai Moyo"
    assert await people.list() == []
