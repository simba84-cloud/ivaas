"""Excel workbooks (.xlsx) for the sheet uploads: tally sheets, manifests, the fleet
register. Each sheet is turned into the CSV text the importers already read, so an
Excel upload is checked by exactly the same rules, with the same messages, as a CSV.

Cells are written the way the importers read them: a date as 2026-10-12, a time as
06:40, a date with a time as 2026-10-12 06:40, a whole number without Excel's ".0".
Formulas give the value Excel last calculated (a workbook never opened in Excel has
none, and the cell reads as empty).

Which sheet: by name where the format has one (the tally workbook's "Entry - Sheets"
and "Entry - Stacks"), else the first sheet whose header row has the columns the
import needs, so an instructions or lookup sheet in front is passed over.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime, time
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

XLSX_MAGIC = b"PK\x03\x04"  # an .xlsx is a zip
XLS_MAGIC = b"\xd0\xcf\x11\xe0"  # the old binary .xls
MAX_XLSX_BYTES = 10 * 1024 * 1024
#: beyond this a sheet is not a day's paperwork: refuse rather than fill memory
MAX_ROWS = 50_000
XLSX_TYPES = ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SpreadsheetError(ValueError):
    pass


def is_xlsx(raw: bytes) -> bool:
    return raw[:4] == XLSX_MAGIC


def refuse_old_xls(raw: bytes) -> None:
    if raw[:4] == XLS_MAGIC:
        raise SpreadsheetError(
            "this is an old .xls workbook: save it as .xlsx or CSV and upload that"
        )


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.time() == time(0, 0):
            return value.date().isoformat()
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S" if value.second else "%H:%M")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value).strip()


def _headers(ws) -> set[str]:
    first = next(ws.iter_rows(max_row=1, values_only=True), ())
    return {_cell(v).lower() for v in first if v is not None}


def _open(raw: bytes):
    try:
        return load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError) as exc:
        raise SpreadsheetError(
            "this is not a readable Excel workbook (.xlsx): save it again from Excel, or as CSV"
        ) from exc


def _as_csv(ws) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for n, row in enumerate(ws.iter_rows(values_only=True)):
        if n >= MAX_ROWS:
            raise SpreadsheetError(
                f"'{ws.title}' has more than {MAX_ROWS:,} rows: is it the right file?"
            )
        cells = [_cell(v) for v in row]
        while cells and cells[-1] == "":
            cells.pop()
        writer.writerow(cells)
    return out.getvalue()


def sheet_csv(raw: bytes, *, name: str | None = None, needs: set[str] | None = None) -> str:
    """One sheet of a workbook as CSV text: the sheet called `name` (any case), else
    the first whose header row has every column in `needs`, else the first sheet."""
    wb = _open(raw)
    try:
        sheets = list(wb.worksheets)
        if name:
            for ws in sheets:
                if ws.title.strip().lower() == name.lower():
                    return _as_csv(ws)
        if needs:
            for ws in sheets:
                if needs <= _headers(ws):
                    return _as_csv(ws)
            wanted = ", ".join(sorted(needs))
            titles = ", ".join(f"'{ws.title}'" for ws in sheets)
            where = f"a sheet called '{name}' or " if name else ""
            raise SpreadsheetError(
                f"no sheet in this workbook has the columns {wanted} "
                f"({where}first row of headers); its sheets are {titles}"
            )
        if not sheets:
            raise SpreadsheetError("this workbook has no sheets")
        return _as_csv(sheets[0])
    finally:
        wb.close()


def has_sheet(raw: bytes, name: str) -> bool:
    wb = _open(raw)
    try:
        return any(ws.title.strip().lower() == name.lower() for ws in wb.worksheets)
    finally:
        wb.close()
