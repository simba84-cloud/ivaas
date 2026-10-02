"""One behaviour suite for every repository, run against BOTH backends.

The in-memory fakes define the contract the use cases rely on; Postgres has to match
it exactly. Every test below is parametrised over the two, so a divergence fails once
per backend and names it. Postgres comes from Testcontainers and gets the real Alembic
migrations, so the schema under test is the schema that ships.

Run with `-m "not postgres"` to skip the container (no Docker available).
"""

from __future__ import annotations

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
from ivaas.domain.tenancy import BAKERS_INN_ID
from ivaas.tenancy import tenant_context

pytestmark = pytest.mark.asyncio

SITE = Site(id=uuid4(), name="Test Site")
BAY = Bay(id=UUID("0bc39dce-7ea1-5331-b0dc-4ffcd94bbfd3"), site_id=SITE.id, name="Bay")


# --- backends ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def in_a_tenant():
    """Repositories hold one tenant's rows; the contract is exercised inside Bakers Inn."""
    with tenant_context(BAKERS_INN_ID):
        yield


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
        # tally sheets reference sessions, so they go first
        for table in (
            "tally_lines",
            "tally_sheets",
            "analysis_jobs",
            "loading_sessions",
            "cameras",
        ):
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


# --- tally sheets --------------------------------------------------------------


@pytest_asyncio.fixture
async def tally(request):
    """-> (tally store, session repository) for the requested backend, both empty."""
    from ivaas.adapters.persistence.tally_postgres import (
        InMemoryTallySheetStore,
        PostgresTallySheetStore,
    )

    if request.param == "memory":
        yield InMemoryTallySheetStore(), InMemorySessionRepository()
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")
    *_, sessions, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:
        for table in ("tally_lines", "tally_sheets"):
            await db.execute(text(f"DELETE FROM {table}"))
    yield PostgresTallySheetStore(sm), sessions
    await dispose()


both_tally = pytest.mark.parametrize(
    "tally", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)


def tally_sheet(sheet_id="BI-20261012-B1-001", **over):
    from datetime import date, time

    from ivaas.domain.tally import TallyLine, TallySheet

    fields = {
        "sheet_id": sheet_id,
        "bay_id": BAY.id,
        "date": date(2026, 10, 12),
        "plate": "AGA 5372",
        "direction": SessionDirection.LOADING,
        "start_time": time(6, 40),
        "end_time": time(7, 5),
        "lines": [TallyLine(2, 30), TallyLine(1, 32, "P")],
        "total_on_paper": 62,
        "counted_by": "R. Ncube",
        "entered_by_user": "operator",
        "entered_at": datetime(2026, 10, 13, 7, 0, tzinfo=UTC),
    }
    return TallySheet(**{**fields, **over})


@both_tally
async def test_tally_sheet_round_trip_with_its_lines(tally):
    from ivaas.domain.tally import TallyStatus

    store, sessions = tally
    s = session()
    await sessions.save(s)
    sheet = tally_sheet(session_id=s.id, status=TallyStatus.MATCHED)
    await store.save(sheet)
    got = await store.get_by_sheet_id(sheet.sheet_id)
    assert got.id == sheet.id and got.session_id == s.id and got.status is TallyStatus.MATCHED
    assert [(ln.line_no, ln.crates, ln.note) for ln in got.lines] == [(1, 32, "P"), (2, 30, None)]
    assert (got.date, got.start_time, got.total_on_paper) == (sheet.date, sheet.start_time, 62)
    assert got.direction is SessionDirection.LOADING and got.counted_by == "R. Ncube"
    assert await store.session_ids_taken() == {s.id}
    assert await store.get_by_sheet_id("BI-nope") is None


@both_tally
async def test_tally_save_is_an_upsert_by_sheet_id_that_replaces_lines(tally):
    from ivaas.domain.tally import TallyLine

    store, _ = tally
    first = tally_sheet()
    await store.save(first)
    again = tally_sheet(lines=[TallyLine(1, 40)], total_on_paper=40)  # a fresh object, new id
    await store.save(again)
    assert again.id == first.id  # the stored row keeps its first id
    (only,) = await store.list_recent()
    assert only.id == first.id and [ln.crates for ln in only.lines] == [40]


