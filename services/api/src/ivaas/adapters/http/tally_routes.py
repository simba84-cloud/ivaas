"""HTTP routes for tally sheets: the paper counts the AI is judged against.

Who may do what:
    operator  enter sheets (CSV or form), see what was entered and whether it matched.
              Nothing here shows an operator the AI count: entry stays blind.
    viewer    the accuracy report, sheet against AI, once a sheet has reconciled
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.schemas import (
    TallyImportOut,
    TallyRematchOut,
    TallyReportOut,
    TallyReportRow,
    TallySheetIn,
    TallySheetOut,
)
from ivaas.adapters.spreadsheet import (
    MAX_XLSX_BYTES,
    SpreadsheetError,
    has_sheet,
    is_xlsx,
    refuse_old_xls,
    sheet_csv,
)
from ivaas.adapters.tally_csv import decode, parse_upload
from ivaas.domain.audit import AuditAction
from ivaas.domain.platform_settings import RECONCILE_TOLERANCE
from ivaas.domain.rbac import Permission as P
from ivaas.domain.tally import (
    InvalidTallySheetError,
    TallyLine,
    TallySheet,
    TallyStatus,
    parse_direction,
)
from ivaas.ports.auth import Principal

if TYPE_CHECKING:
    from ivaas.config.container import Container

Audit = Callable[..., Awaitable[None]]
MAX_CSV_BYTES = 2 * 1024 * 1024
#: listed in an import error before "and N more"
MAX_ERRORS_SHOWN = 15


def add_tally_routes(
    app: FastAPI, get_container: Callable[[Request], Container], audit: Audit
) -> None:
    async def _bay_or_404(c: Container, bay_id: UUID) -> None:
        if await c.bays.get(bay_id) is None:
            raise HTTPException(404, f"bay {bay_id} not found")

    async def _save(
        c: Container, principal: Principal, sheets: list[TallySheet]
    ) -> list[TallySheet]:
        saved = await (await c.save_tally_sheets_uc())(sheets)
        for s in saved:
            await audit(
                c,
                principal.name,
                AuditAction.TALLY_SHEET_SAVED,
                f"{s.sheet_id} ({s.plate})",
                lines=len(s.lines),
                truth=s.truth,
                total_on_paper=s.total_on_paper,
                transcription_mismatch=s.transcription_mismatch or None,
                outcome=s.status.value,
                session_id=str(s.session_id) if s.session_id else None,
            )
            if s.status is TallyStatus.CONFLICT:
                await audit(
                    c,
                    principal.name,
                    AuditAction.TALLY_CONFLICT,
                    f"{s.sheet_id} ({s.plate})",
                    truth=s.truth,
                    session_id=str(s.session_id),
                )
        return saved

    async def _raw(upload: UploadFile | None) -> bytes | None:
        if upload is None:
            return None
        raw = await upload.read(MAX_XLSX_BYTES + 1)
        limit = MAX_XLSX_BYTES if is_xlsx(raw) else MAX_CSV_BYTES
        if len(raw) > limit:
            raise HTTPException(
                413, f"{upload.filename} is larger than {limit // 2**20} MB; is it the right file?"
            )
        return raw

    def _text(raw: bytes | None, sheet: str, needs: set[str]) -> str | None:
        """CSV as it was, or that sheet of an Excel workbook as the same CSV text."""
        if raw is None:
            return None
        try:
            if is_xlsx(raw):
                return sheet_csv(raw, name=sheet, needs=needs)
            refuse_old_xls(raw)
        except SpreadsheetError as exc:
            raise HTTPException(422, str(exc)) from exc
        return decode(raw)

    @app.post(
        "/api/v1/tally/import",
        response_model=TallyImportOut,
        dependencies=[Depends(require(P.GROUNDTRUTH_ENTER))],
    )
    async def import_tally(
        bay_id: UUID,
        sheets: UploadFile = File(...),
        stacks: UploadFile | None = File(None),
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> TallyImportOut:
        await _bay_or_404(c, bay_id)
        sheets_raw, stacks_raw = await _raw(sheets), await _raw(stacks)
        sheets_csv = _text(sheets_raw, "Entry - Sheets", {"sheet_id", "date", "direction"})
        if stacks_raw is None and sheets_raw is not None and is_xlsx(sheets_raw):
            # the whole tally workbook, uploaded once: its stacks are in the same file
            if has_sheet(sheets_raw, "Entry - Stacks"):
                stacks_raw = sheets_raw
        parsed = parse_upload(
            sheets_csv or "",
            _text(stacks_raw, "Entry - Stacks", {"sheet_id", "crates"}),
            bay_id=bay_id,
            entered_by_user=principal.name,
            entered_at=c.clock.now(),
        )
        if parsed.errors:
            shown = parsed.errors[:MAX_ERRORS_SHOWN]
            more = len(parsed.errors) - len(shown)
            detail = (
                "Nothing was imported. " + "; ".join(shown) + (f"; and {more} more" if more else "")
            )
            raise HTTPException(422, detail)
        if not parsed.sheets and parsed.skipped:
            raise HTTPException(
                422, "Only the EXAMPLE rows are filled in: add each sheet's row below them."
            )
        if not parsed.sheets:
            raise HTTPException(
                422, "No sheets found. Is this the tally workbook, or its 'Entry - Sheets' CSV?"
            )
        saved = await _save(c, principal, parsed.sheets)
        return TallyImportOut(saved=[TallySheetOut.of(s) for s in saved], skipped=parsed.skipped)

    @app.post(
        "/api/v1/tally/sheets",
        response_model=TallySheetOut,
        status_code=201,
        dependencies=[Depends(require(P.GROUNDTRUTH_ENTER))],
    )
    async def enter_tally(
        body: TallySheetIn,
        principal: Principal = Depends(current_principal),
        c: Container = Depends(get_container),
    ) -> TallySheetOut:
        await _bay_or_404(c, body.bay_id)
        try:
            sheet = TallySheet(
                sheet_id=body.sheet_id,
                bay_id=body.bay_id,
                date=body.date,
                plate=body.plate,
                direction=parse_direction(body.direction),
                start_time=body.start_time,
                end_time=body.end_time,
                lines=[TallyLine(ln.line_no, ln.crates, ln.note) for ln in body.lines],
                total_on_paper=body.total_on_paper,
                pages=body.pages,
                counted_by=body.counted_by,
                verified_by=body.verified_by,
                entered_by=body.entered_by,
                notes=body.notes,
                entered_by_user=principal.name,
                entered_at=c.clock.now(),
            )
        except InvalidTallySheetError as exc:
            raise HTTPException(422, str(exc)) from exc
        (saved,) = await _save(c, principal, [sheet])
        return TallySheetOut.of(saved)

    @app.get(
        "/api/v1/tally/sheets",
        response_model=list[TallySheetOut],
        dependencies=[Depends(require(P.GROUNDTRUTH_ENTER))],
    )
    async def list_tally(
        limit: int = 200, c: Container = Depends(get_container)
    ) -> list[TallySheetOut]:
        return [TallySheetOut.of(s) for s in await c.tally.list_recent(limit=limit)]

    @app.post(
        "/api/v1/tally/rematch",
        response_model=TallyRematchOut,
        dependencies=[Depends(require(P.GROUNDTRUTH_ENTER))],
    )
    async def rematch_tally(c: Container = Depends(get_container)) -> TallyRematchOut:
        changed = await (await c.rematch_tally_sheets_uc())()
        return TallyRematchOut(changed=[TallySheetOut.of(s) for s in changed])

    @app.get(
        "/api/v1/tally/report",
        response_model=TallyReportOut,
        dependencies=[Depends(require(P.COUNT_READ))],
    )
    async def tally_report(
        limit: int = 500, c: Container = Depends(get_container)
    ) -> TallyReportOut:
        target = float(await c.effective(RECONCILE_TOLERANCE, c.settings.reconcile_tolerance))
        rows: list[TallyReportRow] = []
        ai_sum = truth_sum = 0
        scored: list[float] = []
        for sheet in await c.tally.list_recent(limit=limit):
            session = await c.sessions.get(sheet.session_id) if sheet.session_id else None
            # a reconciled sheet's figure *is* the session's manual count, so the session's
            # own accuracy is this sheet's; one formula, in the domain
            reconciled = (
                sheet.status is TallyStatus.RECONCILED
                and session is not None
                and session.manual_count == sheet.truth
            )
            accuracy = session.accuracy if reconciled else None
            variance = session.variance if reconciled else None
            if accuracy is not None:
                scored.append(accuracy)
                ai_sum += session.ai_count
                truth_sum += session.manual_count
            rows.append(
                TallyReportRow(
                    sheet=TallySheetOut.of(sheet),
                    session_id=session.id if session else None,
                    session_status=session.status if session else None,
                    ai_count=session.ai_count if reconciled else None,
                    variance=variance,
                    accuracy=accuracy,
                    passed=None if accuracy is None else accuracy >= target,
                )
            )
        return TallyReportOut(
            target=target,
            sheets=len(rows),
            reconciled=len(scored),
            passing=sum(1 for a in scored if a >= target),
            mean_accuracy=sum(scored) / len(scored) if scored else None,
            aggregate_error=abs(ai_sum - truth_sum) / truth_sum if truth_sum else None,
            rows=rows,
        )
