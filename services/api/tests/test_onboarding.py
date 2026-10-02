"""M8, T8.1: a partner admin onboards a tenant end to end, creation to first edge node.

The partner gets the install's steps inside the new tenant (its first site and bay,
enrollment tokens, whether the node reports) and nothing else: no topology, no
counts, no other partner's customers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import login, make_client
from test_billing_api import USERS, Clock, direct_tenant
from test_edge import enrol, node_headers

from ivaas.domain.tenancy import BAKERS_INN_ID

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def onboard():
    with make_client(local_users=USERS) as c:
        c.app.state.container.clock = clock = Clock()
        clock.at = T0
        yield c, clock


def provision(c, who="litzim", slug="chipo-foods"):
    r = c.post(
        "/api/v1/platform/tenants",
        json={"slug": slug, "name": "Chipo Foods", "owner_username": f"{slug}.owner"},
        headers={**login(c, who), "Idempotency-Key": f"onboard-{slug}-0001"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def progress(c, tenant, who="litzim"):
    r = c.get(f"/api/v1/platform/tenants/{tenant}/onboarding", headers=login(c, who))
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {s["name"]: s for s in body["steps"]}


def test_t8_1_litzim_onboards_a_tenant_to_a_reporting_node_inside_thirty_minutes(onboard):
    c, clock = onboard
    made = provision(c)
    tenant = made["tenant"]["id"]
    base = f"/api/v1/platform/tenants/{tenant}"
    litzim = login(c, "litzim")

    body, steps = progress(c, tenant)
    assert steps["tenant_created"]["done"] and steps["tenant_created"]["at"]
    assert not any(s["done"] for n, s in steps.items() if n != "tenant_created")
    assert steps["node_reporting"]["detail"] == "never heard from"
    # nothing enrolled: no time to report, not zero
    assert body["seconds_to_first_node"] is None and body["within_target"] is None

    clock.at = T0 + timedelta(minutes=3)
    r = c.put(f"{base}/subscription", json={"plan": "standard"}, headers=litzim)
    assert r.status_code == 200, r.text

    temporary = made["temporary_password"]
    owner = login(c, "chipo-foods.owner", temporary)
    c.post(
        "/api/v1/auth/password",
        json={"current_password": temporary, "new_password": "a long new password 1"},
        headers=owner,
    )

    clock.at = T0 + timedelta(minutes=6)
    site = c.post(
        f"{base}/onboarding/site",
        json={"site_name": "Msasa Bakery", "timezone": "Africa/Harare", "bay_name": "Dock 1"},
        headers=litzim,
    )
    assert site.status_code == 201, site.text
    site = site.json()
    assert site["site"]["timezone"] == "Africa/Harare"

    clock.at = T0 + timedelta(minutes=10)
    tok = c.post(
        f"{base}/onboarding/enrollment-tokens",
        json={"site_id": site["site"]["id"], "bay_id": site["bay"]["id"], "name": "Msasa edge"},
        headers=litzim,
    )
    assert tok.status_code == 201, tok.text
    assert tok.json()["token"].startswith("ivaas-enr-")

    clock.at = T0 + timedelta(minutes=12)
    enrolled = enrol(c, tok.json()["token"], hostname="msasa-edge-01").json()
    clock.at = T0 + timedelta(minutes=13)
    beat = c.post(
        "/api/v1/edge/heartbeat", json={"version": "0.4.0"}, headers=node_headers(enrolled)
    )
    assert beat.status_code == 200

    body, steps = progress(c, tenant)
    assert all(s["done"] for s in steps.values()), steps
    assert steps["plan_set"]["detail"] == "standard"
    assert steps["site_and_bay"]["detail"] == "Msasa Bakery, 1 bay"
    assert steps["enrollment_token"]["detail"] == "1 made, 1 used"
    assert body["seconds_to_first_node"] == 12 * 60
    assert body["target_seconds"] == 30 * 60 and body["within_target"] is True
    (node,) = body["nodes"]
    assert node["health"] == "online" and node["version"] == "0.4.0"
    assert "config" not in node  # what the installer needs, no more

    # the tenant's own log says who set it up; the node is the tenant's alone
    actions = {
        (e["action"], e["actor"]) for e in c.get("/api/v1/audit", headers=owner_after(c)).json()
    }
    assert {("site_created", "litzim"), ("edge_token_created", "litzim")} <= actions
    bakers = c.get("/api/v1/edge/nodes", headers=login(c, "admin")).json()
    assert enrolled["node_id"] not in {n["id"] for n in bakers}


def owner_after(c):
    return login(c, "chipo-foods.owner", "a long new password 1")


def test_a_late_first_node_is_reported_late_not_hidden(onboard):
    c, clock = onboard
    tenant = provision(c)["tenant"]["id"]
    base = f"/api/v1/platform/tenants/{tenant}"
    litzim = login(c, "litzim")
    site = c.post(
        f"{base}/onboarding/site", json={"site_name": "S", "bay_name": "B"}, headers=litzim
    ).json()
    tok = c.post(
        f"{base}/onboarding/enrollment-tokens",
        json={"site_id": site["site"]["id"], "name": "edge"},
        headers=litzim,
    ).json()["token"]
    clock.at = T0 + timedelta(minutes=45)
    enrol(c, tok)
    body, steps = progress(c, tenant)
    assert body["seconds_to_first_node"] == 45 * 60 and body["within_target"] is False
    # enrolled but never heard from is not reporting
    assert steps["node_enrolled"]["done"] and not steps["node_reporting"]["done"]
    assert body["nodes"][0]["health"] == "never_seen"


def test_the_partner_sets_up_the_first_site_only_and_reaches_no_tenant_data(onboard):
    c, _ = onboard
    tenant = provision(c)["tenant"]["id"]
    base = f"/api/v1/platform/tenants/{tenant}"
    litzim = login(c, "litzim")
    first = {"site_name": "Msasa", "bay_name": "Dock 1"}
    assert c.post(f"{base}/onboarding/site", json=first, headers=litzim).status_code == 201
    again = c.post(f"{base}/onboarding/site", json=first, headers=litzim)
    assert again.status_code == 409 and "its own admins" in again.json()["detail"]
    # onboarding is not a way in: the partner still holds no tenant data
    assert c.get("/api/v1/sites", headers=litzim).status_code == 403
    assert c.get("/api/v1/edge/nodes", headers=litzim).status_code == 403


def test_onboarding_reaches_only_the_callers_own_customers(onboard):
    c, _ = onboard
    litzim = login(c, "litzim")
    direct, _ = direct_tenant(c)  # Cassava's own customer, not LITZIM's
    for method, path, body in (
        ("get", "onboarding", None),
        ("post", "onboarding/site", {"site_name": "S", "bay_name": "B"}),
    ):
        r = c.request(
            method.upper(), f"/api/v1/platform/tenants/{direct}/{path}", json=body, headers=litzim
        )
        assert r.status_code == 404, (path, r.text)
    # a tenant's own admin is not a provisioner
    r = c.get(f"/api/v1/platform/tenants/{BAKERS_INN_ID}/onboarding", headers=login(c, "admin"))
    assert r.status_code == 403
    # platform staff onboard anyone
    assert progress(c, direct, who="platform")[0]["tenant"]["id"] == direct


def test_a_token_is_only_for_the_tenants_own_sites(onboard):
    c, _ = onboard
    tenant = provision(c)["tenant"]["id"]
    litzim = login(c, "litzim")
    bakers_site = c.get("/api/v1/sites", headers=login(c, "admin")).json()[0]["id"]
    r = c.post(
        f"/api/v1/platform/tenants/{tenant}/onboarding/enrollment-tokens",
        json={"site_id": bakers_site, "name": "edge"},
        headers=litzim,
    )
    assert r.status_code == 404


def test_a_suspended_tenant_is_not_installed_into(onboard):
    c, _ = onboard
    tenant = provision(c)["tenant"]["id"]
    base = f"/api/v1/platform/tenants/{tenant}"
    litzim = login(c, "litzim")
    held = c.put(f"{base}/hold", json={"on_hold": True}, headers=litzim)
    assert held.json()["status"] == "suspended"
    r = c.post(f"{base}/onboarding/site", json={"site_name": "S", "bay_name": "B"}, headers=litzim)
    assert r.status_code == 402 and "suspended" in r.json()["detail"]
    tenants = c.get("/api/v1/platform/tenants", headers=litzim).json()
    assert next(t for t in tenants if t["id"] == tenant)["on_hold"] is True


def test_a_cancelled_tenant_is_not_installed_into(onboard):
    """Found live: the console offered an enrollment token for a cancelled tenant."""
    c, _ = onboard
    tenant = provision(c)["tenant"]["id"]
    base = f"/api/v1/platform/tenants/{tenant}"
    litzim, platform = login(c, "litzim"), login(c, "platform")
    site = c.post(
        f"{base}/onboarding/site", json={"site_name": "S", "bay_name": "B"}, headers=litzim
    ).json()
    c.post(f"{base}/cancel", json={"confirm": "chipo-foods"}, headers=platform)
    r = c.post(
        f"{base}/onboarding/enrollment-tokens",
        json={"site_id": site["site"]["id"], "name": "edge"},
        headers=litzim,
    )
    assert r.status_code == 409 and "cancelled" in r.json()["detail"]
