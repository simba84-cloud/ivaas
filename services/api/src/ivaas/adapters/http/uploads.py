"""A sheet upload, CSV or Excel, as the CSV text the importers read."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile

from ivaas.adapters.spreadsheet import (
    MAX_XLSX_BYTES,
    SpreadsheetError,
    is_xlsx,
    refuse_old_xls,
    sheet_csv,
)


async def upload_text(
    upload: UploadFile, csv_limit: int, *, needs: set[str], sheet: str | None = None
) -> str:
    """The upload's rows as CSV text. A workbook's sheet is the one called `sheet`, or
    the first whose headers include `needs`. Too big is 413; unreadable is 422."""
    raw = await upload.read(MAX_XLSX_BYTES + 1)
    xlsx = is_xlsx(raw)
    limit = MAX_XLSX_BYTES if xlsx else csv_limit
    if len(raw) > limit:
        kind = "an Excel workbook" if xlsx else "a CSV"
        raise HTTPException(413, f"{kind} here is at most {limit // 2**20} MB")
    try:
        if xlsx:
            return sheet_csv(raw, name=sheet, needs=needs)
        refuse_old_xls(raw)
    except SpreadsheetError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "the file is not UTF-8 text, nor an Excel workbook") from exc
