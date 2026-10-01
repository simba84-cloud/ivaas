"""M7 through the API: plans and limits, idempotent usage, and the golden invoices.

The demo bay holds the POC array, 16 counting cameras and 1 plate reader, so a
Standard plan at its defaults is exactly full. Prices are the placeholder book's;
every figure checked here is worked by hand in tests/test_billing.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import SERVICE, login, make_client

from ivaas.domain.tenancy import BAKERS_INN_ID

USERS = {
    "admin": ("admin", "admin"),
    "owner": ("owner", "tenant_owner"),
    "viewer": ("viewer", "viewer"),
    "operator": ("operator", "operator"),
}


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at


@pytest.fixture
def billed():
    with make_client(local_users=USERS) as c:
        c.app.state.container.clock = clock = Clock()
        yield c, clock


def standard(c, **quantities):
    r = c.put(
        f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
        json={"plan": "standard", "quantities": quantities},
        headers=login(c, "platform"),
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_t7_3_a_17th_counting_channel_is_refused_with_an_upgrade_and_the_upgrade_lifts_it(billed):
    c, clock = billed
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]["id"]
    new = {"name": "Dock 17", "role": "overhead", "source_url": "rtsp://10.0.0.17/s"}

    # no plan: nothing is limited (and nothing billed); the 17th goes in
    assert (
        c.get("/api/v1/billing/subscription", headers=login(c, "owner")).json()["subscribed"]
        is False
    )

    sub = standard(c)
    assert sub["entitlements"]["limits"]["od_channels"] == 16
    assert sub["channels_in_use"] == {"od": 16, "lpr": 1}
    r = c.post(f"/api/v1/bays/{bay}/cameras", json=new, headers=admin)
    assert r.status_code == 402 and "16 counting channel" in r.json()["detail"]
    assert "Upgrade" in r.json()["detail"]
    lpr = {**new, "name": "Gate LPR 2", "role": "lpr"}
    assert c.post(f"/api/v1/bays/{bay}/cameras", json=lpr, headers=admin).status_code == 402

    # the owner upgrades, and the same camera is registered
    up = c.put(
        "/api/v1/billing/subscription",
        json={"plan": "standard", "quantities": {"ivaas-od-count": 24}},
        headers=login(c, "owner"),
    )
    assert up.status_code == 200 and up.json()["entitlements"]["limits"]["od_channels"] == 24
    assert c.post(f"/api/v1/bays/{bay}/cameras", json=new, headers=admin).status_code == 201
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=admin).json()]
    assert actions.count("subscription_changed") == 2


def test_t7_3_the_edge_is_served_no_more_channels_than_the_plan_allows(billed):
    from test_edge import enrol, node_headers, token

    c, clock = billed
    admin = login(c, "admin")
    bay = c.get("/api/v1/bays", headers=admin).json()[0]
    cams = c.get(f"/api/v1/bays/{bay['id']}/cameras", headers=admin).json()
    counting = [x for x in cams if x["role"] != "lpr"][:6]
    enrolled = enrol(c, token(c, admin, bay["site_id"], bay_id=bay["id"])).json()
    body = {
        "model": {"path": "/models/stacks-v2.onnx"},
        "cameras": [{"api_camera_id": x["id"], "zone": [0, 0, 1280, 720]} for x in counting],
    }
    assert (
        c.put(
            f"/api/v1/edge/nodes/{enrolled['node_id']}/config", json=body, headers=admin
        ).status_code
        == 200
    )
    standard(c, **{"ivaas-od-count": 4})  # a smaller plan than the node is set up for
    served = c.get("/api/v1/edge/config", headers=node_headers(enrolled)).json()
    assert [x["api_camera_id"] for x in served["cameras"]] == [x["id"] for x in counting[:4]]
    assert served["not_entitled"] == [x["id"] for x in counting[4:]]


def test_t7_5_a_thousand_copies_of_one_usage_event_count_once(billed):
    c, clock = billed
    standard(c)
    event = {
        "meter": "assistant_tokens",
        "quantity": "250000",
        "at": "2026-10-15T09:00:00Z",
        "key": "litellm-spend:2026-10-15:bakers-inn",
    }
    first = c.post("/api/v1/billing/usage", json=event, headers=SERVICE)
    assert first.status_code == 201 and first.json() == {"counted": True}
    for _ in range(999):
        again = c.post("/api/v1/billing/usage", json=event, headers=SERVICE)
        assert again.status_code == 200 and again.json() == {"counted": False}
    clock.at = datetime(2026, 10, 20, tzinfo=UTC)
    used = c.get("/api/v1/billing/subscription", headers=login(c, "owner")).json()
    assert used["usage_this_month"] == {"assistant_tokens": "250000"}
    bad = {**event, "meter": "crates"}
    assert c.post("/api/v1/billing/usage", json=bad, headers=SERVICE).status_code == 422


def test_t7_1_and_t7_2_october_upgraded_on_the_11th_is_invoiced_to_the_cent(billed):
    c, clock = billed
    standard(c)  # 1 Oct: Standard, 16 OD + 1 LPR
    clock.at = datetime(2026, 10, 11, tzinfo=UTC)
    c.put(
        "/api/v1/billing/subscription",
        json={"plan": "standard", "quantities": {"ivaas-od-count": 24}},
        headers=login(c, "owner"),
    )
    # before the month is over it cannot be issued, only drafted
    platform = login(c, "platform")
    url = f"/api/v1/platform/tenants/{BAKERS_INN_ID}/invoices"
    assert c.post(url, params={"period": "2026-10"}, headers=platform).status_code == 422
    draft = c.get(
        "/api/v1/billing/invoices/draft", params={"period": "2026-10"}, headers=login(c, "owner")
    ).json()
    assert draft["number"] is None and draft["total"] == "1464.95"

    clock.at = datetime(2026, 11, 2, tzinfo=UTC)
    issued = c.post(url, params={"period": "2026-10"}, headers=platform)
    assert issued.status_code == 201, issued.text
    inv = issued.json()
    assert inv["number"].startswith("IVAAS-2026-")
    assert [x["amount"] for x in inv["lines"] if x["sku"] == "ivaas-od-count"] == [
        "232.26",
        "731.61",
    ]
    assert (inv["subtotal"], inv["tax"], inv["total"]) == ("1273.87", "191.08", "1464.95")
    # made-up prices say so, on the invoice itself
    assert inv["placeholder"] and inv["stamp"] == "PLACEHOLDER PRICES: NOT FOR ISSUE"
    assert c.post(url, params={"period": "2026-10"}, headers=platform).status_code == 409
    [listed] = c.get("/api/v1/billing/invoices", headers=login(c, "owner")).json()
    assert listed["number"] == inv["number"]


def test_who_may_see_and_change_billing(billed):
    c, _ = billed
    standard(c)
    assert c.get("/api/v1/billing/price-book", headers=login(c, "owner")).json()["placeholder"]
    # an auditor (the viewer) reads invoices, as the role matrix has it; changes nothing
    viewer = login(c, "viewer")
    assert c.get("/api/v1/billing/invoices", headers=viewer).status_code == 200
    r = c.put("/api/v1/billing/subscription", json={"plan": "enterprise"}, headers=viewer)
    assert r.status_code == 403
    operator = login(c, "operator")
    assert c.get("/api/v1/billing/invoices", headers=operator).status_code == 403
    # another tenant's admin cannot reach Bakers Inn's billing through the platform routes
    other = login(c, "b-admin")
    r = c.put(
        f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
        json={"plan": "enterprise"},
        headers=other,
    )
    assert r.status_code == 403
    # a partner admin bills its own tenants only
    litzim = login(c, "litzim")
    assert (
        c.put(
            f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
            json={"plan": "standard"},
            headers=litzim,
        ).status_code
        == 200
    )
    unknown = "00000000-0000-0000-0000-000000000001"
    r = c.put(
        f"/api/v1/platform/tenants/{unknown}/subscription",
        json={"plan": "standard"},
        headers=litzim,
    )
    assert r.status_code == 404
    bad = c.put(
        f"/api/v1/platform/tenants/{BAKERS_INN_ID}/subscription",
        json={"plan": "gold"},
        headers=login(c, "platform"),
    )
    assert bad.status_code == 422 and "no plan" in bad.json()["detail"]


def test_platform_staff_reach_a_tenants_billing_by_naming_it(billed):
    """Found live: platform staff hold the billing permissions but no tenant, and the
    tenant's own routes answered 500. They are pointed to the routes that name one."""
    c, _ = billed
    standard(c)
    platform = login(c, "platform")
    for path in ("/api/v1/billing/subscription", "/api/v1/billing/invoices"):
        r = c.get(path, headers=platform)
        assert r.status_code == 403 and "/api/v1/platform/tenants/" in r.json()["detail"]
    base = f"/api/v1/platform/tenants/{BAKERS_INN_ID}"
    sub = c.get(f"{base}/subscription", headers=platform).json()
    assert sub["subscribed"] and sub["entitlements"]["plan"] == "standard"
    draft = c.get(f"{base}/invoices/draft", params={"period": "2026-10"}, headers=platform)
    assert draft.status_code == 200 and draft.json()["stamp"]
