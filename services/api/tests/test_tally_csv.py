"""The CSVs Excel writes when 'Entry - Sheets' and 'Entry - Stacks' are saved from
Bakers_Inn_Crate_Tally_Sheet_Template.xlsx: every column, the green EXAMPLE rows, and
hundreds of pre-formatted rows that are blank apart from their formulas."""

from datetime import UTC, date, datetime, time
from uuid import uuid4

from ivaas.adapters.tally_csv import decode, parse_upload
from ivaas.domain.models import SessionDirection

SHEETS_HEADER = (
    "sheet_id,date,bay,truck_plate,direction,start_time,end_time,route_driver,pages,"
    "stack_count,total_crates,total_on_paper,check,counted_by,verified_by,entered_by,notes"
)
STACKS_HEADER = "sheet_id,line_no,crates,note"
EXAMPLE = (
    "EXAMPLE-20261012-B1-001,2026-10-12,B1,AEX 4821,LOAD,06:40,07:05,Route 7,1,6,124,124,OK,"
    "R. Ncube,S. Dube,P. Chuma,Line 5: one dolly pulled back off the truck"
)
BLANK = ",,,,,,,,,,,,,,,,"


def sheets_csv(*rows: str) -> str:
    return "\n".join([SHEETS_HEADER, EXAMPLE, *rows, *[BLANK] * 5]) + "\n"


def stacks_csv(*rows: str) -> str:
    return "\n".join([STACKS_HEADER, "EXAMPLE-20261012-B1-001,1,32,", *rows, ",,,"]) + "\n"


def parse(sheets: str, stacks: str | None = None):
    return parse_upload(
        sheets,
        stacks,
        bay_id=uuid4(),
        entered_by_user="operator",
        entered_at=datetime(2026, 10, 13, 7, tzinfo=UTC),
    )


def test_reads_sheets_and_their_stack_lines():
    out = parse(
        sheets_csv(
            "BI-20261012-B1-001,2026-10-12,B1,AGA 5372,LOAD,06:40,07:05,,1,3,94,94,OK,"
            "R. Ncube,S. Dube,P. Chuma,",
            "BI-20261012-B1-002,12/10/2026,B1,AGC 4793,RETURN,7:30 AM,,,,,,60,,,,,",
        ),
        stacks_csv(
            "BI-20261012-B1-001,2,30,",
            "BI-20261012-B1-001,1,32,",
            "BI-20261012-B1-001,3,32,P",
        ),
    )
    assert out.errors == []
    assert out.skipped == ["Sheets row 2: EXAMPLE-20261012-B1-001 is the example row"]
    first, second = out.sheets
    assert first.sheet_id == "BI-20261012-B1-001"
    assert first.date == date(2026, 10, 12)
    assert first.direction is SessionDirection.LOADING
    assert (first.start_time, first.end_time) == (time(6, 40), time(7, 5))
    assert [ln.line_no for ln in first.lines] == [1, 2, 3]  # sorted, whatever the file order
    assert first.lines[2].note == "P"
    assert first.truth == 94 and not first.transcription_mismatch
    assert (first.counted_by, first.verified_by) == ("R. Ncube", "S. Dube")
    # d/m/Y dates, 12-hour times, and a sheet with a paper total but no typed lines
    assert second.date == date(2026, 10, 12)
    assert second.direction is SessionDirection.OFFLOADING
    assert second.start_time == time(7, 30) and second.end_time is None
    assert second.lines == [] and second.truth == 60


def test_stacks_file_is_optional():
    out = parse(sheets_csv("BI-1,2026-10-12,B1,AGA 5372,LOAD,06:40,07:05,,,,,124,,,,,"))
    assert out.errors == [] and out.sheets[0].truth == 124


def test_every_problem_is_reported_with_its_row():
    out = parse(
        sheets_csv(
            "BI-1,2026-13-45,B1,AGA 5372,LOAD,06:40,07:05,,,,,10,,,,,",
            "BI-2,2026-10-12,B1,AGA 5372,OUT,06:40,07:05,,,,,10,,,,,",
            "BI-3,2026-10-12,B1,,LOAD,06:40,07:05,,,,,10,,,,,",
            "BI-4,2026-10-12,B1,AGA 5372,LOAD,6.40,07:05,,,,,10,,,,,",
            "BI-4,2026-10-12,B1,AGA 5372,LOAD,06:40,07:05,,,,,10,,,,,",
        ),
        stacks_csv("BI-9,1,30,", "BI-2,1,3.5,"),
    )
    assert out.errors == [
        "Sheets row 7: BI-4 appears twice (first on row 6)",
        "Stacks row 3: sheet BI-9 is not in the sheets file",
        "Stacks row 4 (BI-2): '3.5' is not a whole number",
        "Sheets row 3 (BI-1): date '2026-13-45' not recognised; use YYYY-MM-DD",
        "Sheets row 4 (BI-2): direction must be LOAD or RETURN",
        "Sheets row 5 (BI-3): truck plate is required",
        "Sheets row 6 (BI-4): time '6.40' not recognised; use HH:MM",
    ]


def test_excel_encodings():
    assert decode("﻿sheet_id\n".encode()) == "sheet_id\n"
    assert decode("Moyo – ok".encode("cp1252")) == "Moyo – ok"
    header_with_bom = "﻿" + sheets_csv("BI-1,2026-10-12,B1,AGA 5372,LOAD,,,,,,,5,,,,,")
    assert parse(header_with_bom).sheets[0].sheet_id == "BI-1"
