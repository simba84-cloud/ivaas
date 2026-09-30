"""Read the CSVs saved from the tally-sheet workbook (Bakers_Inn_Crate_Tally_Sheet_Template.xlsx).

`Entry - Sheets` has one row per paper sheet; `Entry - Stacks` one row per dolly or
stack line. Excel writes every pre-formatted row, so blank rows are skipped, and the
green EXAMPLE rows are skipped and reported. Any other problem rejects the upload
with the row it was found on: a half-imported day is harder to fix than a clean retry.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time
from uuid import UUID

from ivaas.domain.tally import (
    EXAMPLE_PREFIX,
    InvalidTallySheetError,
    TallyLine,
    TallySheet,
    parse_direction,
)

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%y")
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p")


@dataclass
class ParsedUpload:
    sheets: list[TallySheet] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def decode(raw: bytes) -> str:
    """Excel saves CSV as UTF-8 with a BOM, or in the Windows code page."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in reader]


def _date(value: str) -> date:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date '{value}' not recognised; use YYYY-MM-DD")


def _time(value: str) -> time | None:
    if not value:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value.upper(), fmt).time()
        except ValueError:
            pass
    raise ValueError(f"time '{value}' not recognised; use HH:MM")


def _int(value: str) -> int | None:
    if not value:
        return None
    number = float(value.replace(",", ""))
    if number != int(number):
        raise ValueError(f"'{value}' is not a whole number")
    return int(number)


def _is_example(sheet_id: str) -> bool:
    return sheet_id.upper().startswith(EXAMPLE_PREFIX)


def parse_upload(
    sheets_csv: str,
    stacks_csv: str | None,
    *,
    bay_id: UUID,
    entered_by_user: str,
    entered_at: datetime,
) -> ParsedUpload:
    out = ParsedUpload()
    rows: dict[str, tuple[int, dict[str, str]]] = {}
    for n, r in enumerate(_rows(sheets_csv), start=2):  # row 1 is the header
        sid = r.get("sheet_id", "")
        if not sid:
            continue
        if _is_example(sid):
            out.skipped.append(f"Sheets row {n}: {sid} is the example row")
            continue
        if sid in rows:
            out.errors.append(f"Sheets row {n}: {sid} appears twice (first on row {rows[sid][0]})")
            continue
        rows[sid] = (n, r)

    lines: dict[str, list[TallyLine]] = {sid: [] for sid in rows}
    if stacks_csv:
        for n, r in enumerate(_rows(stacks_csv), start=2):
            sid = r.get("sheet_id", "")
            if not sid or _is_example(sid):
                continue
            if sid not in lines:
                out.errors.append(f"Stacks row {n}: sheet {sid} is not in the sheets file")
                continue
            try:
                line_no, crates = _int(r.get("line_no", "")), _int(r.get("crates", ""))
                if line_no is None or crates is None:
                    raise ValueError("line_no and crates are both required")
                lines[sid].append(TallyLine(line_no, crates, r.get("note") or None))
            except ValueError as exc:
                out.errors.append(f"Stacks row {n} ({sid}): {exc}")

    for sid, (n, r) in rows.items():
        try:
            out.sheets.append(
                TallySheet(
                    sheet_id=sid,
                    bay_id=bay_id,
                    date=_date(r.get("date", "")),
                    plate=r.get("truck_plate", ""),
                    direction=parse_direction(r.get("direction", "")),
                    start_time=_time(r.get("start_time", "")),
                    end_time=_time(r.get("end_time", "")),
                    lines=lines[sid],
                    total_on_paper=_int(r.get("total_on_paper", "")),
                    pages=_int(r.get("pages", "")),
                    counted_by=r.get("counted_by") or None,
                    verified_by=r.get("verified_by") or None,
                    entered_by=r.get("entered_by") or None,
                    notes=r.get("notes") or None,
                    entered_by_user=entered_by_user,
                    entered_at=entered_at,
                )
            )
        except (ValueError, InvalidTallySheetError) as exc:
            out.errors.append(f"Sheets row {n} ({sid}): {exc}")
    return out
