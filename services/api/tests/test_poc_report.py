"""The POC report (M9, days 13-14): the scope's acceptance criteria, from the records.

Two days at the demo site (Harare). Four loads; three have tally sheets; the camera
misreads one plate; a person corrects one count. The edge node drops out for twenty
minutes and drains what it queued. Every figure is checked, and so is the rule that
a criterion the records cannot support is "not measured", never a pass.
"""

from __future__ import annotations

import csv
import io
from datetime import timedelta

from conftest import SERVICE, login
from pypdf import PdfReader
from test_edge import bakers, node_headers  # noqa: F401
from test_edge_availability import beat, configured_node

MIN = timedelta(minutes=1)
DAYS = {"start": "2026-10-01", "end": "2026-10-02"}


def load(c, admin, bay, clock, cams, *, plate_read, crates, sheet=None, direction="loading"):
    """A truck: the camera reads its plate, stacks cross, it leaves; optionally the
    staff's tally sheet for it, entered afterwards."""
    choke, lpr = cams
    s = c.post(
        "/api/v1/sessions", json={"bay_id": bay["id"], "direction": direction}, headers=admin
    ).json()
    opened = clock.at
    c.post(
        "/api/v1/ingest/plates",
        json={
            "bay_id": bay["id"],
            "camera_id": lpr["id"],
            "plate": plate_read,
            "confidence": 0.9,
            "read_at": clock.at.isoformat(),
        },
        headers=SERVICE,
    )
    left, track = crates, 0
    while left > 0:  # a crossing is one stack: forty crates at most
        stack, left, track = min(40, left), left - min(40, left), track + 1
        r = c.post(
            "/api/v1/ingest/crossings",
            json={
                "bay_id": bay["id"],
                "camera_id": choke["id"],
                "track_id": track,
                "direction": direction,
                "crates": stack,
                "confidence": 0.9,
                "crossed_at": clock.at.isoformat(),
            },
            headers=SERVICE,
        )
        assert r.status_code < 300, r.text
    clock.at += 20 * MIN
    c.post(f"/api/v1/sessions/{s['id']}/close", headers=admin)
    if sheet:
        plate, counted = sheet
        local = lambda t: (t + timedelta(hours=2)).strftime("%H:%M")  # noqa: E731 (Harare)
        r = c.post(
            "/api/v1/tally/sheets",
            json={
                "sheet_id": f"BI-{s['id'][:8]}",
                "bay_id": bay["id"],
                "date": (opened + timedelta(hours=2)).date().isoformat(),
                "plate": plate,
                "direction": "LOAD" if direction == "loading" else "RETURN",
                "start_time": local(opened),
                "end_time": local(clock.at),
                "lines": [{"line_no": 1, "crates": counted}],
                "total_on_paper": counted,
            },
            headers=admin,
        )
        assert r.status_code in (200, 201), r.text
    clock.at += 10 * MIN
    return s["id"]


def the_poc(c, admin, bay, clock):
    enrolled, choke, lpr = configured_node(c, admin, bay)
    node = node_headers(enrolled)
    both = lambda t: [(choke, True), (lpr, True)]  # noqa: E731
    cams = (choke, lpr)
    began = clock.at  # day 1, 10:00 Harare
    load(c, admin, bay, clock, cams, plate_read="ABC 1001", crates=100, sheet=("ABC 1001", 98))
    load(c, admin, bay, clock, cams, plate_read="ABC 1002", crates=80, sheet=("ABC 1002", 80))
    # the camera read 1003 as 1008: close enough for the sheet to find its load
    misread = load(
        c, admin, bay, clock, cams, plate_read="ABC 1008", crates=60, sheet=("ABC 1003", 60)
    )
    corrected = load(c, admin, bay, clock, cams, plate_read="ABC 1004", crates=50)
    c.post(
        f"/api/v1/sessions/{corrected}/override",
        json={"count": 48, "reason": "person_or_forklift"},
        headers=admin,
    )
    # the node's heartbeats over the same two hours and twenty minutes: up for an hour,
    # twenty minutes without the WAN, then back, draining what it queued meanwhile
    clock.at = began
    beat(c, node, clock, 60, both)
    clock.at += 20 * MIN
    back = clock.at
    beat(c, node, clock, 60, both, spool=lambda t: 12 if t - back < MIN else 0)
    return misread


