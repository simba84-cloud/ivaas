"""M5: balances (T5.3) and dispatch manifests with exceptions (T5.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import SERVICE, login, make_client


class Clock:
    def __init__(self) -> None:
        # 10:00 in Harare (the demo site's time zone)
        self.at = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at


TODAY = "2026-10-01"


@pytest.fixture
def rig():
    with make_client() as c:
        c.app.state.container.clock = clock = Clock()
        admin = login(c, "admin")
        bay = c.get("/api/v1/bays", headers=admin).json()[0]
        cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
        choke = next(x["id"] for x in cams if x["role"] == "chokepoint")
        lpr = next(x["id"] for x in cams if x["role"] == "lpr")
        yield c, admin, bay["id"], choke, lpr, clock


def load(c, admin, bay, choke, lpr, clock, plate, crates, direction="loading"):
    """One truck at the bay: a load opened, its plate read, its crates counted, closed."""
    s = c.post(
        "/api/v1/sessions", json={"bay_id": bay, "direction": direction}, headers=admin
    ).json()
    at = clock.now().isoformat()
    c.post(
        "/api/v1/ingest/plates",
        json={"bay_id": bay, "camera_id": lpr, "plate": plate, "confidence": 0.95, "read_at": at},
        headers=SERVICE,
    )
    left, track = crates, 0
    while left > 0:
        n, left, track = min(40, left), left - min(40, left), track + 1
        c.post(
            "/api/v1/ingest/crossings",
            json={
                "bay_id": bay,
                "camera_id": choke,
                "track_id": track,
                "direction": direction,
                "crates": n,
                "confidence": 0.9,
                "crossed_at": at,
            },
            headers=SERVICE,
        )
    c.post(f"/api/v1/sessions/{s['id']}/close", headers=admin)
    clock.at += timedelta(minutes=20)
    return s["id"]


def import_csv(c, headers, text):
    r = c.post(
        "/api/v1/manifests/import", files={"file": ("manifest.csv", text.encode())}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- T5.3: balances match a scripted day exactly ------------------------------------------


def test_balances_match_a_scripted_day_of_five_trucks(rig):
    c, admin, bay, choke, lpr, clock = rig
    script = [  # interleaved, as a real yard would be
        ("ABC 1001", 120, "loading"),
        ("ABC 1002", 80, "loading"),
        ("ABC 1001", 30, "offloading"),
        ("ABC 1003", 200, "loading"),
        ("ABC 1004", 60, "loading"),
        ("ABC 1002", 80, "offloading"),
        ("ABC 1005", 45, "loading"),
        ("ABC 1003", 150, "offloading"),
        ("ABC 1001", 90, "offloading"),
    ]
    for plate, crates, direction in script:
        load(c, admin, bay, choke, lpr, clock, plate, crates, direction)
    got = {
        b["key"]: b for b in c.get("/api/v1/balances", params={"by": "truck"}, headers=admin).json()
    }
    expected = {  # dispatched, returned, outstanding
        "ABC 1001": (120, 120, 0),
        "ABC 1002": (80, 80, 0),
        "ABC 1003": (200, 150, 50),
        "ABC 1004": (60, 0, 60),
        "ABC 1005": (45, 0, 45),
    }
    assert {
        k: (b["dispatched"], b["returned"], b["outstanding"]) for k, b in got.items()
    } == expected
    by_day = c.get("/api/v1/balances", params={"by": "day"}, headers=admin).json()
    assert [(b["key"], b["outstanding"]) for b in by_day] == [(TODAY, 155)]


def test_balances_use_the_count_of_record_and_leave_open_loads_out(rig):
    c, admin, bay, choke, lpr, clock = rig
    sid = load(c, admin, bay, choke, lpr, clock, "ABC 2001", 100)
    c.post(
        f"/api/v1/sessions/{sid}/override",
        json={"count": 96, "reason": "person_or_forklift"},
        headers=admin,
    )
    c.post("/api/v1/sessions", json={"bay_id": bay, "direction": "loading"}, headers=admin)
    [b] = [x for x in c.get("/api/v1/balances", headers=admin).json() if x["key"] == "ABC 2001"]
    assert b["dispatched"] == 96 and b["corrected"] == 1
    open_ones = [x for x in c.get("/api/v1/balances", headers=admin).json() if x["in_progress"]]
    assert open_ones and open_ones[0]["dispatched"] == 0  # counted as in progress, not added in


# --- T5.4: manifests and exceptions ---------------------------------------------------------

HEADER = "date,plate,direction,expected,reference,route\n"


def test_a_short_load_raises_an_exception_with_its_evidence_at_hand(rig):
    """The proposal's case: the manifest says 1,200, the cameras counted 1,150."""
    c, admin, bay, choke, lpr, clock = rig
    sid = load(c, admin, bay, choke, lpr, clock, "ABC 1234", 1150)
    out = import_csv(c, admin, HEADER + f"{TODAY},ABC 1234,LOAD,1200,M-001,Route 7\n")
    assert out["added"] == 1 and out["exceptions_raised"] == 1
    [e] = c.get("/api/v1/exceptions", headers=admin).json()
    assert (e["kind"], e["expected"], e["counted"], e["difference"]) == (
        "count_mismatch",
        1200,
        1150,
        -50,
    )
    assert e["session_id"] == sid and e["route"] == "Route 7"
    # the evidence is one call away, filed under the same load
    assert c.get(f"/api/v1/sessions/{sid}/evidence", headers=admin).status_code == 200
    [line] = c.get("/api/v1/manifests", headers=admin).json()
    assert line["status"] == "matched" and line["session_id"] == sid


