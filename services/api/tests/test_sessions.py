from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ivaas.adapters.persistence.memory import (
    InMemoryBayRepository,
    InMemoryEventPublisher,
    InMemorySessionRepository,
    SystemClock,
)
from ivaas.application.sessions import (
    CloseIdleSessions,
    CloseSession,
    OpenSession,
    ReconcileSession,
    RecordCrateCrossing,
    RecordPlateRead,
)
from ivaas.domain.models import (
    Bay,
    CrateCrossing,
    NotFoundError,
    PlateRead,
    SessionClosedError,
    SessionDirection,
    SessionStatus,
    SessionStillOpenError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def ctx():
    bay = Bay(id=uuid4(), site_id=uuid4(), name="Bay 16")
    return {
        "bay": bay,
        "bays": InMemoryBayRepository([bay]),
        "sessions": InMemorySessionRepository(),
        "events": InMemoryEventPublisher(),
        "clock": SystemClock(),
    }


def crossing(direction=SessionDirection.LOADING, track_id=1, crates=1):
    return CrateCrossing(
        track_id=track_id,
        camera_id=uuid4(),
        direction=direction,
        confidence=0.9,
        crossed_at=datetime.now(UTC),
        crates=crates,
    )


async def open_session(ctx):
    return await OpenSession(ctx["bays"], ctx["sessions"], ctx["events"], ctx["clock"])(
        ctx["bay"].id, SessionDirection.LOADING
    )


async def test_open_is_idempotent_per_bay(ctx):
    first = await open_session(ctx)
    second = await open_session(ctx)
    assert first.id == second.id
    assert len(ctx["events"].published) == 1


async def test_open_unknown_bay_raises(ctx):
    use_case = OpenSession(ctx["bays"], ctx["sessions"], ctx["events"], ctx["clock"])
    with pytest.raises(NotFoundError):
        await use_case(uuid4(), SessionDirection.LOADING)


async def test_crossings_count_and_reverse_crossings_undo(ctx):
    session = await open_session(ctx)
    record = RecordCrateCrossing(ctx["sessions"], ctx["events"])
    for i in range(5):
        await record(ctx["bay"].id, crossing(track_id=i))
    await record(ctx["bay"].id, crossing(SessionDirection.OFFLOADING, track_id=99))
    assert session.ai_count == 4


async def test_count_never_goes_negative(ctx):
    session = await open_session(ctx)
    record = RecordCrateCrossing(ctx["sessions"], ctx["events"])
    await record(ctx["bay"].id, crossing(SessionDirection.OFFLOADING))
    assert session.ai_count == 0


async def test_crossing_without_open_session_is_dropped(ctx):
    record = RecordCrateCrossing(ctx["sessions"], ctx["events"])
    assert await record(ctx["bay"].id, crossing()) is None


async def test_plate_is_linked_to_open_session(ctx):
    session = await open_session(ctx)
    read = PlateRead("ABE 2437", 0.97, uuid4(), datetime.now(UTC))
    await RecordPlateRead(ctx["sessions"], ctx["events"])(ctx["bay"].id, read)
    assert session.plate == "ABE 2437"


async def test_reconcile_within_tolerance(ctx):
    session = await open_session(ctx)
    record = RecordCrateCrossing(ctx["sessions"], ctx["events"])
    for i in range(97):
        await record(ctx["bay"].id, crossing(track_id=i))
    await CloseSession(ctx["sessions"], ctx["events"], ctx["clock"])(session.id)
    await ReconcileSession(ctx["sessions"], ctx["events"])(session.id, manual_count=100)
    assert session.status is SessionStatus.RECONCILED
    assert session.variance == -3
    assert session.accuracy == pytest.approx(0.97)


async def test_reconcile_outside_tolerance_is_disputed(ctx):
    session = await open_session(ctx)
    record = RecordCrateCrossing(ctx["sessions"], ctx["events"])
    for i in range(80):
        await record(ctx["bay"].id, crossing(track_id=i))
    await CloseSession(ctx["sessions"], ctx["events"], ctx["clock"])(session.id)
    await ReconcileSession(ctx["sessions"], ctx["events"])(session.id, manual_count=100)
    assert session.status is SessionStatus.DISPUTED


async def test_cannot_reconcile_open_session(ctx):
    session = await open_session(ctx)
    with pytest.raises(SessionStillOpenError):
        await ReconcileSession(ctx["sessions"], ctx["events"])(session.id, manual_count=10)


async def test_cannot_count_into_closed_session(ctx):
    session = await open_session(ctx)
    await CloseSession(ctx["sessions"], ctx["events"], ctx["clock"])(session.id)
    with pytest.raises(SessionClosedError):
        session.record_crossing(crossing())


async def test_a_stack_crossing_adds_all_its_crates_and_reversal_floors_at_zero(ctx):
    session = await open_session(ctx)
    record = RecordCrateCrossing(ctx["sessions"], ctx["events"])
    await record(ctx["bay"].id, crossing(crates=14))
    await record(ctx["bay"].id, crossing(crates=13, track_id=2))
    assert session.ai_count == 27
    await record(ctx["bay"].id, crossing(SessionDirection.OFFLOADING, track_id=3, crates=14))
    assert session.ai_count == 13
    await record(ctx["bay"].id, crossing(SessionDirection.OFFLOADING, track_id=4, crates=30))
    assert session.ai_count == 0


async def test_crossing_must_carry_at_least_one_crate():
    with pytest.raises(ValueError):
        crossing(crates=0)


async def test_plate_at_idle_bay_opens_a_session(ctx):
    opener = OpenSession(ctx["bays"], ctx["sessions"], ctx["events"], ctx["clock"])
    record = RecordPlateRead(ctx["sessions"], ctx["events"], auto_open=opener)
    read = PlateRead("ABC 1234", 0.96, uuid4(), datetime.now(UTC))
    session = await record(ctx["bay"].id, read)
    assert session is not None and session.plate == "ABC 1234"
    assert session.status is SessionStatus.OPEN and session.direction is SessionDirection.LOADING
    assert await ctx["sessions"].get_open_for_bay(ctx["bay"].id) is session


async def test_second_plate_does_not_hijack_the_truck_being_loaded(ctx):
    opener = OpenSession(ctx["bays"], ctx["sessions"], ctx["events"], ctx["clock"])
    record = RecordPlateRead(ctx["sessions"], ctx["events"], auto_open=opener)
    first = await record(ctx["bay"].id, PlateRead("ABC 1234", 0.96, uuid4(), datetime.now(UTC)))
    again = await record(ctx["bay"].id, PlateRead("ABD 5670", 0.96, uuid4(), datetime.now(UTC)))
    assert again is first and first.plate == "ABC 1234"


async def test_plate_without_auto_open_is_dropped_at_idle_bay(ctx):
    record = RecordPlateRead(ctx["sessions"], ctx["events"])
    assert (
        await record(ctx["bay"].id, PlateRead("ABC 1234", 0.96, uuid4(), datetime.now(UTC))) is None
    )


class FixedClock:
    def __init__(self, at):
        self.at = at

    def now(self):
        return self.at


async def test_idle_session_closes_after_the_truck_stops_being_seen(ctx):
    t0 = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
    clock = FixedClock(t0)
    opener = OpenSession(ctx["bays"], ctx["sessions"], ctx["events"], clock)
    record = RecordPlateRead(ctx["sessions"], ctx["events"], auto_open=opener)
    sweep = CloseIdleSessions(
        ctx["sessions"], ctx["events"], clock, idle_after=timedelta(minutes=10)
    )

    session = await record(ctx["bay"].id, PlateRead("ABC 1234", 0.96, uuid4(), t0))
    clock.at = t0 + timedelta(minutes=8)
    await record(ctx["bay"].id, PlateRead("ABC 1234", 0.96, uuid4(), clock.at))  # still parked
    clock.at = t0 + timedelta(minutes=17)
    assert await sweep() == []  # last seen 9 min ago: not yet
    clock.at = t0 + timedelta(minutes=19)
    assert [s.id for s in await sweep()] == [session.id]
    assert session.status is SessionStatus.CLOSED and session.closed_at == clock.at


async def test_manually_opened_session_without_a_plate_is_never_auto_closed(ctx):
    t0 = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
    clock = FixedClock(t0)
    await OpenSession(ctx["bays"], ctx["sessions"], ctx["events"], clock)(
        ctx["bay"].id, SessionDirection.LOADING
    )
    clock.at = t0 + timedelta(hours=5)
    assert await CloseIdleSessions(ctx["sessions"], ctx["events"], clock)() == []
