"""M6: the daily report, filed every morning and on demand, in PDF and CSV."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from conftest import SERVICE, login, make_client
from pypdf import PdfReader


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)  # 09:00 in Harare

    def now(self) -> datetime:
        return self.at


DAY = "2026-10-01"


@pytest.fixture
def day():
    """A day at the demo site: a verified load, a corrected one, a return, an open one,
    and one whose plate was never read."""
    with make_client() as c:
        container = c.app.state.container
        container.clock = clock = Clock()
        admin = login(c, "admin")
        bay = c.get("/api/v1/bays", headers=admin).json()[0]
        cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
        choke = next(x["id"] for x in cams if x["role"] == "chokepoint")
        lpr = next(x["id"] for x in cams if x["role"] == "lpr")

        def load(plate, crates, direction="loading", close=True):
            s = c.post(
                "/api/v1/sessions",
                json={"bay_id": bay["id"], "direction": direction},
                headers=admin,
            ).json()
            at = clock.now().isoformat()
            if plate:
                c.post(
                    "/api/v1/ingest/plates",
                    json={
                        "bay_id": bay["id"],
                        "camera_id": lpr,
                        "plate": plate,
                        "confidence": 0.9,
                        "read_at": at,
                    },
                    headers=SERVICE,
                )
            left = crates
            while left > 0:
                n = min(40, left)
                left -= n
                c.post(
                    "/api/v1/ingest/crossings",
                    json={
                        "bay_id": bay["id"],
                        "camera_id": choke,
                        "track_id": left,
                        "direction": direction,
                        "crates": n,
                        "confidence": 0.9,
                        "crossed_at": at,
                    },
                    headers=SERVICE,
                )
            if close:
                c.post(f"/api/v1/sessions/{s['id']}/close", headers=admin)
            clock.at += timedelta(minutes=30)
            return s["id"]

        verified = load("ABC 1001", 100)
        c.post(f"/api/v1/sessions/{verified}/reconcile", json={"manual_count": 98}, headers=admin)
        corrected = load("ABC 1002", 80)
        c.post(
            f"/api/v1/sessions/{corrected}/override",
            json={"count": 78, "reason": "person_or_forklift"},
            headers=admin,
        )
        load("ABC 1001", 30, "offloading")
        load(None, 12)
        load("ABC 1003", 40, close=False)
        yield c, admin, bay, clock


def test_the_csv_has_one_row_per_load_with_every_figure(day):
    c, admin, bay, _ = day
    r = c.get(
        "/api/v1/reports/daily",
        params={"site_id": bay["site_id"], "day": DAY, "format": "csv"},
        headers=admin,
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [
        (
            x["plate"],
            x["direction"],
            x["ai_count"],
            x["corrected_count"],
            x["count_of_record"],
            x["tally_count"],
            x["status"],
        )
        for x in rows
    ] == [
        ("ABC 1001", "loading", "100", "", "100", "98", "reconciled"),
        ("ABC 1002", "loading", "80", "78", "78", "", "closed"),
        ("ABC 1001", "offloading", "30", "", "30", "", "closed"),
        ("", "loading", "12", "", "12", "", "closed"),
        ("ABC 1003", "loading", "40", "", "40", "", "open"),
    ]
    assert rows[0]["accuracy"] == "0.9796" and rows[0]["opened"] == "09:00"


def test_the_pdf_states_totals_accuracy_and_corrections(day):
    c, admin, bay, _ = day
    r = c.get(
        "/api/v1/reports/daily", params={"site_id": bay["site_id"], "day": DAY}, headers=admin
    )
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(r.content)).pages)
    # dispatched 100 + 78 (corrected) + 12; returned 30; the open load is not in the totals
    assert "190" in text and "160" in text
    assert "98.0%" in text  # 1 - 2/98 = 97.96%, on the AI count, from the tally sheet
    assert "Corrected to" in text and "Without a plate" in text
    assert "Accuracy is measured on the AI count" in text


def test_a_day_without_tally_sheets_has_no_accuracy_rather_than_zero():
    with make_client() as c:
        admin = login(c, "admin")
        site = c.get("/api/v1/sites", headers=admin).json()[0]["id"]
        r = c.get(
            "/api/v1/reports/daily", params={"site_id": site, "day": "2026-01-01"}, headers=admin
        )
        text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
        assert "no accuracy figure" in text and "0.0%" not in text
        assert "No loads were counted" in text


def test_yesterdays_report_is_filed_once_after_six_in_the_morning(day):
    c, admin, bay, clock = day
    container = c.app.state.container
    from ivaas.domain.tenancy import BAKERS_INN_ID
    from ivaas.tenancy import tenant_context

    async def file():
        with tenant_context(BAKERS_INN_ID):
            return await (await container.file_daily_reports_uc())()

    clock.at = datetime(2026, 10, 2, 3, 30, tzinfo=UTC)  # 05:30 in Harare: too early
    assert c.portal.call(file) == []
    clock.at = datetime(2026, 10, 2, 4, 30, tzinfo=UTC)  # 06:30
    [filed] = c.portal.call(file)
    assert str(filed.day) == DAY and filed.loads == 5
    assert c.portal.call(file) == []  # once
    [listed] = c.get("/api/v1/reports", headers=admin).json()
    assert listed["day"] == DAY and listed["site"] == "Bakery Industrial Site"
    assert c.get(listed["pdf_url"]).content.startswith(b"%PDF")
    assert c.get(listed["csv_url"]).text.startswith("date,site,opened")


def test_reports_are_for_those_who_may_export_them(day):
    c, _, bay, _ = day
    params = {"site_id": bay["site_id"], "day": DAY}
    assert (
        c.get("/api/v1/reports/daily", params=params, headers=login(c, "operator")).status_code
        == 403
    )
    assert (
        c.get("/api/v1/reports/daily", params=params, headers=login(c, "viewer")).status_code == 200
    )
    other = login(c, "b-admin")
    assert c.get("/api/v1/reports/daily", params=params, headers=other).status_code == 404
    assert c.get("/api/v1/reports", headers=other).json() == []
