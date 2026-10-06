"""Daily reports over HTTP: the filed ones, and any day on demand (proposal M6)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

# named apart from the poc_report route below, which would otherwise shadow it
from ivaas.adapters import poc_report as poc_files
from ivaas.adapters.branding import filename
from ivaas.adapters.http.auth import require
from ivaas.adapters.http.media import files
from ivaas.adapters.reports import to_csv, to_pdf, to_xlsx
from ivaas.domain.poc import PocReport
from ivaas.domain.rbac import Permission as P

Audit = Callable[..., Awaitable[None]]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _file(body: bytes, media: str, name: str) -> Response:
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


class FiledReportOut(BaseModel):
    site_id: UUID
    site: str
    day: date
    loads: int
    generated_at: datetime
    #: signed, short-lived: a download link cannot carry a bearer token
    pdf_url: str
    csv_url: str
    #: reports filed before workbooks were filed have none
    xlsx_url: str | None = None
    #: what to save the files as: liquid-ivaas-<site>-<day>, then the extension
    file_stem: str


class CriterionOut(BaseModel):
    name: str
    result: Literal["pass", "fail", "not measured"]
    figure: str
    target: str
    how: str
    notes: list[str]


class NodeUptimeOut(BaseModel):
    name: str
    measured_from: datetime
    measured_to: datetime
    uptime_pct: float | None
    outages: int
    down_minutes: float


class ExceptionCountOut(BaseModel):
    kind: str
    status: str
    count: int


class PocReportOut(BaseModel):
    site: str
    start: date
    end: date
    timezone: str
    generated_at: datetime
    #: pass only when every criterion was measured and met; incomplete when any was not
    verdict: Literal["pass", "fail", "incomplete"]
    criteria: list[CriterionOut]
    loads: int
    dispatched: int
    returned: int
    outstanding: int
    still_at_the_bay: int
    corrections: int
    correction_crates: int
    #: only when a crate value was given: the crates not yet back, priced
    outstanding_value: float | None
    currency: str
    exceptions: list[ExceptionCountOut]
    nodes: list[NodeUptimeOut]

    @classmethod
    def of(cls, r: PocReport) -> PocReportOut:
        return cls(
            site=r.site,
            start=r.start,
            end=r.end,
            timezone=r.timezone,
            generated_at=r.generated_at,
            verdict=r.verdict,
            criteria=[CriterionOut(**vars(c)) for c in r.criteria],
            loads=len(r.loads),
            dispatched=r.balance.dispatched,
            returned=r.balance.returned,
            outstanding=r.balance.outstanding,
            still_at_the_bay=r.balance.in_progress,
            corrections=r.corrections,
            correction_crates=r.correction_crates,
            outstanding_value=r.outstanding_value,
            currency=r.currency,
            exceptions=[
                ExceptionCountOut(kind=k, status=s, count=n)
                for (k, s), n in sorted(r.exceptions.items())
            ],
            nodes=[
                NodeUptimeOut(
                    name=n.name,
                    measured_from=n.availability.start,
                    measured_to=n.availability.end,
                    uptime_pct=None
                    if n.availability.uptime is None
                    else round(n.availability.uptime * 100, 2),
                    outages=len(n.availability.outages),
                    down_minutes=round(n.availability.down_minutes, 1),
                )
                for n in r.nodes
            ],
        )


def add_report_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    @app.get(
        "/api/v1/reports",
        response_model=list[FiledReportOut],
        dependencies=[Depends(require(P.REPORT_EXPORT))],
    )
    async def filed_reports(
        days: int = 30, c: Any = Depends(get_container)
    ) -> list[FiledReportOut]:
        """Reports filed each morning for the day before, newest first."""
        names = {s.id: s.name for s in await c.sites.list_all()}
        since = (c.clock.now() - timedelta(days=max(1, min(days, 400)))).date()
        return [
            FiledReportOut(
                site_id=r.site_id,
                site=names.get(r.site_id, ""),
                day=r.day,
                loads=r.loads,
                generated_at=r.generated_at,
                pdf_url=c.signer.sign(r.pdf_key),
                csv_url=c.signer.sign(r.csv_key),
                xlsx_url=c.signer.sign(r.xlsx_key) if r.xlsx_key else None,
                file_stem=filename(names.get(r.site_id, ""), r.day.isoformat(), ext="")[:-1],
            )
            for r in await c.reports.since(since)
        ]

    @app.get(
        "/api/v1/reports/poc",
        response_model=PocReportOut,
        dependencies=[Depends(require(P.REPORT_EXPORT))],
        responses={200: {"content": {"application/pdf": {}, "text/csv": {}, XLSX: {}}}},
    )
    async def poc_report(
        site_id: UUID,
        start: date,
        end: date,
        format: Literal["json", "pdf", "csv", "xlsx"] = "json",
        uptime_target: float = Query(99.0, gt=0, le=100, description="percent"),
        baseline_minutes: float | None = Query(None, gt=0, description="loading cycle before"),
        crate_value: float | None = Query(None, ge=0),
        currency: str = Query("USD", min_length=3, max_length=3),
        c: Any = Depends(get_container),
    ) -> Any:
        """The POC's acceptance criteria, measured over the site's own days from start
        to end. A criterion the records cannot support is reported as not measured."""
        try:
            report = await (await c.build_poc_report_uc())(
                site_id,
                start,
                end,
                uptime_target=uptime_target / 100,
                baseline_minutes=baseline_minutes,
                crate_value=crate_value,
                currency=currency.upper(),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if format == "json":
            return PocReportOut.of(report)
        name = filename(report.site, "poc", str(start), "to", str(end), ext=format)
        if format == "csv":
            return _file(poc_files.to_csv(report), "text/csv", name)
        if format == "xlsx":
            return _file(poc_files.to_xlsx(report), XLSX, name)
        return _file(poc_files.to_pdf(report), "application/pdf", name)

    @app.get(
        "/api/v1/reports/daily",
        dependencies=[Depends(require(P.REPORT_EXPORT))],
        **files("The day's report", "application/pdf", "text/csv", XLSX),
    )
    async def daily_report(
        site_id: UUID,
        day: date,
        format: Literal["pdf", "csv", "xlsx"] = "pdf",
        c: Any = Depends(get_container),
    ) -> Response:
        """Any day's report, built now. Today's is as far as the day has got, and the
        report says when it was generated."""
        report = await (await c.build_daily_report_uc())(site_id, day)
        name = filename(report.site, day.isoformat(), ext=format)
        if format == "csv":
            return _file(to_csv(report), "text/csv", name)
        if format == "xlsx":
            return _file(to_xlsx(report), XLSX, name)
        return _file(to_pdf(report), "application/pdf", name)
