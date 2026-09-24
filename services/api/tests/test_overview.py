"""The dashboard's figures. These drive what an operator sees and acts on, so the
series, the deltas and the insights are all pinned to known data here.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ivaas.adapters.persistence.memory import (
    InMemoryBayRepository,
    InMemoryCameraRepository,
    InMemorySessionRepository,
)
from ivaas.application.overview import OperationsOverview, Severity
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

NOW = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)


class FrozenClock:
    def now(self) -> datetime:
        return NOW


def session(
    *,
    days_ago: int = 0,
    ai: int = 10,
    manual: int | None = None,
    status: SessionStatus = SessionStatus.CLOSED,
    plate: str | None = "ABC 1234",
    bay_id=None,
) -> LoadingSession:
    return LoadingSession(
        bay_id=bay_id or uuid4(),
        direction=SessionDirection.LOADING,
        opened_at=NOW - timedelta(days=days_ago),
        plate=plate,
        status=status,
        ai_count=ai,
        manual_count=manual,
    )


def camera(name: str, status: CameraStatus) -> Camera:
    return Camera(
        uuid4(),
        uuid4(),  # rebound to the bay in build()
        name,
        CameraRole.CHOKEPOINT,
        f"bay/{name}",
        source=StreamSource(None),
        status=status,
    )


async def build(sessions: list[LoadingSession], cameras: list[Camera] | None = None):
    bay = Bay(id=uuid4(), site_id=uuid4(), name="Bay 16")
    store = InMemorySessionRepository()
    for s in sessions:
        s.bay_id = bay.id
        await store.save(s)
    for c in cameras or []:
        c.bay_id = bay.id
    cams = InMemoryCameraRepository(cameras or [])
    return OperationsOverview(store, cams, InMemoryBayRepository([bay]), FrozenClock())


async def test_daily_series_has_one_point_per_day_including_quiet_days():
    overview = await build([session(days_ago=0, ai=12), session(days_ago=3, ai=8)])
    result = await overview(days=7)

    assert len(result.daily) == 7
    assert [d.crates for d in result.daily] == [0, 0, 0, 8, 0, 0, 12]
    assert result.daily[-1].day == NOW.date()
    assert result.crates.series == [0, 0, 0, 8, 0, 0, 12]


async def test_today_counts_only_todays_sessions():
    overview = await build([session(days_ago=0, ai=12), session(days_ago=1, ai=100)])
    result = await overview(days=7)

    assert result.crates_today == 12
    assert result.sessions_today == 1
    assert result.crates.value == 112  # the trend total spans the window


async def test_delta_compares_recent_half_of_window_with_the_half_before():
    # 20 crates a day in the recent half, 10 a day in the prior half: +100%.
    rows = [session(days_ago=d, ai=20) for d in range(0, 3)]
    rows += [session(days_ago=d, ai=10) for d in range(3, 6)]
    result = await (await build(rows))(days=6)

    assert result.crates.delta_pct == pytest.approx(1.0)


async def test_delta_is_undefined_against_an_empty_baseline():
    result = await (await build([session(days_ago=0, ai=20)]))(days=6)
    assert result.crates.delta_pct is None


async def test_accuracy_delta_is_reported_in_points_not_percent():
    rows = [
        session(days_ago=0, ai=10, manual=10, status=SessionStatus.RECONCILED),  # 100%
        session(days_ago=3, ai=8, manual=10, status=SessionStatus.RECONCILED),  # 80%
    ]
    result = await (await build(rows))(days=4)

    assert result.mean_accuracy == pytest.approx(0.9)
    assert result.accuracy.delta_pct == pytest.approx(0.2)  # 1.00 - 0.80


async def test_offline_cameras_raise_an_insight_that_names_them():
    overview = await build(
        [],
        [camera("Chokepoint", CameraStatus.ONLINE), camera("Door 3", CameraStatus.OFFLINE)],
    )
    result = await overview(days=7)

    insight = next(i for i in result.insights if i.key == "cameras_offline")
    assert insight.severity is Severity.WARN
    assert "Door 3" in insight.detail
    assert insight.metric == "1/2"


async def test_every_camera_offline_is_critical_not_a_warning():
    overview = await build([], [camera("Door 3", CameraStatus.OFFLINE)])
    insight = next(i for i in (await overview(days=7)).insights if i.key == "cameras_offline")
    assert insight.severity is Severity.CRITICAL


async def test_no_camera_insight_when_all_are_streaming():
    overview = await build([], [camera("Chokepoint", CameraStatus.ONLINE)])
    assert not [i for i in (await overview(days=7)).insights if i.key == "cameras_offline"]


async def test_accuracy_below_target_is_critical_and_above_it_is_good():
    low = await (await build([session(ai=8, manual=10, status=SessionStatus.RECONCILED)]))(days=7)
    assert next(i for i in low.insights if i.key == "accuracy_below_target").severity is (
        Severity.CRITICAL
    )

    high = await (await build([session(ai=10, manual=10, status=SessionStatus.RECONCILED)]))(days=7)
    assert next(i for i in high.insights if i.key == "accuracy_on_target").severity is Severity.GOOD


async def test_disputed_loads_report_their_net_variance():
    rows = [session(ai=12, manual=10, status=SessionStatus.DISPUTED)]
    insight = next(i for i in (await (await build(rows))(days=7)).insights if i.key == "disputed")
    assert insight.metric == "+2"


async def test_loads_without_a_plate_are_flagged_but_open_ones_are_not():
    rows = [
        session(plate=None, status=SessionStatus.CLOSED),
        session(plate=None, status=SessionStatus.OPEN),
    ]
    insight = next(
        i for i in (await (await build(rows))(days=7)).insights if i.key == "missing_plates"
    )
    assert insight.metric == "1"


async def test_insights_are_ordered_worst_first():
    rows = [
        session(ai=8, manual=10, status=SessionStatus.RECONCILED),  # critical: below target
        session(status=SessionStatus.CLOSED),  # info: awaiting count
        session(ai=12, manual=10, status=SessionStatus.DISPUTED),  # warn
    ]
    severities = [i.severity for i in (await (await build(rows))(days=7)).insights]
    assert severities == sorted(
        severities,
        key=lambda s: [Severity.CRITICAL, Severity.WARN, Severity.INFO, Severity.GOOD].index(s),
    )


async def test_window_is_clamped_to_a_sane_range():
    overview = await build([])
    assert (await overview(days=1)).days == 2
    assert (await overview(days=10_000)).days == 90
