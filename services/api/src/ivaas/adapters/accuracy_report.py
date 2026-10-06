"""The accuracy report (AI count against the tally sheets) as files: a PDF to read and a
workbook to work with, both under the Liquid brand (`branding`).

They render the report exactly as the API returns it (`TallyReportOut`, as JSON), so
the files and the Accuracy page cannot disagree. A sheet that reconciled no load has no
accuracy: it is listed apart and left out of the averages, never shown as 0%.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table

from ivaas.adapters import branding

HOW = (
    "Accuracy per load = 1 - |AI count - tally count| / tally count, measured on the AI "
    "count, never on a correction. Mean accuracy averages the loads; aggregate error is "
    "|sum of AI - sum of tallies| / sum of tallies, so it weights each load by its crates."
)
UNSCORED = (
    "A sheet without a counted load has no accuracy. It is listed here and left out of "
    "the averages rather than counted as 0%."
)


def _pct(v: float | None) -> str:
    return "" if v is None else f"{v * 100:.1f}%"


def _split(report: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    rows = report["rows"]
    return [r for r in rows if r["accuracy"] is not None], [
        r for r in rows if r["accuracy"] is None
    ]


def _result(r: dict) -> str:
    return "passed" if r["passed"] else "below target"


def _sub(tenant: str, generated_at: datetime, bay: str | None) -> str:
    where = f"{bay} only" if bay else "every bay"
    return (
        f"{tenant}, {where}. Generated {generated_at:%Y-%m-%d %H:%M} UTC from the "
        "platform's records."
    )


def to_pdf(
    report: dict[str, Any],
    *,
    tenant: str,
    bays: dict[str, str],
    generated_at: datetime,
    bay: str | None = None,
) -> bytes:
    styles = branding.pdf_styles()
    h1, h2, body = styles["h1"], styles["h2"], styles["body"]

    def table(rows: list[list[str]]) -> Table:
        t = Table(rows, repeatRows=1, hAlign="LEFT")
        t.setStyle(branding.GRID)
        return t

    buf = io.BytesIO()
    doc = branding.pdf_document(buf, landscape(A4), f"{tenant} accuracy report")
    scored, unscored = _split(report)
    story: list = [
        Paragraph("Accuracy against the tally sheets", h1),
        Paragraph(_sub(tenant, generated_at, bay), body),
        Spacer(1, 4 * mm),
        table(
            [
                [
                    "Tally sheets",
                    "Scored against a load",
                    "At or above target",
                    "Mean accuracy",
                    "Aggregate error",
                    "Target",
                ],
                [
                    str(report["sheets"]),
                    str(report["reconciled"]),
                    f"{report['passing']} of {report['reconciled']}",
                    _pct(report["mean_accuracy"]),
                    _pct(report["aggregate_error"]),
                    _pct(report["target"]),
                ],
            ]
        ),
        Spacer(1, 2 * mm),
        Paragraph(HOW, body),
        Paragraph("Scored loads", h2),
    ]
    if scored:
        story.append(
            table(
                [
                    [
                        "Sheet",
                        "Day",
                        "Bay",
                        "Truck",
                        "Direction",
                        "Tally",
                        "AI count",
                        "Variance",
                        "Accuracy",
                        "Result",
                        "Load",
                    ]
                ]
                + [
                    [
                        r["sheet"]["sheet_id"],
                        r["sheet"]["date"],
                        bays.get(r["sheet"]["bay_id"], ""),
                        r["sheet"]["plate"],
                        r["sheet"]["direction"],
                        f"{r['sheet']['truth']:,}",
                        f"{r['ai_count']:,}",
                        f"{r['variance']:+,}",
                        _pct(r["accuracy"]),
                        _result(r),
                        r["session_status"] or "",
                    ]
                    for r in scored
                ]
            )
        )
    else:
        story.append(
            Paragraph(
                "No sheet has been matched to a counted load yet, so there is no accuracy figure.",
                body,
            )
        )
    story.append(Paragraph("Not scored: no counted load to compare against", h2))
    if unscored:
        story.append(
            table(
                [["Sheet", "Day", "Bay", "Truck", "Direction", "Tally", "Status", "Typing check"]]
                + [
                    [
                        r["sheet"]["sheet_id"],
                        r["sheet"]["date"],
                        bays.get(r["sheet"]["bay_id"], ""),
                        r["sheet"]["plate"],
                        r["sheet"]["direction"],
                        "" if r["sheet"]["truth"] is None else f"{r['sheet']['truth']:,}",
                        r["sheet"]["status"],
                        "MISMATCH" if r["sheet"]["transcription_mismatch"] else "OK",
                    ]
                    for r in unscored
                ]
            )
        )
        story.append(Paragraph(UNSCORED, body))
    else:
        story.append(Paragraph("None.", body))
    page = branding.pdf_page("Accuracy report")
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()


def to_xlsx(
    report: dict[str, Any],
    *,
    tenant: str,
    bays: dict[str, str],
    generated_at: datetime,
    bay: str | None = None,
) -> bytes:
    wb = branding.workbook(f"{tenant} accuracy report")
    title, sub = "Accuracy against the tally sheets", _sub(tenant, generated_at, bay)
    scored, unscored = _split(report)

    s = branding.Sheet(wb, "Summary", title, sub)
    s.table(
        [
            "Tally sheets",
            "Scored against a load",
            "At or above target",
            "Mean accuracy",
            "Aggregate error",
            "Target",
        ],
        [
            [
                report["sheets"],
                report["reconciled"],
                report["passing"],
                "" if report["mean_accuracy"] is None else report["mean_accuracy"],
                "" if report["aggregate_error"] is None else report["aggregate_error"],
                report["target"],
            ]
        ],
        formats={3: "0.0%", 4: "0.0%", 5: "0.0%"},
    )
    s.note(HOW)
    if not scored:
        s.note("No sheet has been matched to a counted load yet, so there is no accuracy figure.")

    every = branding.Sheet(wb, "Every sheet", title, sub)
    every.table(
        [
            "Sheet",
            "Day",
            "Bay",
            "Truck",
            "Direction",
            "Tally",
            "AI count",
            "Variance",
            "Accuracy",
            "Result",
            "Sheet status",
            "Load",
            "Typing check",
        ],
        [
            [
                r["sheet"]["sheet_id"],
                date.fromisoformat(r["sheet"]["date"]),
                bays.get(r["sheet"]["bay_id"], ""),
                r["sheet"]["plate"],
                r["sheet"]["direction"],
                "" if r["sheet"]["truth"] is None else r["sheet"]["truth"],
                "" if r["ai_count"] is None else r["ai_count"],
                "" if r["variance"] is None else r["variance"],
                "" if r["accuracy"] is None else r["accuracy"],
                "" if r["accuracy"] is None else _result(r),
                r["sheet"]["status"],
                r["session_status"] or "",
                "MISMATCH" if r["sheet"]["transcription_mismatch"] else "OK",
            ]
            for r in scored + unscored
        ],
        formats={1: "yyyy-mm-dd", 7: "+#,##0;-#,##0;0", 8: "0.0%"},
        filterable=True,
    )
    if unscored:
        every.note(UNSCORED)
    return branding.xlsx_bytes(wb)
