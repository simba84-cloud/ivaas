"""The daily report as files: CSV for imports, PDF and Excel for people (proposal M6).

The CSV is one row per load, the columns a spreadsheet or an ERP import wants, and
stays plain. The PDF is the day at a glance: totals, accuracy against the tally
sheets, what disagreed with the manifests, what people corrected, then every load.
The Excel workbook carries the same sections with the figures as numbers. Both carry
the Liquid brand (`branding`).
"""

from __future__ import annotations

import csv
import io

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table

from ivaas.adapters import branding
from ivaas.domain.reports import EXCEPTION_LABELS, DailyReport

CSV_COLUMNS = [
    "date",
    "site",
    "opened",
    "closed",
    "plate",
    "identified",
    "direction",
    "ai_count",
    "corrected_count",
    "count_of_record",
    "tally_count",
    "accuracy",
    "status",
]


def _pct(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.1f}%"


def to_csv(report: DailyReport) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for r in report.loads:
        w.writerow(
            [
                report.day.isoformat(),
                report.site,
                r.opened,
                r.closed,
                r.plate,
                r.identified,
                r.direction,
                r.ai_count,
                "" if r.corrected is None else r.corrected,
                r.count_of_record,
                "" if r.tally is None else r.tally,
                "" if r.accuracy is None else f"{r.accuracy:.4f}",
                r.status,
            ]
        )
    return out.getvalue().encode("utf-8")


def _table(rows: list[list[str]], widths: list[float] | None = None) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(branding.GRID)
    return t


def to_pdf(report: DailyReport) -> bytes:
    styles = branding.pdf_styles()
    h1, h2, body = styles["h1"], styles["h2"], styles["body"]
    buf = io.BytesIO()
    doc = branding.pdf_document(
        buf, landscape(A4), f"{report.site} daily report {report.day.isoformat()}"
    )
    story: list = [
        Paragraph(f"{report.site}: {report.day:%A %d %B %Y}", h1),
        Paragraph(
            f"{report.tenant}. Times are {report.timezone}. Generated "
            f"{report.generated_at:%Y-%m-%d %H:%M} UTC.",
            body,
        ),
        Spacer(1, 4 * mm),
        _table(
            [
                [
                    "Loads",
                    "Crates dispatched",
                    "Crates returned",
                    "Outstanding",
                    "Still at the bay",
                    "Without a plate",
                ],
                [
                    str(len(report.loads)),
                    f"{report.dispatched:,}",
                    f"{report.returned:,}",
                    f"{report.outstanding:,}",
                    str(report.in_progress),
                    str(report.unidentified),
                ],
            ]
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            "Counts are each load's count of record: a person's correction where there "
            "is one, else the AI count. Loads still at the bay are not in the totals.",
            body,
        ),
        Paragraph("Accuracy against the tally sheets", h2),
    ]
    if report.verified:
        story.append(
            _table(
                [
                    [
                        "Verified loads",
                        "Mean accuracy",
                        "Aggregate error",
                        "Target",
                        "At or above target",
                    ],
                    [
                        str(report.verified),
                        _pct(report.mean_accuracy),
                        _pct(report.aggregate_error),
                        _pct(report.target),
                        f"{report.passing} of {report.verified}",
                    ],
                ]
            )
        )
        story.append(
            Paragraph("Accuracy is measured on the AI count, never on a correction.", body)
        )
    else:
        story.append(
            Paragraph(
                "No tally sheet was reconciled for this day, so there is no "
                "accuracy figure for it.",
                body,
            )
        )

    story.append(Paragraph("Manifest exceptions", h2))
    if report.exceptions:
        rows = [["What", "Truck", "Route", "Manifest", "Counted", "Difference", "Status"]]
        for e in report.exceptions:
            diff = e.difference
            rows.append(
                [
                    EXCEPTION_LABELS[e.kind],
                    e.plate or "",
                    e.route,
                    "" if e.expected is None else f"{e.expected:,}",
                    "" if e.counted is None else f"{e.counted:,}",
                    "" if diff is None else f"{diff:+,}",
                    e.status.value,
                ]
            )
        story.append(_table(rows))
    else:
        story.append(Paragraph("None raised for this day.", body))

    story.append(Paragraph("Corrections by people", h2))
    if report.corrections:
        story.append(
            _table(
                [["Opened", "Truck", "AI count", "Corrected to"]]
                + [
                    [r.opened, r.plate or "no plate", f"{r.ai_count:,}", f"{r.corrected:,}"]
                    for r in report.corrections
                ]
            )
        )
    else:
        story.append(Paragraph("None.", body))

    story.append(Paragraph("Every load", h2))
    if report.loads:
        rows = [
            [
                "Opened",
                "Closed",
                "Truck",
                "Direction",
                "AI count",
                "Corrected",
                "Of record",
                "Tally",
                "Accuracy",
                "Status",
            ]
        ]
        for r in report.loads:
            rows.append(
                [
                    r.opened,
                    r.closed or "open",
                    r.plate or "no plate",
                    r.direction,
                    f"{r.ai_count:,}",
                    "" if r.corrected is None else f"{r.corrected:,}",
                    f"{r.count_of_record:,}",
                    "" if r.tally is None else f"{r.tally:,}",
                    _pct(r.accuracy),
                    r.status,
                ]
            )
        story.append(_table(rows))
    else:
        story.append(Paragraph("No loads were counted at this site on this day.", body))
    page = branding.pdf_page(f"Daily report · {report.day:%d %b %Y}")
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()


