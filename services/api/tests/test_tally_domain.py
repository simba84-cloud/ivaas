from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from ivaas.domain.models import LoadingSession, SessionDirection
from ivaas.domain.tally import (
    InvalidTallySheetError,
    TallyLine,
    TallySheet,
    match_session,
    parse_direction,
    plate_similarity,
)

HARARE = ZoneInfo("Africa/Harare")  # UTC+2, no daylight saving
BAY = uuid4()
DAY = date(2026, 10, 12)


def sheet(**kw) -> TallySheet:
    fields = {
        "sheet_id": "BI-20261012-B1-001",
        "bay_id": BAY,
        "date": DAY,
        "plate": "AGA 5372",
        "direction": SessionDirection.LOADING,
        "start_time": time(6, 40),
        "end_time": time(7, 5),
        "lines": [TallyLine(1, 32), TallyLine(2, 30)],
        "entered_by_user": "operator",
        "entered_at": datetime(2026, 10, 13, 7, 0, tzinfo=UTC),
    }
    return TallySheet(**{**fields, **kw})


def session(opened_local: time, closed_local: time | None, plate: str | None = "AGA 5372", **kw):
    opened = datetime.combine(DAY, opened_local, HARARE)
    closed = datetime.combine(DAY, closed_local, HARARE) if closed_local else None
    return LoadingSession(
        bay_id=kw.pop("bay_id", BAY),
        direction=kw.pop("direction", SessionDirection.LOADING),
        opened_at=opened,
        closed_at=closed,
        plate=plate,
        **kw,
    )


NOW = datetime.combine(DAY, time(12, 0), HARARE)


def match(s, candidates, **kw):
    return match_session(s, candidates, tz=HARARE, now=NOW, **kw)


# --- the sheet itself ------------------------------------------------------------


def test_truth_is_the_lines_when_typed_in_else_the_paper_total():
    assert sheet().truth == 62
    assert sheet(lines=[], total_on_paper=124).truth == 124


def test_a_stack_carried_back_off_is_a_negative_line():
    s = sheet(lines=[TallyLine(1, 32), TallyLine(2, 32), TallyLine(3, -32, "X")])
    assert s.truth == 32


def test_transcription_mismatch_when_lines_disagree_with_the_paper_total():
    assert sheet(total_on_paper=62).transcription_mismatch is False
    assert sheet(total_on_paper=64).transcription_mismatch is True
    assert sheet(lines=[], total_on_paper=64).transcription_mismatch is False


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"sheet_id": "EXAMPLE-20261012-B1-001"}, "example"),
        ({"sheet_id": "  "}, "sheet_id"),
        ({"plate": " - "}, "plate"),
        ({"lines": [], "total_on_paper": None}, "lines or a total"),
        ({"lines": [TallyLine(1, 5), TallyLine(1, 6)]}, "unique"),
        ({"lines": [TallyLine(1, -5)]}, "fewer than zero"),
    ],
)
def test_invalid_sheets_are_rejected(kw, message):
    with pytest.raises(InvalidTallySheetError, match=message):
        sheet(**kw)


def test_direction_words_from_the_paper_form():
    assert parse_direction(" load ") is SessionDirection.LOADING
    assert parse_direction("RETURN") is SessionDirection.OFFLOADING
    with pytest.raises(InvalidTallySheetError):
        parse_direction("OUT")


def test_window_is_local_time_and_crosses_midnight():
    start, end = sheet(start_time=time(23, 40), end_time=time(0, 20)).window(HARARE)
    assert start == datetime(2026, 10, 12, 21, 40, tzinfo=UTC)
    assert end == datetime(2026, 10, 12, 22, 20, tzinfo=UTC)


def test_plate_similarity_forgives_spacing_and_ocr_confusions():
    assert plate_similarity("AGA 5372", "aga5372") == 1.0
    assert plate_similarity("AGA 5372", "AGA S372") == 1.0  # S read for 5
    assert plate_similarity("AGA 5372", "AFY 2210") < 0.8
    assert plate_similarity("AGA 5372", None) == 0.0


# --- matching a sheet to its session ---------------------------------------------


def test_matches_the_session_at_the_same_time_and_plate():
    target = session(time(6, 38), time(7, 10))
    other = session(time(9, 0), time(9, 30))
    assert match(sheet(), [other, target]) is target


def test_tolerance_absorbs_clocks_that_disagree_by_a_few_minutes():
    near = session(time(7, 15), time(7, 40))  # starts 10 min after the sheet ends
    assert match(sheet(), [near]) is near
    assert match(sheet(), [session(time(7, 25), time(7, 40))]) is None


def test_open_session_is_measured_up_to_now():
    assert match(sheet(), [session(time(6, 30), None)]) is not None


def test_wrong_plate_bay_or_direction_never_matches():
    assert match(sheet(), [session(time(6, 40), time(7, 0), plate="AFY 2210")]) is None
    assert match(sheet(), [session(time(6, 40), time(7, 0), bay_id=uuid4())]) is None
    offloading = session(time(6, 40), time(7, 0), direction=SessionDirection.OFFLOADING)
    assert match(sheet(), [offloading]) is None


def test_session_without_a_plate_matches_on_time_but_loses_to_a_plate_match():
    unread = session(time(6, 30), time(7, 30), plate=None)
    read = session(time(6, 50), time(7, 0))
    assert match(sheet(), [unread]) is unread
    assert match(sheet(), [unread, read]) is read  # less overlap, but the plate agrees


def test_a_session_claimed_by_another_sheet_is_skipped():
    target = session(time(6, 38), time(7, 10))
    assert match(sheet(), [target], taken={target.id}) is None


def test_largest_overlap_wins_between_equal_plate_matches():
    short = session(time(6, 0), time(6, 45))
    long = session(time(6, 45), time(7, 30))
    assert match(sheet(), [short, long]) is long


def test_sessions_the_day_before_do_not_match():
    yesterday = session(time(6, 40), time(7, 5))
    yesterday.opened_at -= timedelta(days=1)
    yesterday.closed_at -= timedelta(days=1)
    assert match(sheet(), [yesterday]) is None