@both_tally
async def test_tally_lists_newest_first_and_unresolved_oldest_first(tally):
    from ivaas.domain.tally import TallyStatus

    store, _ = tally
    t0 = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)
    for i, status in enumerate(
        [TallyStatus.RECONCILED, TallyStatus.UNMATCHED, TallyStatus.CONFLICT, TallyStatus.PENDING]
    ):
        await store.save(
            tally_sheet(f"BI-{i}", status=status, entered_at=t0 + timedelta(minutes=i))
        )
    assert [s.sheet_id for s in await store.list_recent()] == ["BI-3", "BI-2", "BI-1", "BI-0"]
    assert [s.sheet_id for s in await store.list_recent(limit=2)] == ["BI-3", "BI-2"]
    assert [s.sheet_id for s in await store.list_unresolved()] == ["BI-1", "BI-3"]


# --- webhooks (T6.4) ---------------------------------------------------------------
@pytest_asyncio.fixture
async def hooks(request):
    """-> (store, sessionmaker or None) for the requested backend, empty."""
    from ivaas.adapters.persistence.webhooks_postgres import (
        InMemoryWebhookStore,
        PostgresWebhookStore,
    )

    if request.param == "memory":
        yield InMemoryWebhookStore(), None
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")
    *_, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    box = SecretBox([SecretBox.generate_key()])
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM webhook_deliveries"))
        await db.execute(text("DELETE FROM webhook_endpoints"))
    yield PostgresWebhookStore(sm, box), sm
    await dispose()


