"""Daily reports over HTTP: the filed ones, and any day on demand (proposal M6)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ivaas.adapters.http.auth import require
from ivaas.adapters.http.media import files
from ivaas.adapters.reports import to_csv, to_pdf
from ivaas.domain.rbac import Permission as P

Audit = Callable[..., Awaitable[None]]


class FiledReportOut(BaseModel):
    site_id: UUID
    site: str
    day: date
    loads: int
    generated_at: datetime
    #: signed, short-lived: a download link cannot carry a bearer token
    pdf_url: str
    csv_url: str


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
            )
            for r in await c.reports.since(since)
        ]

    @app.get(
        "/api/v1/reports/daily",
        dependencies=[Depends(require(P.REPORT_EXPORT))],
        **files("The day's report", "application/pdf", "text/csv"),
    )
    async def daily_report(
        site_id: UUID,
        day: date,
        format: Literal["pdf", "csv"] = "pdf",
        c: Any = Depends(get_container),
    ) -> Response:
        """Any day's report, built now. Today's is as far as the day has got, and the
        report says when it was generated."""
        report = await (await c.build_daily_report_uc())(site_id, day)
        name = f"{report.site or 'site'}-{day.isoformat()}".replace(" ", "-").lower()
        if format == "csv":
            body, media = to_csv(report), "text/csv"
        else:
            body, media = to_pdf(report), "application/pdf"
        return Response(
            body,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'},
        )
