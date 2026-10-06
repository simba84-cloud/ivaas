"""Tally sheets over HTTP: blind entry, matching, and reconciliation through the session."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import SERVICE, login

HARARE = ZoneInfo("Africa/Harare")  # the demo site's zone
BLIND_FIELDS = {"ai_count", "accuracy", "variance", "manual_count", "session_id"}


@pytest.fixture
def roles(anon):
    return {u: login(anon, u) for u in ("viewer", "operator", "admin")}


@pytest.fixture
def bay(anon, roles):
    return anon.get("/api/v1/bays", headers=roles["viewer"]).json()[0]


def truck_session(client, bay, headers, plate="ABE 2437", crates=(15, 15, 14), close=True):
    """A plate read opens the session; stacks cross; the truck leaves unless close=False."""
    cams = client.get(f"/api/v1/bays/{bay['id']}/cameras", headers=headers).json()
    lpr = next(c for c in cams if c["role"] == "lpr")
    door = next(c for c in cams if c["role"] == "chokepoint")
    now = datetime.now(UTC).isoformat()
    s = client.post(
        "/api/v1/ingest/plates",
        headers=SERVICE,
        json={
            "bay_id": bay["id"],
            "camera_id": lpr["id"],
            "plate": plate,
            "confidence": 0.97,
            "read_at": now,
        },
    ).json()
    for i, n in enumerate(crates):
        client.post(
            "/api/v1/ingest/crossings",
            headers=SERVICE,
            json={
                "bay_id": bay["id"],
                "camera_id": door["id"],
                "track_id": i,
                "direction": "loading",
                "confidence": 0.9,
                "crossed_at": now,
                "crates": n,
            },
        )
    if close:
        client.post(f"/api/v1/sessions/{s['id']}/close", headers=headers)
    return s["id"]


def local_window():
    now = datetime.now(HARARE)
    start, end = now - timedelta(minutes=10), now + timedelta(minutes=10)
    return start.date().isoformat(), start.strftime("%H:%M"), end.strftime("%H:%M")


def sheets_csv(sheet_id="BI-1", plate="ABE 2437", direction="LOAD", total=""):
    day, start, end = local_window()
    return (
        "sheet_id,date,bay,truck_plate,direction,start_time,end_time,route_driver,pages,"
        "stack_count,total_crates,total_on_paper,check,counted_by,verified_by,entered_by,notes\n"
        f"EXAMPLE-1,{day},B1,AEX 4821,LOAD,06:40,07:05,,1,,,124,,,,,\n"
        f"{sheet_id},{day},B1,{plate},{direction},{start},{end},,1,,,{total},,R. Ncube,S. Dube,,\n"
        ",,,,,,,,,,,,,,,,\n"
    )


def stacks_csv(sheet_id="BI-1", crates=(15, 15, 15)):
    rows = "".join(f"{sheet_id},{i},{n},\n" for i, n in enumerate(crates, start=1))
    return "sheet_id,line_no,crates,note\n" + rows


def upload(client, bay, headers, sheets, stacks=None):
    files = {"sheets": ("Entry - Sheets.csv", sheets.encode(), "text/csv")}
    if stacks is not None:
        files["stacks"] = ("Entry - Stacks.csv", stacks.encode(), "text/csv")
    return client.post(f"/api/v1/tally/import?bay_id={bay['id']}", files=files, headers=headers)


def session(client, session_id, headers):
    listed = client.get("/api/v1/sessions", headers=headers).json()
    return next(s for s in listed if s["id"] == session_id)


def test_import_reconciles_the_closed_session_it_describes(anon, roles, bay):
    op = roles["operator"]
    sid = truck_session(anon, bay, op, crates=(15, 15, 14))  # AI saw 44

    r = upload(anon, bay, op, sheets_csv(total="45"), stacks_csv(crates=(15, 15, 15)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped"] == ["Sheets row 2: EXAMPLE-1 is the example row"]
    (saved,) = body["saved"]
    assert saved["status"] == "reconciled"
    assert saved["truth"] == 45 and saved["lines"] == 3 and not saved["transcription_mismatch"]
    assert BLIND_FIELDS.isdisjoint(saved)  # the operator never sees the AI's figure here

    s = session(anon, sid, op)
    assert s["manual_count"] == 45 and s["ai_count"] == 44
    assert s["accuracy"] == pytest.approx(1 - 1 / 45)
    assert s["status"] == "reconciled"  # 97.8% clears the 95% target

    report = anon.get("/api/v1/tally/report", headers=roles["viewer"]).json()
    assert report["reconciled"] == 1 and report["passing"] == 1
    assert report["mean_accuracy"] == pytest.approx(s["accuracy"])
    assert report["aggregate_error"] == pytest.approx(1 / 45)
    assert report["rows"][0]["ai_count"] == 44 and report["rows"][0]["variance"] == -1

    audit = anon.get("/api/v1/audit?action=tally_sheet_saved", headers=roles["admin"]).json()
    assert audit[0]["actor"] == "operator" and audit[0]["detail"]["outcome"] == "reconciled"


def test_sheet_entered_while_loading_reconciles_once_the_truck_leaves(anon, roles, bay):
    op = roles["operator"]
    sid = truck_session(anon, bay, op, close=False)  # still open
    (saved,) = upload(anon, bay, op, sheets_csv(), stacks_csv(crates=(15, 15, 14))).json()["saved"]
    assert saved["status"] == "matched"
    assert session(anon, sid, op)["manual_count"] is None

    anon.post(f"/api/v1/sessions/{sid}/close", headers=op)
    (changed,) = anon.post("/api/v1/tally/rematch", headers=op).json()["changed"]
    assert changed["status"] == "reconciled"
    assert session(anon, sid, op)["manual_count"] == 44


def test_a_sheet_never_overwrites_a_count_already_recorded(anon, roles, bay):
    op, admin = roles["operator"], roles["admin"]
    sid = truck_session(anon, bay, op)
    anon.post(f"/api/v1/sessions/{sid}/reconcile", json={"manual_count": 50}, headers=admin)

    (saved,) = upload(anon, bay, op, sheets_csv(total="45")).json()["saved"]
    assert saved["status"] == "conflict"
    assert session(anon, sid, op)["manual_count"] == 50
    conflicts = anon.get("/api/v1/audit?action=tally_conflict", headers=admin).json()
    assert conflicts[0]["subject"] == "BI-1 (ABE 2437)"

    report = anon.get("/api/v1/tally/report", headers=roles["viewer"]).json()
    assert report["reconciled"] == 0 and report["mean_accuracy"] is None  # not scored as 0%


def test_correcting_a_reconciled_sheet_becomes_a_conflict_not_a_rewrite(anon, roles, bay):
    op = roles["operator"]
    sid = truck_session(anon, bay, op)
    first = upload(anon, bay, op, sheets_csv(total="45")).json()["saved"][0]
    assert first["status"] == "reconciled"
    again = upload(anon, bay, op, sheets_csv(total="47")).json()["saved"][0]
    assert again["status"] == "conflict"
    assert session(anon, sid, op)["manual_count"] == 45
    assert len(anon.get("/api/v1/tally/sheets", headers=op).json()) == 1  # replaced, not duplicated


def test_unmatched_sheet_is_reported_as_unmatched_never_as_zero(anon, roles, bay):
    truck_session(anon, bay, roles["operator"], plate="ABE 2437")
    r = upload(anon, bay, roles["operator"], sheets_csv(plate="ZZZ 9999", total="40"))
    (saved,) = r.json()["saved"]
    assert saved["status"] == "unmatched"
    row = anon.get("/api/v1/tally/report", headers=roles["viewer"]).json()["rows"][0]
    assert row["accuracy"] is None and row["passed"] is None and row["session_id"] is None


def test_a_bad_file_imports_nothing_and_says_why(anon, roles, bay):
    op = roles["operator"]
    r = upload(anon, bay, op, sheets_csv(direction="OUT", total="40"))
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Nothing was imported. Sheets row 3 (BI-1): direction")
    assert anon.get("/api/v1/tally/sheets", headers=op).json() == []

    only_example = sheets_csv().splitlines()[:2]
    r = upload(anon, bay, op, "\n".join(only_example))
    # only the example filled in: said so, rather than a bare "no sheets found"
    assert r.status_code == 422 and "Only the EXAMPLE rows are filled in" in r.json()["detail"]


def test_form_entry(anon, roles, bay):
    op = roles["operator"]
    day, start, end = local_window()
    body = {
        "sheet_id": "BI-F1",
        "bay_id": bay["id"],
        "date": day,
        "plate": "ABE 2437",
        "direction": "RETURN",
        "start_time": start,
        "end_time": end,
        "lines": [{"line_no": 1, "crates": 15}, {"line_no": 2, "crates": 14, "note": "P"}],
    }
    r = anon.post("/api/v1/tally/sheets", json=body, headers=op)
    assert r.status_code == 201, r.text
    assert r.json()["truth"] == 29 and r.json()["direction"] == "RETURN"
    assert r.json()["status"] == "unmatched"  # no offloading session exists

    bad = anon.post("/api/v1/tally/sheets", json={**body, "sheet_id": "EXAMPLE-9"}, headers=op)
    assert bad.status_code == 422 and "example" in bad.json()["detail"]
    missing = anon.post(
        "/api/v1/tally/sheets",
        json={**body, "bay_id": "00000000-0000-0000-0000-000000000000"},
        headers=op,
    )
    assert missing.status_code == 404


def test_who_may_do_what(anon, roles, bay):
    viewer, op, admin = roles["viewer"], roles["operator"], roles["admin"]
    assert upload(anon, bay, viewer, sheets_csv(total="1")).status_code == 403
    assert anon.get("/api/v1/tally/sheets", headers=viewer).status_code == 403
    assert anon.get("/api/v1/tally/report", headers=viewer).status_code == 200

    sid = truck_session(anon, bay, op)
    # typing a count in beside the AI's is now an admin's correction path only
    assert (
        anon.post(f"/api/v1/sessions/{sid}/reconcile", json={"manual_count": 44}, headers=op)
    ).status_code == 403
    assert (
        anon.post(f"/api/v1/sessions/{sid}/reconcile", json={"manual_count": 44}, headers=admin)
    ).status_code == 200