def test_matching_counts_raise_nothing_and_rerunning_raises_nothing_twice(rig):
    c, admin, bay, choke, lpr, clock = rig
    load(c, admin, bay, choke, lpr, clock, "ABC 1234", 300)
    load(c, admin, bay, choke, lpr, clock, "ABD 5678", 250)
    csv = HEADER + f"{TODAY},A8C I234,LOAD,300,M-1,R1\n{TODAY},ABD 5678,LOAD,260,M-2,R1\n"
    assert import_csv(c, admin, csv)["exceptions_raised"] == 1  # only the short one
    again = import_csv(c, admin, csv)
    assert again["updated"] == 2 and again["exceptions_raised"] == 0
    assert len(c.get("/api/v1/exceptions", headers=admin).json()) == 1


def test_a_correction_that_brings_the_count_into_line_closes_the_exception(rig):
    c, admin, bay, choke, lpr, clock = rig
    sid = load(c, admin, bay, choke, lpr, clock, "ABC 1234", 290)
    import_csv(c, admin, HEADER + f"{TODAY},ABC 1234,LOAD,300,M-1,R1\n")
    c.post(
        f"/api/v1/sessions/{sid}/override",
        json={"count": 300, "reason": "missed_by_camera"},
        headers=admin,
    )
    import_csv(c, admin, HEADER)  # any run of matching (the sweep does the same)
    assert c.get("/api/v1/exceptions", headers=admin).json() == []
    [done] = c.get("/api/v1/exceptions", params={"status": "resolved"}, headers=admin).json()
    assert done["resolved_by"] == "system" and "agrees" in done["resolution_note"]


def test_a_truck_that_never_came_is_raised_once_its_day_is_over(rig):
    c, admin, *_, clock = rig
    import_csv(c, admin, HEADER + f"{TODAY},ZZZ 9999,LOAD,100,M-9,R2\n")
    assert c.get("/api/v1/exceptions", headers=admin).json() == []  # the day is not over
    clock.at += timedelta(days=1)
    import_csv(c, admin, HEADER)
    [e] = c.get("/api/v1/exceptions", headers=admin).json()
    assert e["kind"] == "not_seen" and e["plate"] == "ZZZ 9999" and e["expected"] == 100


def test_a_load_no_manifest_expected_is_raised_after_its_day(rig):
    c, admin, bay, choke, lpr, clock = rig
    import_csv(c, admin, HEADER + f"{TODAY},ABC 1234,LOAD,100,M-1,R1\n")
    load(c, admin, bay, choke, lpr, clock, "ABC 1234", 100)
    stray = load(c, admin, bay, choke, lpr, clock, "QQQ 0001", 40)
    clock.at += timedelta(days=1)
    import_csv(c, admin, HEADER)
    [e] = c.get("/api/v1/exceptions", headers=admin).json()
    assert e["kind"] == "unexpected" and e["session_id"] == stray and e["counted"] == 40


def test_the_tolerance_setting_is_respected(rig):
    c, admin, bay, choke, lpr, clock = rig
    c.put("/api/v1/settings/manifest_tolerance_crates", json={"value": 5}, headers=admin)
    load(c, admin, bay, choke, lpr, clock, "ABC 1234", 297)
    assert (
        import_csv(c, admin, HEADER + f"{TODAY},ABC 1234,LOAD,300,M-1,R1\n")["exceptions_raised"]
        == 0
    )


def test_an_exception_is_resolved_with_a_note_by_someone_entitled_to(rig):
    c, admin, bay, choke, lpr, clock = rig
    load(c, admin, bay, choke, lpr, clock, "ABC 1234", 280)
    import_csv(c, admin, HEADER + f"{TODAY},ABC 1234,LOAD,300,M-1,R1\n")
    [e] = c.get("/api/v1/exceptions", headers=admin).json()
    url = f"/api/v1/exceptions/{e['id']}/resolve"
    assert c.post(url, json={"note": "x"}, headers=login(c, "operator")).status_code == 403
    r = c.post(url, json={"note": "20 crates left on the dock; returned next trip"}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert c.post(url, json={"note": "again"}, headers=admin).status_code == 409
    actions = [a["action"] for a in c.get("/api/v1/audit", headers=admin).json()]
    assert "exception_resolved" in actions and "manifest_imported" in actions


def test_the_import_says_which_lines_it_could_not_use(rig):
    c, admin, *_ = rig
    c.post("/api/v1/fleet", json={"plate": "ABC 1234", "fleet_number": "SL-01"}, headers=admin)
    csv = (
        "date,fleet_number,direction,expected,reference\n"
        f"{TODAY},SL-01,LOAD,100,M-1\n"  # resolved through the fleet register
        f"{TODAY},SL-99,LOAD,100,M-2\n"
        f"yesterday,SL-01,LOAD,100,M-3\n"
        f"{TODAY},SL-01,SIDEWAYS,100,M-4\n"
    )
    out = import_csv(c, admin, csv)
    assert out["added"] == 1 and len(out["errors"]) == 3
    assert "SL-99 is not registered" in out["errors"][0]
    [line] = c.get("/api/v1/manifests", headers=admin).json()
    assert line["plate"] == "ABC 1234"
    bad = c.post(
        "/api/v1/manifests/import", files={"file": ("m.csv", b"a,b\n1,2\n")}, headers=admin
    )
    assert bad.status_code == 422
