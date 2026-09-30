"""T1.6 and the M1 exit demo: provisioning a tenant, once, and what it can then see.

"Log in as Partner Admin, create a tenant, invite a Tenant Owner, then show the
Tenant Owner cannot see the other tenant." (proposal M1 exit gate)
"""

from __future__ import annotations

import pytest
from conftest import login, make_client

from ivaas.domain.tenancy import LITZIM_ID


def _provision(c, headers, key="key-0001-abcdef", **overrides):
    body = {
        "slug": "acme-foods",
        "name": "Acme Foods",
        "owner_username": "acme.owner",
        "owner_display_name": "Acme Owner",
    } | overrides
    return c.post(
        "/api/v1/platform/tenants", json=body, headers={**headers, "Idempotency-Key": key}
    )


@pytest.fixture
def c():
    with make_client() as client:
        yield client


def test_the_m1_exit_demo(c):
    litzim = login(c, "litzim")
    made = _provision(c, litzim)
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["created"] is True
    assert body["tenant"]["partner_id"] == str(LITZIM_ID)  # a partner creates its own
    temporary = body["temporary_password"]
    assert temporary

    # the owner signs in with the password shown once, and must replace it first
    owner = login(c, "acme.owner", temporary)
    assert c.get("/api/v1/bays", headers=owner).status_code == 403
    changed = c.post(
        "/api/v1/auth/password",
        json={"current_password": temporary, "new_password": "acme owner passphrase"},
        headers=owner,
    )
    assert changed.status_code == 200, changed.text
    owner = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    me = c.get("/api/v1/auth/me", headers=owner).json()
    assert me["tenant"]["slug"] == "acme-foods" and me["roles"] == ["tenant_owner"]
    # a new tenant is empty: none of Bakers Inn's sites, sessions or people
    assert c.get("/api/v1/sites", headers=owner).json() == []
    assert c.get("/api/v1/sessions", headers=owner).json() == []
    assert {u["username"] for u in c.get("/api/v1/users", headers=owner).json()} == {"acme.owner"}
    bakers_bay = c.get("/api/v1/bays", headers=login(c, "admin")).json()[0]["id"]
    assert c.get(f"/api/v1/bays/{bakers_bay}/cameras", headers=owner).status_code == 404
    # and the provisioning is in the new tenant's own audit trail
    trail = c.get("/api/v1/audit", params={"action": "tenant_provisioned"}, headers=owner).json()
    assert [e["actor"] for e in trail] == ["litzim"]


def test_provisioning_twice_with_one_key_makes_one_tenant(c):
    platform = login(c, "platform")
    first = _provision(c, platform, partner_id=str(LITZIM_ID))
    again = _provision(c, platform, partner_id=str(LITZIM_ID))
    assert first.status_code == again.status_code == 200
    assert first.json()["tenant"]["id"] == again.json()["tenant"]["id"]
    assert again.json()["created"] is False
    assert again.json()["temporary_password"] is None  # shown once, never again
    slugs = [t["slug"] for t in c.get("/api/v1/platform/tenants", headers=platform).json()]
    assert slugs.count("acme-foods") == 1


def test_a_new_key_for_an_existing_slug_or_owner_is_refused(c):
    platform = login(c, "platform")
    assert _provision(c, platform).status_code == 200
    assert _provision(c, platform, key="key-0002-abcdef").status_code == 409
    other = _provision(c, platform, key="key-0003-abcdef", slug="other-co")
    assert other.status_code == 409  # acme.owner is taken, platform-wide


def test_pending_steps_are_reported_as_pending(c):
    steps = {s["name"]: s["done"] for s in _provision(c, login(c, "platform")).json()["steps"]}
    assert steps["tenant_record"] and steps["owner_invited"] and steps["audit_entry"]
    for later in ("idp_organisation", "storage_prefix_and_key", "edge_enrollment_tokens"):
        assert steps[later] is False, later


def test_a_partner_sees_and_creates_only_its_own_customers(c):
    litzim = login(c, "litzim")
    assert (
        _provision(c, litzim, partner_id="00000000-0000-0000-0000-000000000001").status_code == 403
    )
    tenants = c.get("/api/v1/platform/tenants", headers=litzim).json()
    assert {t["slug"] for t in tenants} == {"bakers-inn", "isolation-test"}


def test_tenant_staff_cannot_reach_the_platform(c):
    for user in ("admin", "operator"):
        h = login(c, user)
        assert c.get("/api/v1/platform/tenants", headers=h).status_code == 403
        assert _provision(c, h).status_code == 403


def test_the_idempotency_key_is_required(c):
    r = c.post(
        "/api/v1/platform/tenants",
        json={"slug": "x-co", "name": "X", "owner_username": "x.owner"},
        headers=login(c, "platform"),
    )
    assert r.status_code == 422
