"""Controlled bays (ADR 0004): standard units validated, traced to a driver."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ivaas.domain.crate_control import (
    AcceptReason,
    BayStandard,
    ColumnVerdict,
    CrateControlError,
    CrateTransaction,
    StackObservation,
    TransactionStatus,
    driver_balances,
    validate,
)
from ivaas.domain.drivers import Driver, DriverError, match_badge
from ivaas.domain.models import SessionDirection

OUT, BACK = SessionDirection.LOADING, SessionDirection.OFFLOADING
T0 = datetime(2026, 11, 11, 8, tzinfo=UTC)
BAY = uuid4()
STD = BayStandard(BAY, columns=2, layers=14)  # a unit is 2 columns of 14: 28 crates


def col(crates=14, *, agreement=0.9, confidence=0.8, direction=OUT, n=0):
    return StackObservation(
        session_id=uuid4(),
        track_id=n,
        camera_id=uuid4(),
        direction=direction,
        crates=crates,
        confidence=confidence,
        agreement=agreement,
        at=T0 + timedelta(seconds=n),
    )


def cols(*heights, **kw):
    return [col(h, n=i, **kw) for i, h in enumerate(heights)]


# --- validation ----------------------------------------------------------------------


def test_every_column_at_the_standard_height_verifies():
    v = validate(cols(14, 14, 14, 14), STD, declared_units=2, direction=OUT)
    assert v.status is TransactionStatus.VERIFIED
    assert (v.expected_columns, v.expected_crates, v.counted_crates) == (4, 56, 56)
    assert all(verdict is ColumnVerdict.FULL for _, verdict in v.columns)


def test_a_short_column_is_short():
    v = validate(cols(14, 14, 14, 12), STD, 2, OUT)
    assert v.status is TransactionStatus.SHORT
    assert (v.full_columns, v.non_standard_columns, v.counted_crates) == (3, 1, 54)


def test_a_missing_column_is_short():
    v = validate(cols(14, 14, 14), STD, 2, OUT)
    assert v.status is TransactionStatus.SHORT and v.counted_crates == 42


def test_an_extra_column_is_over():
    assert validate(cols(14, 14, 14), STD, 1, OUT).status is TransactionStatus.OVER


def test_the_right_total_in_odd_columns_is_not_vouched_for():
    v = validate(cols(14, 10, 4), STD, 1, OUT)
    assert v.status is TransactionStatus.NON_STANDARD and v.counted_crates == 28


def test_one_column_that_could_not_be_judged_makes_the_whole_unverified():
    obs = cols(14, 14, 14) + [col(14, agreement=0.4, n=9)]
    v = validate(obs, STD, 2, OUT)
    assert v.status is TransactionStatus.UNVERIFIED
    assert v.unsure_columns == 1
    assert v.counted_crates is None  # not 42, and not 56


@pytest.mark.parametrize("bad", [{"agreement": None}, {"confidence": 0.0}, {"agreement": 0.59}])
def test_weak_evidence_is_unsure(bad):
    v = validate([col(14, **bad)], BayStandard(BAY, 1, 14), 1, OUT)
    assert v.status is TransactionStatus.UNVERIFIED


def test_nothing_seen_is_unverified_not_zero():
    v = validate([], STD, 1, OUT)
    assert v.status is TransactionStatus.UNVERIFIED and v.counted_crates is None


def test_a_column_carried_back_cancels_the_last_one():
    obs = cols(14, 14, 14) + [col(14, direction=BACK, n=10)]
    assert validate(obs, STD, 1, OUT).status is TransactionStatus.VERIFIED


@pytest.mark.parametrize("columns, layers", [(0, 14), (1, 0), (21, 14), (2, 41)])
def test_an_impossible_standard_is_refused(columns, layers):
    with pytest.raises(CrateControlError):
        BayStandard(BAY, columns, layers)


# --- transactions --------------------------------------------------------------------


def txn(direction=OUT, units=1, driver=None):
    return CrateTransaction(
        bay_id=BAY,
        driver_id=driver or uuid4(),
        session_id=uuid4(),
        direction=direction,
        declared_units=units,
        standard_columns=2,
        standard_layers=14,
        opened_at=T0,
        checked_in_by="kiosk",
    )


def test_accepting_an_exception_keeps_what_was_measured():
    t = txn(units=2)
    t.close(validate(cols(14, 14, 14), STD, 2, OUT), T0)
    assert t.status is TransactionStatus.SHORT
    t.accept(by="sup", at=T0, reason=AcceptReason.DAMAGED_CRATES)
    assert t.status is TransactionStatus.ACCEPTED
    assert t.validated_status is TransactionStatus.SHORT
    assert t.counted_crates == 42  # not rewritten to the declared 56


def test_a_verified_transaction_has_nothing_to_accept():
    t = txn()
    t.close(validate(cols(14, 14), STD, 1, OUT), T0)
    with pytest.raises(CrateControlError):
        t.accept(by="sup", at=T0, reason=AcceptReason.OTHER, note="x")


def test_other_needs_a_note():
    t = txn()
    t.close(validate([], STD, 1, OUT), T0)
    with pytest.raises(CrateControlError):
        t.accept(by="sup", at=T0, reason=AcceptReason.OTHER)


# --- balances ------------------------------------------------------------------------


def test_driver_balance_keeps_unmeasured_transactions_out_of_the_totals():
    d = uuid4()
    out = txn(OUT, units=2, driver=d)
    out.close(validate(cols(14, 14, 14, 14), STD, 2, OUT), T0)
    back = txn(BACK, units=2, driver=d)
    back.close(validate(cols(14, 14, 14, direction=BACK), STD, 2, BACK), T0)
    lost = txn(BACK, units=1, driver=d)
    lost.close(validate([], STD, 1, BACK), T0)
    still = txn(OUT, driver=d)

    (b,) = driver_balances([out, back, lost, still])
    assert (b.issued, b.returned, b.outstanding) == (56, 42, 14)
    assert (b.unmeasured, b.unmeasured_declared_back) == (1, 28)
    assert (b.in_progress, b.exceptions) == (1, 2)


# --- drivers -------------------------------------------------------------------------


def test_badges_match_exactly_after_normalising():
    a, b = Driver("Tendai Moyo", "BI-0042"), Driver("Old Driver", "BI-0001", active=False)
    assert match_badge(" bi-0042 ", [a, b]) is a
    assert match_badge("BI-0043", [a, b]) is None
    assert match_badge("BI-0001", [a, b]) is None  # no longer active


@pytest.mark.parametrize("name, badge", [("", "BI-1"), ("Tendai", "x"), ("Tendai", "BI 0042!")])
def test_a_driver_needs_a_name_and_a_usable_badge(name, badge):
    with pytest.raises(DriverError):
        Driver(name, badge)


# --- the API -------------------------------------------------------------------------

from conftest import SERVICE, login, make_client  # noqa: E402


@pytest.fixture
def bay():
    with make_client() as c:
        admin = login(c, "admin")
        b = c.get("/api/v1/bays", headers=admin).json()[0]
        cams = c.get(f"/api/v1/bays/{b['id']}/cameras", headers=admin).json()
        choke = next(x["id"] for x in cams if x["role"] == "chokepoint")
        yield c, admin, b["id"], choke


def column(c, bay_id, cam, crates=14, agreement=0.9, direction="loading", n=0):
    body = {
        "bay_id": bay_id,
        "camera_id": cam,
        "track_id": n,
        "direction": direction,
        "crates": crates,
        "confidence": 0.9,
        "crossed_at": (datetime.now(UTC) + timedelta(seconds=n)).isoformat(),
    }
    if agreement is not None:
        body["agreement"] = agreement
    r = c.post("/api/v1/ingest/crossings", json=body, headers=SERVICE)
    assert r.status_code == 200, r.text


def setup_bay(c, admin, bay_id, pin=None):
    r = c.put(f"/api/v1/bays/{bay_id}/standard", json={"columns": 2, "layers": 14}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["crates_per_unit"] == 28
    body = {"name": "Tendai Moyo", "badge_code": "bi-0042"}
    if pin:
        body["pin"] = pin
    r = c.post("/api/v1/drivers", json=body, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()


def check_in(c, admin, bay_id, units=1, direction="loading", pin=None, badge="BI0042"):
    return c.post(
        f"/api/v1/bays/{bay_id}/check-in",
        json={"badge": badge, "pin": pin, "direction": direction, "declared_units": units},
        headers=admin,
    )


def close(c, admin, txn_id):
    r = c.post(f"/api/v1/crate-transactions/{txn_id}/close", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_dispatch_of_standard_units_is_verified_and_lands_on_the_driver(bay):
    c, admin, bay_id, choke = bay
    driver = setup_bay(c, admin, bay_id)
    r = check_in(c, admin, bay_id, units=1)
    assert r.status_code == 201, r.text
    txn = r.json()
    assert txn["driver_name"] == "Tendai Moyo" and txn["expected_crates"] == 28
    column(c, bay_id, choke, n=1)
    column(c, bay_id, choke, n=2)
    live = c.get(f"/api/v1/bays/{bay_id}/transaction", headers=admin).json()
    assert live["progress"]["full_columns"] == 2

    done = close(c, admin, txn["id"])
    assert done["status"] == "verified" and done["counted_crates"] == 28
    assert c.get(f"/api/v1/bays/{bay_id}/transaction", headers=admin).json() is None
    (bal,) = c.get("/api/v1/drivers/balances", headers=admin).json()
    assert bal["driver_id"] == driver["id"]
    assert (bal["issued"], bal["returned"], bal["outstanding"]) == (28, 0, 28)


def test_a_short_return_is_an_exception_a_supervisor_can_accept_without_rewriting_it(bay):
    c, admin, bay_id, choke = bay
    setup_bay(c, admin, bay_id)
    txn = check_in(c, admin, bay_id, direction="offloading").json()
    column(c, bay_id, choke, direction="offloading", n=1)
    column(c, bay_id, choke, crates=12, direction="offloading", n=2)
    done = close(c, admin, txn["id"])
    assert done["status"] == "short" and done["counted_crates"] == 26

    listed = c.get("/api/v1/crate-transactions?exceptions_only=true", headers=admin).json()
    assert [t["id"] for t in listed] == [txn["id"]]
    r = c.post(
        f"/api/v1/crate-transactions/{txn['id']}/accept",
        json={"reason": "damaged_crates"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted" and r.json()["counted_crates"] == 26
    assert r.json()["validated_status"] == "short"


def test_columns_from_an_old_node_cannot_be_vouched_for(bay):
    c, admin, bay_id, choke = bay
    setup_bay(c, admin, bay_id)
    txn = check_in(c, admin, bay_id).json()
    column(c, bay_id, choke, agreement=None, n=1)
    column(c, bay_id, choke, n=2)
    done = close(c, admin, txn["id"])
    assert done["status"] == "unverified" and done["counted_crates"] is None
    (bal,) = c.get("/api/v1/drivers/balances", headers=admin).json()
    assert bal["issued"] == 0 and bal["unmeasured"] == 1 and bal["unmeasured_declared_out"] == 28


def test_a_wrong_pin_and_an_unknown_badge_are_refused_alike(bay):
    c, admin, bay_id, _ = bay
    setup_bay(c, admin, bay_id, pin="4821")
    wrong = check_in(c, admin, bay_id, pin="0000")
    unknown = check_in(c, admin, bay_id, badge="BI-9999", pin="4821")
    assert wrong.status_code == unknown.status_code == 409
    assert wrong.json() == unknown.json()
    assert check_in(c, admin, bay_id, pin="4821").status_code == 201


def test_one_driver_at_a_time(bay):
    c, admin, bay_id, _ = bay
    setup_bay(c, admin, bay_id)
    assert check_in(c, admin, bay_id).status_code == 201
    assert check_in(c, admin, bay_id).status_code == 409


def test_a_bay_without_a_standard_cannot_check_drivers_in(bay):
    c, admin, bay_id, _ = bay
    c.post("/api/v1/drivers", json={"name": "Tendai", "badge_code": "BI0042"}, headers=admin)
    assert check_in(c, admin, bay_id).status_code == 422


def test_uncontrolled_bays_keep_no_columns(bay):
    c, admin, bay_id, choke = bay
    column(c, bay_id, choke, n=1)  # no standard set: counting carries on as before
    s = c.get("/api/v1/sessions", headers=admin).json()[0]
    assert s["ai_count"] == 14


def test_the_pin_never_leaves_the_api(bay):
    c, admin, bay_id, _ = bay
    d = setup_bay(c, admin, bay_id, pin="4821")
    assert d["has_pin"] is True and "pin" not in d and "pin_hash" not in d
    listed = c.get("/api/v1/drivers", headers=admin).text
    assert "4821" not in listed and "argon2" not in listed
    audit = c.get("/api/v1/audit", headers=admin).text
    assert "4821" not in audit and "argon2" not in audit


def test_a_badge_is_held_by_one_driver(bay):
    c, admin, bay_id, _ = bay
    setup_bay(c, admin, bay_id)
    r = c.post("/api/v1/drivers", json={"name": "Other", "badge_code": "BI 0042"}, headers=admin)
    assert r.status_code == 409


def test_drivers_import_from_csv(bay):
    c, admin, _, _ = bay
    csv_body = "name,badge_code,employee_ref\nTendai Moyo,BI-0042,E1\nNo Badge,,E2\n"
    r = c.post(
        "/api/v1/drivers/import",
        files={"file": ("drivers.csv", csv_body, "text/csv")},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1 and len(r.json()["errors"]) == 1


def test_only_a_manager_accepts_an_exception_never_the_operator_at_the_kiosk(bay):
    c, admin, bay_id, choke = bay
    setup_bay(c, admin, bay_id)
    operator = login(c, "operator")
    txn = check_in(c, operator, bay_id).json()
    column(c, bay_id, choke, crates=12, n=1)
    assert close(c, operator, txn["id"])["status"] == "short"
    body = {"reason": "damaged_crates"}
    r = c.post(f"/api/v1/crate-transactions/{txn['id']}/accept", json=body, headers=operator)
    assert r.status_code == 403
    r = c.post(f"/api/v1/crate-transactions/{txn['id']}/accept", json=body, headers=admin)
    assert r.status_code == 200 and r.json()["accepted_by"] == "admin"


def test_a_driver_is_edited_and_their_pin_changed_without_the_pin_leaving_the_api(bay):
    c, admin, bay_id, _ = bay
    d = setup_bay(c, admin, bay_id)
    (std,) = c.get("/api/v1/bay-standards", headers=admin).json()
    assert std["bay_id"] == bay_id and std["crates_per_unit"] == 28

    body = {"name": "Tendai M. Moyo", "badge_code": "BI0042", "employee_ref": "E9", "active": True}
    r = c.put(f"/api/v1/drivers/{d['id']}", json=body, headers=admin)
    assert r.status_code == 200 and r.json()["name"] == "Tendai M. Moyo"
    assert r.json()["has_pin"] is False  # an edit never touches the PIN

    r = c.put(f"/api/v1/drivers/{d['id']}/pin", json={"pin": "7310"}, headers=admin)
    assert r.status_code == 200 and r.json()["has_pin"] is True and "7310" not in r.text
    assert check_in(c, admin, bay_id, pin="7310").status_code == 201

    r = c.put(f"/api/v1/drivers/{d['id']}/pin", json={"pin": "12"}, headers=admin)
    assert r.status_code == 422  # a PIN is 4 to 8 digits
    r = c.put(f"/api/v1/drivers/{d['id']}/pin", json={"pin": None}, headers=admin)
    assert r.status_code == 200 and r.json()["has_pin"] is False
