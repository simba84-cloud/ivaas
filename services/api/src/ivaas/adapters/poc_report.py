"""The POC report as files: a PDF to sign, and every load as CSV to check it against."""

from __future__ import annotations

import csv
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ivaas.domain.manifests import local_day, site_tz
from ivaas.domain.poc import PocReport

CSV_COLUMNS = [
    "day",
    "opened",
    "closed",
    "plate",
    "plate_read_by_camera",
    "identified_by",
    "direction",
    "ai_count",
    "corrected_count",
    "count_of_record",
    "tally_count",
    "accuracy",
    "status",
]
VERDICT = {
    "pass": "Every criterion measured, and every one met.",
    "fail": "At least one criterion was measured and not met.",
    "incomplete": "At least one criterion could not be measured from the records; "
    "the report is not a pass until it is.",
}
_GRID = TableStyle(
    [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EAF0")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8BCC8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
)


def _table(rows: list[list[str]]) -> Table:
    t = Table(rows, repeatRows=1)
    t.setStyle(_GRID)
    return t


def to_csv(report: PocReport) -> bytes:
    tz = site_tz(report.timezone)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for s in report.loads:
        w.writerow(
            [
                local_day(s.opened_at, tz).isoformat(),
                s.opened_at.astimezone(tz).strftime("%H:%M"),
                s.closed_at.astimezone(tz).strftime("%H:%M") if s.closed_at else "",
                s.plate or "",
                s.plate_read or "",
                s.identified_by or "",
                s.direction.value,
                s.ai_count,
                "" if s.override_count is None else s.override_count,
                s.count_of_record,
                "" if s.manual_count is None else s.manual_count,
                "" if s.accuracy is None else f"{s.accuracy:.4f}",
                s.status.value,
            ]
        )
    return out.getvalue().encode("utf-8")


def to_pdf(report: PocReport) -> bytes:
    styles = getSampleStyleSheet()
    h1, h2, body = styles["Heading1"], styles["Heading3"], styles["BodyText"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"{report.site} POC report {report.start} to {report.end}",
        author="IVaaS",
    )
    b = report.balance
    story: list = [
        Paragraph(
            f"{report.site}: proof of concept, {report.start:%d %B} to {report.end:%d %B %Y}", h1
        ),
        Paragraph(
            f"{report.tenant}. Times are {report.timezone}. Generated "
            f"{report.generated_at:%Y-%m-%d %H:%M} UTC from the platform's own records.",
            body,
        ),
        Spacer(1, 4 * mm),
        Paragraph(f"<b>Result: {report.verdict.upper()}</b>. {VERDICT[report.verdict]}", body),
        Spacer(1, 3 * mm),
        _table(
            [["Criterion", "Result", "Measured", "Target"]]
            + [[c.name, c.result, c.figure, c.target] for c in report.criteria]
        ),
    ]
    for c in report.criteria:
        story += [Paragraph(f"{c.name}: {c.result}", h2), Paragraph(f"How: {c.how}.", body)]
        story += [Paragraph(n.replace("  ", "&nbsp;&nbsp;"), body) for n in c.notes]

    story += [
        Paragraph("Return on investment: what the records show", h2),
        _table(
            [
                [
                    "Loads out",
                    "Loads back",
                    "Crates dispatched",
                    "Crates returned",
                    "Not yet back",
                    "Still at the bay",
                ],
                [
                    str(b.loads_out),
                    str(b.loads_back),
                    f"{b.dispatched:,}",
                    f"{b.returned:,}",
                    f"{b.outstanding:,}",
                    str(b.in_progress),
                ],
            ]
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "Crates are each load's count of record: a person's correction where there is one."
            + (
                f" At the given crate value, the crates not yet back are worth "
                f"{report.outstanding_value:,.2f} {report.currency}."
                if report.outstanding_value is not None
                else " No crate value was given, so they are not priced."
            )
            + " Leakage is the part of them that never comes back; a longer window tells it "
            "from crates still on the road.",
            body,
        ),
        Paragraph(
            f"People corrected {report.corrections} load(s), changing the count of record by "
            f"{report.correction_crates:+,} crates in all.",
            body,
        ),
    ]
    if report.exceptions:
        story += [
            Spacer(1, 2 * mm),
            _table(
                [["Manifest exception", "Status", "Count"]]
                + [
                    [k.replace("_", " "), s, str(n)]
                    for (k, s), n in sorted(report.exceptions.items())
                ]
            ),
        ]
    else:
        story.append(Paragraph("No manifest exceptions were raised in the window.", body))

    tz = site_tz(report.timezone)
    story += [PageBreak(), Paragraph("Every load", h2)]
    if report.loads:
        story.append(
            _table(
                [
                    [
                        "Day",
                        "Opened",
                        "Truck",
                        "Read by camera",
                        "Direction",
                        "AI",
                        "Corrected",
                        "Of record",
                        "Tally",
                        "Accuracy",
                        "Status",
                    ]
                ]
                + [
                    [
                        f"{local_day(s.opened_at, tz):%d %b}",
                        s.opened_at.astimezone(tz).strftime("%H:%M"),
                        s.plate or "no plate",
                        s.plate_read or "",
                        s.direction.value,
                        f"{s.ai_count:,}",
                        "" if s.override_count is None else f"{s.override_count:,}",
                        f"{s.count_of_record:,}",
                        "" if s.manual_count is None else f"{s.manual_count:,}",
                        "" if s.accuracy is None else f"{s.accuracy * 100:.1f}%",
                        s.status.value,
                    ]
                    for s in report.loads
                ]
            )
        )
    else:
        story.append(Paragraph("No loads were counted at this site in the window.", body))
    doc.build(story)
    return buf.getvalue()