def test_the_report_measures_each_criterion_from_the_records(bakers):  # noqa: F811
    c, admin, bay, clock = bakers
    the_poc(c, admin, bay, clock)
    r = c.get(
        "/api/v1/reports/poc",
        params={
            "site_id": bay["site_id"],
            **DAYS,
            "baseline_minutes": 25,
            "crate_value": 4.5,
        },
        headers=admin,
    )
    assert r.status_code == 200, r.text
    got = r.json()
    crit = {x["name"]: x for x in got["criteria"]}

    # accuracy, on the AI count, over the three loads with sheets: 98/100, 80/80, 60/60
    assert crit["Accuracy"]["result"] == "pass"
    assert crit["Accuracy"]["figure"] == f"{(1 - 2 / 98 + 1 + 1) / 3 * 100:.1f}%"
    assert any("3 of 4 completed loading loads" in n for n in crit["Accuracy"]["notes"])

    # speed: every load took 20 minutes against a 25-minute baseline
    assert crit["Speed"]["result"] == "pass" and crit["Speed"]["figure"] == "median 20.0 min"

    # LPR: the camera read two of the three sheet plates right; the 98% target is missed
    assert crit["LPR"]["result"] == "fail" and crit["LPR"]["figure"] == "66.7%"
    assert any("read differently 1" in n for n in crit["LPR"]["notes"])

    # reliability: up 120 of 140 minutes, so short of 99%; but the outage drained
    assert crit["Reliability"]["result"] == "fail"
    assert any("came back with 12 queued, drained" in n for n in crit["Reliability"]["notes"])
    # in the site's time, like every time in the report: from the last heartbeat before
    # it, 08:59:30 UTC, which is 10:59 in Harare
    assert any("2026-10-01 10:59 for 20 min" in n for n in crit["Reliability"]["notes"])
    [node] = got["nodes"]
    assert node["outages"] == 1 and 80 < node["uptime_pct"] < 90

    # what went out, as counts of record: 100 + 80 + 60, and 48 after the correction
    assert got["dispatched"] == 288 and got["corrections"] == 1
    assert got["correction_crates"] == -2
    assert got["outstanding_value"] == 288 * 4.5 and got["currency"] == "USD"
    assert got["verdict"] == "fail"


def test_without_a_baseline_or_tally_sheets_it_is_incomplete_never_a_pass(bakers):  # noqa: F811
    c, admin, bay, _ = bakers
    got = c.get(
        "/api/v1/reports/poc", params={"site_id": bay["site_id"], **DAYS}, headers=admin
    ).json()
    results = {x["name"]: x["result"] for x in got["criteria"]}
    assert set(results.values()) == {"not measured"} and got["verdict"] == "incomplete"
    # no heartbeat ever recorded is no history, not 0% uptime
    rel = next(x for x in got["criteria"] if x["name"] == "Reliability")
    assert rel["figure"] == "no heartbeat history"
    assert got["outstanding_value"] is None  # no crate value given: not priced


def test_the_pdf_and_csv_carry_the_same_figures(bakers):  # noqa: F811
    c, admin, bay, clock = bakers
    misread = the_poc(c, admin, bay, clock)
    params = {"site_id": bay["site_id"], **DAYS, "baseline_minutes": 25}
    pdf = c.get("/api/v1/reports/poc", params={**params, "format": "pdf"}, headers=admin)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Result: FAIL" in text and "66.7%" in text and "not priced" in text
    rows = list(
        csv.DictReader(
            io.StringIO(
                c.get("/api/v1/reports/poc", params={**params, "format": "csv"}, headers=admin).text
            )
        )
    )
    assert len(rows) == 4
    wrong = next(x for x in rows if x["plate_read_by_camera"] == "ABC 1008")
    assert wrong["tally_count"] == "60" and misread  # the misread load, with its sheet


def test_only_those_who_export_reports_and_only_their_own_sites(bakers):  # noqa: F811
    c, _, bay, _ = bakers
    params = {"site_id": bay["site_id"], **DAYS}
    assert (
        c.get("/api/v1/reports/poc", params=params, headers=login(c, "operator")).status_code == 403
    )
    assert (
        c.get("/api/v1/reports/poc", params=params, headers=login(c, "b-admin")).status_code == 404
    )
    long = {**params, "start": "2026-01-01"}
    assert c.get("/api/v1/reports/poc", params=long, headers=login(c, "admin")).status_code == 422
