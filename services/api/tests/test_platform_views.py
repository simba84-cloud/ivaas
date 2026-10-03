"""M8: Cassava's console across tenants: the fleet's health and the revenue issued."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import login
from test_billing_api import billed, direct_tenant, litzims_october, standard  # noqa: F401
from test_edge import enrol, node_headers, token

from ivaas.domain.tenancy import BAKERS_INN_ID, ISOLATION_TEST_ID


def test_the_fleet_shows_each_tenants_nodes_and_which_need_attention(billed):  # noqa: F811
    c, clock = billed
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]
    cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()[:2]
    up = enrol(c, token(c, admin, bay["site_id"], name="Dock edge"), hostname="dock").json()
    c.post(
        "/api/v1/edge/heartbeat",
        json={
            "version": "0.4.0",
            "spool_pending": 3,
            "cameras": [
                {"api_camera_id": cams[0]["id"], "connected": True},
                {"api_camera_id": cams[1]["id"], "connected": False},
            ],
        },
        headers=node_headers(up),
    )
    enrol(c, token(c, admin, bay["site_id"], name="Spare edge"), hostname="spare")  # never heard

    fleet = {
        f["tenant"]["slug"]: f
        for f in c.get("/api/v1/platform/fleet", headers=login(c, "platform")).json()
    }
    bakers = fleet["bakers-inn"]
    by = {n["name"]: n for n in bakers["nodes"]}
    assert by["Dock edge"]["health"] == "online" and by["Dock edge"]["version"] == "0.4.0"
    assert (by["Dock edge"]["cameras_reported"], by["Dock edge"]["cameras_connected"]) == (2, 1)
    assert by["Dock edge"]["spool_pending"] == 3
    # never heard from: said so, with nothing about cameras it never reported
    assert (
        by["Spare edge"]["health"] == "never_seen" and by["Spare edge"]["cameras_reported"] is None
    )
    assert bakers["health"] == {"online": 1, "never_seen": 1} and bakers["needs_attention"]
    # nothing installed is not a problem to flag, and not a green tick either: no nodes
    assert fleet["isolation-test"]["nodes"] == [] and not fleet["isolation-test"]["needs_attention"]


def test_a_partner_sees_its_own_customers_fleet_and_a_tenant_sees_none(billed):  # noqa: F811
    c, _ = billed
    direct, _ = direct_tenant(c)
    mine = {
        f["tenant"]["id"]
        for f in c.get("/api/v1/platform/fleet", headers=login(c, "litzim")).json()
    }
    assert str(BAKERS_INN_ID) in mine and str(ISOLATION_TEST_ID) in mine and direct not in mine
    everyone = {
        f["tenant"]["id"]
        for f in c.get("/api/v1/platform/fleet", headers=login(c, "platform")).json()
    }
    assert direct in everyone
    assert c.get("/api/v1/platform/fleet", headers=login(c, "admin")).status_code == 403


def test_revenue_is_what_was_issued_month_by_month_and_nothing_issued_is_not_zero(billed):  # noqa: F811
    c, clock = billed
    tenant, _ = direct_tenant(c)
    standard(c, tenant)  # 1 Oct: Standard 16+1, all month: 1,030.00 before tax
    litzim_id, _ = litzims_october(c, clock)  # LITZIM's October wholesale: 1,062.42
    platform = login(c, "platform")
    direct = c.post(
        f"/api/v1/platform/tenants/{tenant}/invoices",
        params={"period": "2026-10"},
        headers=platform,
    ).json()
    c.post(
        f"/api/v1/platform/partners/{litzim_id}/invoices",
        params={"period": "2026-10"},
        headers=platform,
    )
    c.post(
        f"/api/v1/platform/tenants/{tenant}/invoices/{direct['number']}/payments",
        json={"amount": "500.00", "reference": "BT-1"},
        headers=platform,
    )

    r = c.get("/api/v1/platform/revenue", params={"months": 3}, headers=platform).json()
    by = {m["period"]: m for m in r["months"]}
    assert list(by) == ["2026-09", "2026-10", "2026-11"]
    october = by["2026-10"]
    assert october["invoices"] == 2
    assert (october["direct"], october["wholesale"]) == ("1030.00", "1062.42")
    assert october["subtotal"] == "2092.42"  # 1,030.00 + 1,062.42
    assert october["tax"] == "313.86"  # 154.50 + 159.36, each invoice taxed on its own
    assert october["total"] == "2406.28" and october["paid"] == "500.00"
    assert october["outstanding"] == "1906.28" and october["placeholder"] and r["placeholder"]
    # September and November: nothing issued, so no figure rather than 0.00
    for empty in ("2026-09", "2026-11"):
        assert by[empty]["invoices"] == 0 and by[empty]["total"] is None
    assert r["by_payer"] == {"Direct Co": "1030.00", "LITZIM": "1062.42"}
    assert r["overdue"] == 0  # due 16 Nov, and it is the 2nd

    clock.at = datetime(2026, 11, 20, tzinfo=UTC)
    late = c.get("/api/v1/platform/revenue", headers=platform).json()
    assert late["overdue"] == 2 and late["overdue_amount"] == "1906.28"


def test_revenue_is_cassavas_alone(billed):  # noqa: F811
    c, _ = billed
    assert c.get("/api/v1/platform/revenue", headers=login(c, "litzim")).status_code == 403
    assert c.get("/api/v1/platform/revenue", headers=login(c, "owner")).status_code == 403
    assert c.get("/api/v1/platform/revenue", headers=login(c, "support")).status_code == 403
    empty = c.get("/api/v1/platform/revenue", headers=login(c, "platform")).json()
    assert all(m["total"] is None for m in empty["months"]) and not empty["placeholder"]


@pytest.mark.parametrize("months", [0, 25])
def test_revenue_months_are_bounded(billed, months):  # noqa: F811
    c, _ = billed
    r = c.get("/api/v1/platform/revenue", params={"months": months}, headers=login(c, "platform"))
    assert r.status_code == 422


def test_revoked_nodes_are_retired_and_never_raise_attention(billed):  # noqa: F811
    """Found live: a tenant whose sixteen test nodes were all revoked was flagged,
    because a revoked node's last report still said a camera was down."""
    c, _ = billed
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]
    cam = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()[0]
    gone = enrol(c, token(c, admin, bay["site_id"], name="Old edge")).json()
    c.post(
        "/api/v1/edge/heartbeat",
        json={"cameras": [{"api_camera_id": cam["id"], "connected": False}]},
        headers=node_headers(gone),
    )
    assert c.delete(f"/api/v1/edge/nodes/{gone['node_id']}", headers=admin).status_code == 204
    fleet = {
        f["tenant"]["slug"]: f
        for f in c.get("/api/v1/platform/fleet", headers=login(c, "platform")).json()
    }
    bakers = fleet["bakers-inn"]
    assert bakers["health"] == {"revoked": 1} and not bakers["needs_attention"]