both_hooks = pytest.mark.parametrize(
    "hooks", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def endpoint(**over):
    from ivaas.domain.webhooks import WebhookEndpoint, new_secret

    fields = dict(
        url="https://erp.example.com/hooks",
        events=("session.closed",),
        secret=new_secret(),
        created_by="admin",
        created_at=T0,
    )
    return WebhookEndpoint(**{**fields, **over})


@both_hooks
async def test_webhook_endpoint_round_trip_with_the_secret_sealed(hooks):
    from sqlalchemy import text

    store, sm = hooks
    e = endpoint(events=("exception.raised", "session.closed"), description="ERP")
    await store.save_endpoint(e)
    (back,) = await store.endpoints()
    assert (back.id, back.url, back.events, back.secret) == (e.id, e.url, e.events, e.secret)
    if sm is not None:  # the column holds the sealed form, never the secret itself
        async with sm() as db:
            raw = (await db.execute(text("SELECT secret FROM webhook_endpoints"))).scalar_one()
        assert e.secret not in raw and raw


@both_hooks
async def test_deliveries_list_newest_first_and_go_with_their_endpoint(hooks):
    from ivaas.domain.webhooks import Delivery

    store, _ = hooks
    e = endpoint()
    await store.save_endpoint(e)
    for i in range(3):
        at = T0 + timedelta(minutes=i)
        await store.save_delivery(Delivery(e.id, uuid4(), "session.closed", {"n": i}, at))
    assert [d.payload["n"] for d in await store.deliveries(e.id)] == [2, 1, 0]
    assert await store.delete_endpoint(e.id) and await store.deliveries(e.id) == []
    assert not await store.delete_endpoint(e.id)


@both_hooks
async def test_a_claimed_delivery_is_not_due_again_until_its_lease_runs_out(hooks):
    from ivaas.domain.webhooks import LEASE_S, Delivery, DeliveryStatus

    store, _ = hooks
    e = endpoint()
    await store.save_endpoint(e)
    d = Delivery(e.id, uuid4(), "session.closed", {"x": 1}, T0)
    await store.save_delivery(d)
    assert [x.id for x in await store.claim_due(T0)] == [d.id]
    assert await store.claim_due(T0 + timedelta(seconds=LEASE_S - 1)) == []
    # the sender died without recording an outcome: it is sent again
    (again,) = await store.claim_due(T0 + timedelta(seconds=LEASE_S))
    again.succeeded(T0 + timedelta(seconds=LEASE_S + 1), 204)
    await store.save_delivery(again)
    assert await store.claim_due(T0 + timedelta(days=1)) == []
    assert (await store.delivery(d.id)).status is DeliveryStatus.DELIVERED


@pytest.mark.postgres
async def test_two_senders_never_claim_the_same_webhook_delivery(request):
    import asyncio

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories
    from ivaas.adapters.persistence.webhooks_postgres import PostgresWebhookStore
    from ivaas.domain.webhooks import Delivery

    url = request.getfixturevalue("postgres_url")
    *_, dispose, sm = await build_postgres_repositories(
        url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    box = SecretBox([SecretBox.generate_key()])
    one, two = PostgresWebhookStore(sm, box), PostgresWebhookStore(sm, box)
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM webhook_deliveries"))
        await db.execute(text("DELETE FROM webhook_endpoints"))
    e = endpoint()
    await one.save_endpoint(e)
    for i in range(10):
        await one.save_delivery(Delivery(e.id, uuid4(), "session.closed", {"n": i}, T0))
    a, b = await asyncio.gather(one.claim_due(T0, limit=10), two.claim_due(T0, limit=10))
    ids_a, ids_b = {d.id for d in a}, {d.id for d in b}
    assert ids_a.isdisjoint(ids_b) and len(ids_a | ids_b) == 10
    await dispose()


# --- edge availability (POC reliability) ----------------------------------------------
@pytest_asyncio.fixture
async def avail(request):
    """-> (store, node_id) for the requested backend, empty, with an enrolled node."""
    from ivaas.adapters.persistence.availability_postgres import (
        InMemoryAvailabilityStore,
        PostgresAvailabilityStore,
    )

    if request.param == "memory":
        yield InMemoryAvailabilityStore(), uuid4()
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.edge_postgres import PostgresEdgeStore
    from ivaas.adapters.persistence.postgres import build_postgres_repositories
    from ivaas.domain.edge import EdgeNode

    postgres_url = request.getfixturevalue("postgres_url")
    _s, _b, _c, _se, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM edge_availability"))
    node = EdgeNode(name="n", site_id=SITE.id, credential_hash="x", enrolled_at=T0)
    await PostgresEdgeStore(sm).save_node(node)  # the period rows reference it
    yield PostgresAvailabilityStore(sm), node.id
    await dispose()


both_avail = pytest.mark.parametrize(
    "avail", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)


@both_avail
async def test_availability_keeps_the_latest_period_per_subject_and_reads_a_window(avail):
    from ivaas.domain.availability import Period

    store, node = avail
    cam = uuid4()
    t = T0
    old_node = Period(node, None, True, t, t + timedelta(hours=1))
    new_node = Period(node, None, True, t + timedelta(hours=2), t + timedelta(hours=3))
    cam_down = Period(node, cam, False, t, t + timedelta(minutes=10))
    await store.save_all([old_node, new_node, cam_down])
    latest = await store.latest(node)
    assert set(latest) == {None, cam}
    assert latest[None].id == new_node.id and latest[cam].id == cam_down.id
    # extending a period is saving it again
    new_node.until = t + timedelta(hours=4)
    await store.save_all([new_node])
    assert (await store.latest(node))[None].until == t + timedelta(hours=4)
    window = await store.between(
        [node], t + timedelta(minutes=30), t + timedelta(hours=1, minutes=30)
    )
    assert {p.id for p in window} == {old_node.id}
    assert await store.between([], t, t + timedelta(days=1)) == []


# --- enrollment tokens, listed (M8 onboarding) --------------------------------------
@pytest_asyncio.fixture
async def edge(request):
    from ivaas.adapters.persistence.edge_postgres import InMemoryEdgeStore, PostgresEdgeStore

    if request.param == "memory":
        yield InMemoryEdgeStore()
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")
    _s, _b, _c, _se, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM edge_enrollment_tokens"))
    yield PostgresEdgeStore(sm)
    await dispose()


@pytest.mark.parametrize(
    "edge", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)
async def test_enrollment_tokens_are_listed_oldest_first_and_per_tenant(edge):
    from ivaas.domain.edge import EnrollmentToken
    from ivaas.domain.tenancy import ISOLATION_TEST_ID

    def issue(name, at):
        return EnrollmentToken.issue(
            site_id=SITE.id, bay_id=None, name=name, ttl=timedelta(hours=1), by="t", now=at
        )[0]

    later, first = issue("later", T0 + timedelta(minutes=5)), issue("first", T0)
    await edge.save_token(later)
    await edge.save_token(first)
    assert [t.name for t in await edge.list_tokens()] == ["first", "later"]
    with tenant_context(ISOLATION_TEST_ID):
        assert await edge.list_tokens() == []


# --- break-glass grants (M8) -----------------------------------------------------------
@pytest_asyncio.fixture
async def grants(request):
    from ivaas.adapters.persistence.break_glass_postgres import (
        InMemoryBreakGlassStore,
        PostgresBreakGlassStore,
    )

    if request.param == "memory":
        yield InMemoryBreakGlassStore()
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")
    _s, _b, _c, _se, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    async with sm.begin() as db:
        await db.execute(text("DELETE FROM break_glass_grants"))
    yield PostgresBreakGlassStore(sm)
    await dispose()


@pytest.mark.parametrize(
    "grants", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)
async def test_break_glass_grants_round_trip_and_are_found_across_tenants_only_by_the_system(
    grants,
):
    from ivaas.domain.break_glass import BreakGlassGrant, GrantState
    from ivaas.domain.tenancy import ISOLATION_TEST_ID
    from ivaas.tenancy import system_context

    old = BreakGlassGrant.request(BAKERS_INN_ID, "support", "why", timedelta(hours=1), T0)
    new = BreakGlassGrant.request(
        BAKERS_INN_ID, "support", "again", timedelta(minutes=15), T0 + timedelta(hours=2)
    )
    await grants.save(old)
    await grants.save(new)
    new.decide(True, "owner", T0 + timedelta(hours=3))
    await grants.save(new)
    got = await grants.get(new.id)
    assert got.duration == timedelta(minutes=15) and got.approved and got.decided_by == "owner"
    assert got.state(T0 + timedelta(hours=3, minutes=5)) is GrantState.ACTIVE
    assert [g.reason for g in await grants.list_all()] == ["again", "why"]
    with tenant_context(ISOLATION_TEST_ID):
        assert await grants.get(new.id) is None and await grants.list_all() == []
    with system_context():
        assert (await grants.get(old.id)).tenant_id == BAKERS_INN_ID


# --- billing (M7) ---------------------------------------------------------------------
@pytest_asyncio.fixture
async def billing(request):
    """-> (store, sessionmaker or None) for the requested backend, empty."""
    from ivaas.adapters.persistence.billing_postgres import (
        InMemoryBillingStore,
        PostgresBillingStore,
    )

    if request.param == "memory":
        yield InMemoryBillingStore(), None
        return

    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    postgres_url = request.getfixturevalue("postgres_url")
    *_, dispose, sm = await build_postgres_repositories(
        postgres_url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    # as the owner: the application's role may not delete from the ledger (below)
    from sqlalchemy.ext.asyncio import create_async_engine

    owner = create_async_engine(postgres_url)
    async with owner.begin() as db:
        for table in ("invoices", "usage_events", "subscriptions"):
            await db.execute(text(f"DELETE FROM {table}"))
    await owner.dispose()
    yield PostgresBillingStore(sm), sm
    await dispose()


both_billing = pytest.mark.parametrize(
    "billing", ["memory", pytest.param("postgres", marks=pytest.mark.postgres)], indirect=True
)


@both_billing
async def test_billing_subscription_ledger_and_invoices_round_trip(billing):
    from decimal import Decimal

    from ivaas.domain.billing import (
        OD,
        Invoice,
        Line,
        Segment,
        Subscription,
        UsageEvent,
    )

    store, _ = billing
    sub = Subscription(BAKERS_INN_ID, [Segment(T0, "standard", {OD: 16}, "platform")])
    await store.save_subscription(sub)
    sub.change(Segment(T0 + timedelta(days=10), "standard", {OD: 24}, "owner"))
    await store.save_subscription(sub)  # an upsert, not a second row
    back = await store.subscription()
    assert [s.quantities[OD] for s in back.segments] == [16, 24]

    e = UsageEvent("assistant_tokens", Decimal("1000.5"), T0, "spend:1")
    assert await store.record(e) is True
    assert await store.record(UsageEvent("assistant_tokens", Decimal(9), T0, "spend:1")) is False
    await store.record(
        UsageEvent("assistant_tokens", Decimal(2), T0 + timedelta(hours=1), "spend:2")
    )
    got = await store.usage(T0, T0 + timedelta(days=1))
    assert got == {"assistant_tokens": Decimal("1002.5")}

    first, second = await store.next_invoice_number(2026), await store.next_invoice_number(2026)
    assert first < second and first.startswith("IVAAS-2026-")
    inv = Invoice(
        BAKERS_INN_ID,
        T0.date(),
        T0.date(),
        "USD",
        [Line("ivaas-platform", "x", Decimal(1), Decimal("250.00"), Decimal("250.00"))],
        "VAT",
        Decimal("0.15"),
        "book",
        True,
        number=first,
        issued_at=T0,
    )
    await store.save_invoice(inv)
    [listed] = await store.invoices()
    assert listed.number == first and listed.total == Decimal("287.50") and listed.placeholder
    assert (await store.invoice_for(T0.date(), T0.date())).id == inv.id


@pytest.mark.postgres
async def test_the_usage_ledger_is_append_only_for_the_application(request):
    from sqlalchemy import text

    from ivaas.adapters.persistence.postgres import build_postgres_repositories

    url = request.getfixturevalue("postgres_url")
    *_, dispose, sm = await build_postgres_repositories(
        url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    try:
        for change in ("DELETE FROM usage_events", "UPDATE usage_events SET quantity = 0"):
            with pytest.raises(Exception, match="permission denied"):
                async with sm.begin() as db:  # the application's own sessions, as ivaas_app
                    await db.execute(text(change))
    finally:
        await dispose()


@both_billing
async def test_payments_are_saved_onto_the_issued_invoice(billing):
    from decimal import Decimal

    from ivaas.domain.billing import Invoice, Line, Payment

    store, _ = billing
    inv = Invoice(
        BAKERS_INN_ID,
        T0.date(),
        T0.date(),
        "USD",
        [Line("ivaas-platform", "x", Decimal(1), Decimal("100.00"), Decimal("100.00"))],
        "VAT",
        Decimal("0.15"),
        "book",
        True,
        number=await store.next_invoice_number(2026),
        issued_at=T0,
        due_date=T0.date(),
    )
    await store.save_invoice(inv)
    inv.pay(Payment(Decimal("115.00"), "BT-1", T0, "finance"))
    await store.save_invoice(inv)  # the same invoice, now paid: not a second one
    [back] = await store.invoices()
    assert back.settled and back.payments[0].reference == "BT-1"
    assert back.due_date == T0.date()
    assert (await store.invoice(inv.number)).id == inv.id


@pytest.mark.postgres
async def test_partner_invoices_and_holds_round_trip_on_postgres(request):
    from decimal import Decimal

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from ivaas.adapters.persistence.billing_postgres import PostgresPartnerInvoiceStore
    from ivaas.adapters.persistence.postgres import build_postgres_repositories
    from ivaas.adapters.persistence.tenants_postgres import PostgresTenantStore
    from ivaas.domain.billing import Line, PartnerInvoice, Payment, TenantPart
    from ivaas.domain.tenancy import LITZIM_ID
    from ivaas.tenancy import system_context

    url = request.getfixturevalue("postgres_url")
    *_, dispose, sm = await build_postgres_repositories(
        url, seed=(SITE, BAY, []), box=SecretBox([SecretBox.generate_key()])
    )
    owner = create_async_engine(url)
    async with owner.begin() as db:
        await db.execute(text("DELETE FROM partner_invoices"))
    await owner.dispose()
    try:
        with system_context():
            store, tenants = PostgresPartnerInvoiceStore(sm), PostgresTenantStore(sm)
            line = Line("ivaas-platform", "x", Decimal(1), Decimal("175.00"), Decimal("175.00"))
            inv = PartnerInvoice(
                LITZIM_ID,
                "LITZIM",
                T0.date(),
                T0.date(),
                "USD",
                [TenantPart(BAKERS_INN_ID, "Bakers Inn", [line])],
                "VAT",
                Decimal("0.15"),
                "book+wholesale-litzim",
                True,
                number="IVAAS-2026-990001",
                issued_at=T0,
                due_date=T0.date(),
            )
            await store.save(inv)
            inv.pay(Payment(inv.total, "LITZIM-1", T0, "finance"))
            await store.save(inv)
            [back] = await store.for_partner(LITZIM_ID)
            assert back.settled and back.parts[0].tenant_name == "Bakers Inn"
            assert back.total == Decimal("201.25")  # 175.00 + 15%
            assert (await store.by_number("IVAAS-2026-990001")).id == inv.id

            bakers = await tenants.get(BAKERS_INN_ID)
            bakers.on_hold = True
            await tenants.save(bakers)
            assert (await tenants.get(BAKERS_INN_ID)).on_hold
            bakers.on_hold = False
            await tenants.save(bakers)
    finally:
        await dispose()