def to_xlsx(report: DailyReport) -> bytes:
    """Summary and Every load, branded; the same figures as the PDF, as numbers."""
    wb = branding.workbook(f"{report.site} daily report {report.day.isoformat()}")
    title = f"{report.site}: {report.day:%A %d %B %Y}"
    sub = (
        f"{report.tenant}. Times are {report.timezone}. "
        f"Generated {report.generated_at:%Y-%m-%d %H:%M} UTC."
    )

    s = branding.Sheet(wb, "Summary", title, sub)
    s.heading("The day")
    s.table(
        [
            "Loads",
            "Crates dispatched",
            "Crates returned",
            "Outstanding",
            "Still at the bay",
            "Without a plate",
        ],
        [
            [
                len(report.loads),
                report.dispatched,
                report.returned,
                report.outstanding,
                report.in_progress,
                report.unidentified,
            ]
        ],
    )
    s.note(
        "Counts are each load's count of record: a person's correction where there is "
        "one, else the AI count. Loads still at the bay are not in the totals."
    )
    s.gap()
    s.heading("Accuracy against the tally sheets")
    if report.verified:
        s.table(
            ["Verified loads", "Mean accuracy", "Aggregate error", "Target", "At or above target"],
            [
                [
                    report.verified,
                    report.mean_accuracy,
                    report.aggregate_error,
                    report.target,
                    report.passing,
                ]
            ],
            formats={1: "0.0%", 2: "0.0%", 3: "0.0%"},
        )
        s.note("Accuracy is measured on the AI count, never on a correction.")
    else:
        s.note("No tally sheet was reconciled for this day, so there is no accuracy figure for it.")
    s.gap()
    s.heading("Manifest exceptions")
    if report.exceptions:
        s.table(
            ["What", "Truck", "Route", "Manifest", "Counted", "Difference", "Status"],
            [
                [
                    EXCEPTION_LABELS[e.kind],
                    e.plate or "",
                    e.route,
                    "" if e.expected is None else e.expected,
                    "" if e.counted is None else e.counted,
                    "" if e.difference is None else e.difference,
                    e.status.value,
                ]
                for e in report.exceptions
            ],
            formats={5: "+#,##0;-#,##0;0"},
        )
    else:
        s.note("None raised for this day.")
    s.gap()
    s.heading("Corrections by people")
    if report.corrections:
        s.table(
            ["Opened", "Truck", "AI count", "Corrected to"],
            [
                [r.opened, r.plate or "no plate", r.ai_count, r.corrected]
                for r in report.corrections
            ],
        )
    else:
        s.note("None.")

    loads = branding.Sheet(wb, "Every load", title, sub)
    if report.loads:
        loads.table(
            [
                "Opened",
                "Closed",
                "Truck",
                "Identified",
                "Direction",
                "AI count",
                "Corrected",
                "Of record",
                "Tally",
                "Accuracy",
                "Status",
            ],
            [
                [
                    r.opened,
                    r.closed or "open",
                    r.plate or "no plate",
                    r.identified,
                    r.direction,
                    r.ai_count,
                    "" if r.corrected is None else r.corrected,
                    r.count_of_record,
                    "" if r.tally is None else r.tally,
                    "" if r.accuracy is None else r.accuracy,
                    r.status,
                ]
                for r in report.loads
            ],
            formats={9: "0.0%"},
            filterable=True,
        )
    else:
        loads.note("No loads were counted at this site on this day.")
    return branding.xlsx_bytes(wb)
