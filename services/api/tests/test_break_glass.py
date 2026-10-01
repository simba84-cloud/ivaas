"""M8, T8.3: support asks for break-glass access, the tenant owner must approve, the
access ends on its own, and every request made with it is in the tenant's audit log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import login, make_client
from test_billing_api import USERS, Clock

from ivaas.domain.break_glass import BreakGlassError, BreakGlassGrant, GrantState
from ivaas.domain.tenancy import BAKERS_INN_ID, ISOLATION_TEST_ID

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
GLASS = "X-IVaaS-Break-Glass"


# --- the grant itself ----------------------------------------------------------------
def grant(minutes=60) -> BreakGlassGrant:
    return BreakGlassGrant.request(
        uuid4(), "support", "counts at bay 2 look doubled", timedelta(minutes=minutes), T0
    )


def test_access_runs_from_approval_for_the_time_asked_and_then_ends():
    g = grant(60)
    assert g.state(T0) is GrantState.PENDING and g.expires_at is None
    g.decide(True, "owner", T0 + timedelta(hours=3))  # approved late: still a full hour
    assert g.expires_at == T0 + timedelta(hours=4)
    assert g.state(T0 + timedelta(hours=3, minutes=59)) is GrantState.ACTIVE
    assert g.state(T0 + timedelta(hours=4)) is GrantState.EXPIRED
    with pytest.raises(BreakGlassError, match="already expired"):
        g.end("owner", T0 + timedelta(hours=5))


def test_requests_lapse_unanswered_and_are_decided_once():
    g = grant()
    assert g.state(T0 + timedelta(hours=24)) is GrantState.LAPSED
    with pytest.raises(BreakGlassError, match="lapsed"):
        g.decide(True, "owner", T0 + timedelta(hours=25))
    g = grant()
    g.decide(False, "owner", T0)
    assert g.state(T0) is GrantState.DENIED
    with pytest.raises(BreakGlassError, match="denied, not waiting"):
        g.decide(True, "owner", T0)


def test_a_request_needs_a_reason_and_a_bounded_length():
    with pytest.raises(BreakGlassError, match="say why"):
        BreakGlassGrant.request(uuid4(), "s", " ", timedelta(hours=1), T0)
    for bad in (timedelta(minutes=5), timedelta(hours=9)):
        with pytest.raises(BreakGlassError, match="15 minutes and 8 hours"):
            BreakGlassGrant.request(uuid4(), "s", "why", bad, T0)


# --- through the API -------------------------------------------------------------------
@pytest.fixture
def glass():
    with make_client(local_users=USERS) as c:
        c.app.state.container.clock = clock = Clock()
        clock.at = T0
        yield c, clock


def ask(c, tenant=BAKERS_INN_ID, minutes=60, who="support"):
    return c.post(
        f"/api/v1/platform/tenants/{tenant}/break-glass",
        json={"reason": "Bakers Inn reports bay counts doubled since Monday", "minutes": minutes},
        headers=login(c, who),
    )


def as_support(c, grant_id):
    return {**login(c, "support"), GLASS: grant_id}


def test_t8_3_support_sees_nothing_until_the_owner_approves_then_reads_only(glass):
    c, clock = glass
    support, owner = login(c, "support"), login(c, "owner")
    # no standing access
    assert c.get("/api/v1/sessions", headers=support).status_code == 403
    r = ask(c)
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["state"] == "pending" and g["expires_at"] is None
    assert g["tenant_name"] == "Bakers Inn"
    # pending is not access
    r = c.get("/api/v1/sessions", headers=as_support(c, g["id"]))
    assert r.status_code == 403 and "ended or was not granted" in r.json()["detail"]

    # the owner sees the request, and approves it at 10:00 for an hour
    pending = c.get("/api/v1/support-access", headers=owner).json()
    assert [x["id"] for x in pending] == [g["id"]] and pending[0]["requested_by"] == "support"
    clock.at = T0 + timedelta(hours=1)
    ok = c.post(f"/api/v1/support-access/{g['id']}/approve", headers=owner).json()
    assert ok["state"] == "active" and ok["expires_at"].startswith("2026-10-01T11:00")

    h = as_support(c, g["id"])
    me = c.get("/api/v1/auth/me", headers=h).json()
    assert me["tenant"]["slug"] == "bakers-inn" and me["roles"] == ["break_glass"]
    assert me["break_glass"]["grant_id"] == g["id"]
    assert c.get("/api/v1/sessions", headers=h).status_code == 200
    assert c.get("/api/v1/bays", headers=h).status_code == 200
    # read-only, whatever a role would say
    r = c.post("/api/v1/sessions", json={"bay_id": str(uuid4())}, headers=h)
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    # and only what §4.2 unlocks: not users, not the audit log, not settings
    assert c.get("/api/v1/users", headers=h).status_code == 403
    assert c.get("/api/v1/audit", headers=h).status_code == 403

    # it ends on its own
    clock.at = T0 + timedelta(hours=2)
    assert c.get("/api/v1/sessions", headers=h).status_code == 403

    # every step and every request is in the tenant's own log, for the owner to read
    log = c.get("/api/v1/audit", params={"limit": 50}, headers=owner).json()
    actions = [(e["action"], e["subject"]) for e in log]
    assert ("break_glass_requested", "support") in actions
    assert ("break_glass_approved", "support") in actions
    used = [s for a, s in actions if a == "break_glass_used"]
    # what support tried to open, allowed or not; not /auth/me, and nothing after it ended
    assert sorted(used) == ["/api/v1/audit", "/api/v1/bays", "/api/v1/sessions", "/api/v1/users"]


def test_the_owner_ends_access_and_it_stops_at_once(glass):
    c, clock = glass
    owner = login(c, "owner")
    g = ask(c).json()
    c.post(f"/api/v1/support-access/{g['id']}/approve", headers=owner)
    h = as_support(c, g["id"])
    assert c.get("/api/v1/sessions", headers=h).status_code == 200
    ended = c.post(f"/api/v1/support-access/{g['id']}/end", headers=owner).json()
    assert ended["state"] == "ended" and ended["ended_by"] == "owner"
    assert c.get("/api/v1/sessions", headers=h).status_code == 403
    # support can end its own too, and ask again once nothing is open
    g2 = ask(c).json()
    assert ask(c).status_code == 409
    mine = c.post(f"/api/v1/platform/break-glass/{g2['id']}/end", headers=login(c, "support"))
    assert mine.json()["state"] == "ended"
    assert [
        x["state"]
        for x in c.get("/api/v1/platform/break-glass", headers=login(c, "support")).json()
    ] == ["ended", "ended"]


def test_only_the_owner_decides_and_only_for_its_own_tenant(glass):
    c, _ = glass
    g = ask(c).json()
    # a tenant admin is not the owner; another tenant's admin cannot even see it
    assert (
        c.post(f"/api/v1/support-access/{g['id']}/approve", headers=login(c, "admin")).status_code
        == 403
    )
    assert c.get("/api/v1/support-access", headers=login(c, "b-admin")).status_code == 403
    # nor can staff approve for the tenant, nor support approve itself
    for who in ("platform", "litzim", "support"):
        assert (
            c.post(f"/api/v1/support-access/{g['id']}/approve", headers=login(c, who)).status_code
            == 403
        )
    # only support asks
    assert ask(c, who="platform").status_code == 403
    assert ask(c, tenant=uuid4()).status_code == 404


def test_a_grant_works_for_its_own_tenant_and_its_own_requester_only(glass):
    c, _ = glass
    g = ask(c).json()
    c.post(f"/api/v1/support-access/{g['id']}/approve", headers=login(c, "owner"))
    h = as_support(c, g["id"])
    # it is Bakers Inn's data, not Isolation Test's
    bakers = {b["id"] for b in c.get("/api/v1/bays", headers=h).json()}
    other = {b["id"] for b in c.get("/api/v1/bays", headers=login(c, "b-admin")).json()}
    assert bakers and not bakers & other
    # somebody else holding the grant id gets nothing from it
    for who in ("platform", "admin", "b-admin"):
        r = c.get("/api/v1/sessions", headers={**login(c, who), GLASS: g["id"]})
        assert r.status_code == 403, who
    assert c.get("/api/v1/sessions", headers=as_support(c, "not-a-uuid")).status_code == 403
    assert ISOLATION_TEST_ID  # the second tenant is seeded in every test app
