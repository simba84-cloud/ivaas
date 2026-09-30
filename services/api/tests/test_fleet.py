"""M5: the fleet register, unidentified loads, and people's corrections (T5.1, T5.2, T5.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import SERVICE, login, make_client

from ivaas.domain.fleet import Identification, Vehicle, identification, match_vehicle

FLEET = [Vehicle("ABC 1234"), Vehicle("ABD 5678"), Vehicle("XYZ 9999", active=False)]


# --- matching (T5.1) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "read, plate",
    [
        ("ABC 1234", "ABC 1234"),
        ("abc1234", "ABC 1234"),  # spacing and case
        ("A8C I234", "ABC 1234"),  # the characters OCR confuses
        ("ABC 1Z34", "ABC 1234"),
        ("ABC 124", "ABC 1234"),  # one character missed
    ],
)
def test_reads_match_the_registered_truck(read, plate):
    m = match_vehicle(read, FLEET)
    assert m is not None and m.vehicle.plate == plate


@pytest.mark.parametrize("read", ["QQQ 0001", "", None, "XYZ 9999"])
def test_no_match_is_better_than_a_guess(read):
    """Unknown plates, nothing read, and trucks no longer in service match nothing."""
    assert match_vehicle(read, FLEET) is None


def test_a_read_equally_close_to_two_trucks_matches_neither():
    twins = [Vehicle("ABC 1234"), Vehicle("ABC 1235")]
    assert match_vehicle("ABC 123", twins) is None


def test_identification_is_honest_about_what_is_known():
    assert identification(None, None, True) is Identification.UNIDENTIFIED
    assert identification("QQQ 1", None, True) is Identification.UNREGISTERED
    assert identification("QQQ 1", None, False) is Identification.UNCHECKED  # no register
    assert identification("ABC 1234", FLEET[0].id, True) is Identification.REGISTERED


# --- the API -----------------------------------------------------------------------------


@pytest.fixture
def bay():
    with make_client() as c:
        admin = login(c, "admin")
        b = c.get("/api/v1/bays", headers=admin).json()[0]
        cams = c.get(f"/api/v1/bays/{b['id']}/cameras", headers=admin).json()
        choke = next(x["id"] for x in cams if x["role"] == "chokepoint")
        lpr = next(x["id"] for x in cams if x["role"] == "lpr")
        yield c, admin, b["id"], choke, lpr


def plate(c, bay_id, cam, text):
    body = {
        "bay_id": bay_id,
        "camera_id": cam,
        "plate": text,
        "confidence": 0.95,
        "read_at": datetime.now(UTC).isoformat(),
    }
    r = c.post("/api/v1/ingest/plates", json=body, headers=SERVICE)
    assert r.status_code == 200, r.text
    return r.json()


def crossing(c, bay_id, cam, crates=12, direction="loading"):
    body = {
        "bay_id": bay_id,
        "camera_id": cam,
        "track_id": 1,
        "direction": direction,
        "crates": crates,
        "confidence": 0.9,
        "crossed_at": datetime.now(UTC).isoformat(),
    }
    r = c.post("/api/v1/ingest/crossings", json=body, headers=SERVICE)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_read_is_filed_under_the_registered_truck_with_the_raw_read_kept(bay):
    c, admin, bay_id, _, lpr = bay
    truck = c.post(
        "/api/v1/fleet", json={"plate": "ABC 1234", "operator": "Superlink"}, headers=admin
    ).json()
    s = plate(c, bay_id, lpr, "A8C I234")
    assert s["plate"] == "ABC 1234" and s["plate_read"] == "A8C I234"
    assert s["vehicle_id"] == truck["id"] and s["identified_by"] == "lpr"
    assert s["identification"] == "registered"


def test_a_plate_outside_the_register_is_kept_as_read_and_flagged(bay):
    c, admin, bay_id, _, lpr = bay
    c.post("/api/v1/fleet", json={"plate": "ABC 1234"}, headers=admin)
    s = plate(c, bay_id, lpr, "QQQ 0001")
    assert s["plate"] == "QQQ 0001" and s["vehicle_id"] is None
    listed = c.get("/api/v1/sessions", headers=admin).json()[0]
    assert listed["identification"] == "unregistered"


def test_without_a_register_nothing_claims_to_be_unregistered(bay):
    c, admin, bay_id, _, lpr = bay
    plate(c, bay_id, lpr, "QQQ 0001")
    assert c.get("/api/v1/sessions", headers=admin).json()[0]["identification"] == "unchecked"


def test_crates_at_an_idle_bay_open_an_unidentified_load_instead_of_vanishing(bay):
    """T5.2: the LPR camera missed the truck; its crates must still be counted."""
    c, admin, bay_id, choke, _ = bay
    s = crossing(c, bay_id, choke, crates=14, direction="offloading")
    assert s["ai_count"] == 14 and s["direction"] == "offloading"
    assert s["plate"] is None
    listed = c.get("/api/v1/sessions", headers=admin).json()[0]
    assert listed["identification"] == "unidentified"


def test_a_later_read_identifies_the_load(bay):
    c, admin, bay_id, choke, lpr = bay
    c.post("/api/v1/fleet", json={"plate": "ABC 1234"}, headers=admin)
    first = crossing(c, bay_id, choke)
    s = plate(c, bay_id, lpr, "ABC 1234")
    assert s["id"] == first["id"] and s["identification"] == "registered" and s["ai_count"] == 12


def test_with_auto_open_off_crates_at_an_idle_bay_are_still_dropped(bay):
    c, admin, bay_id, choke, _ = bay
    c.put("/api/v1/settings/auto_open_direction", json={"value": ""}, headers=admin)
    body = {
        "bay_id": bay_id,
        "camera_id": choke,
        "track_id": 1,
        "direction": "loading",
        "crates": 5,
        "confidence": 0.9,
        "crossed_at": datetime.now(UTC).isoformat(),
    }
    assert c.post("/api/v1/ingest/crossings", json=body, headers=SERVICE).json() is None
    assert c.get("/api/v1/sessions", headers=admin).json() == []


def test_an_operator_says_which_truck_it_was_and_it_is_audited(bay):
    c, admin, bay_id, choke, lpr = bay
    truck = c.post("/api/v1/fleet", json={"plate": "ABC 1234"}, headers=admin).json()
    s = crossing(c, bay_id, choke)
    operator = login(c, "operator")
    r = c.post(
        f"/api/v1/sessions/{s['id']}/vehicle",
        json={"vehicle_id": truck["id"], "note": "seen at the gate"},
        headers=operator,
    )
    assert r.status_code == 200, r.text
    assert r.json()["identification"] == "registered" and r.json()["identified_by"] == "operator"
    # the camera then reads the same truck: it confirms, it does not take over
    after = plate(c, bay_id, lpr, "ABC 1234")
    assert after["identified_by"] == "operator"
    [entry] = c.get("/api/v1/audit", params={"action": "session_identified"}, headers=admin).json()
    assert entry["actor"] == "operator" and entry["detail"]["before"]["plate"] is None
    assert entry["detail"]["after"]["plate"] == "ABC 1234"


def test_a_correction_changes_the_count_of_record_and_never_the_ai_count(bay):
    """T5.5: original and new are both stored, with a reason; accuracy stays on the AI."""
    c, admin, bay_id, choke, _ = bay
    for crates in (40, 40, 20):  # a crossing is at most 40 crates
        s = crossing(c, bay_id, choke, crates=crates)
    c.post(f"/api/v1/sessions/{s['id']}/close", headers=admin)
    c.post(f"/api/v1/sessions/{s['id']}/reconcile", json={"manual_count": 96}, headers=admin)
    before = c.get("/api/v1/sessions", headers=admin).json()[0]

    r = c.post(
        f"/api/v1/sessions/{s['id']}/override",
        json={"count": 96, "reason": "person_or_forklift"},
        headers=login(c, "operator"),
    )
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["ai_count"] == 100 and o["override_count"] == 96 and o["count_of_record"] == 96
    assert o["override_by"] == "operator" and o["override_reason"] == "person_or_forklift"
    assert o["accuracy"] == before["accuracy"], "a correction must not flatter the accuracy"
    [entry] = c.get("/api/v1/audit", params={"action": "count_overridden"}, headers=admin).json()
    assert entry["detail"]["ai_count"] == 100 and entry["detail"]["after"] == 96


def test_a_correction_needs_a_real_reason_and_the_right_role(bay):
    c, admin, bay_id, choke, _ = bay
    s = crossing(c, bay_id, choke)
    url = f"/api/v1/sessions/{s['id']}/override"
    assert c.post(url, json={"count": 3, "reason": "other"}, headers=admin).status_code == 422
    assert (
        c.post(
            url, json={"count": 3, "reason": "other", "note": "a crate fell"}, headers=admin
        ).status_code
        == 200
    )
    assert (
        c.post(
            url, json={"count": 3, "reason": "double_counted"}, headers=login(c, "viewer")
        ).status_code
        == 403
    )


def test_one_truck_cannot_be_registered_twice_under_two_spellings(bay):
    c, admin, *_ = bay
    assert c.post("/api/v1/fleet", json={"plate": "ABC 1234"}, headers=admin).status_code == 201
    r = c.post("/api/v1/fleet", json={"plate": "A8C-I234"}, headers=admin)
    assert r.status_code == 409 and "ABC 1234" in r.json()["detail"]
    assert (
        c.post("/api/v1/fleet", json={"plate": "x"}, headers=login(c, "operator")).status_code
        == 403
    )


def test_the_register_imports_from_csv_and_reports_bad_lines(bay):
    c, admin, *_ = bay
    c.post("/api/v1/fleet", json={"plate": "ABC 1234", "fleet_number": "old"}, headers=admin)
    csv_text = (
        "Plate,Fleet_Number,Operator\n"
        "ABC 1234,SL-01,Superlink\n"  # already there: updated, not duplicated
        "ABD 5678,SL-02,Superlink\n"
        "?,SL-03,Superlink\n"  # unusable
    )
    r = c.post(
        "/api/v1/fleet/import",
        files={"file": ("fleet.csv", csv_text.encode())},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "added": 1,
        "updated": 1,
        "errors": ["line 4: a plate needs at least two letters or digits"],
    }
    fleet = {v["plate"]: v for v in c.get("/api/v1/fleet", headers=admin).json()}
    assert set(fleet) == {"ABC 1234", "ABD 5678"} and fleet["ABC 1234"]["fleet_number"] == "SL-01"
