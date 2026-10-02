"""T1.4 and T1.5: the permission matrix and scope inheritance (proposal §4).

MATRIX below is a literal transcription of §4.2. It is kept apart from
`ivaas.domain.rbac` on purpose: if the code drifts from the document, this fails.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from conftest import login, make_client

from ivaas.domain.models import Bay, Site
from ivaas.domain.rbac import (
    ROLE_PERMISSIONS,
    BindingError,
    Permission,
    Role,
    RoleBinding,
    Scope,
    authorize,
)
from ivaas.domain.tenancy import BAKERS_INN_ID, ScopeType, stable_id
from ivaas.domain.users import User
from ivaas.tenancy import system_context, tenant_context

R = Role
#: §4.2, column by column. Break-glass (🔓) is not a standing grant, so it is absent.
MATRIX: dict[str, set[Role]] = {
    "tenant.create": {R.PLATFORM_ADMIN, R.PARTNER_ADMIN},
    "tenant.suspend": {R.PLATFORM_ADMIN},
    "subscription.manage": {R.PLATFORM_ADMIN, R.PARTNER_ADMIN, R.TENANT_OWNER},
    "invoice.read": {R.PLATFORM_ADMIN, R.PARTNER_ADMIN, R.TENANT_OWNER, R.AUDITOR},
    "user.invite": {R.PARTNER_ADMIN, R.TENANT_OWNER, R.TENANT_ADMIN},
    "device.register": {R.PARTNER_ADMIN, R.PARTNER_INSTALLER, R.TENANT_ADMIN},
    "device.calibrate": {R.PARTNER_ADMIN, R.PARTNER_INSTALLER, R.TENANT_ADMIN},
    "video.live.view": {
        R.PARTNER_INSTALLER,
        R.TENANT_OWNER,
        R.TENANT_ADMIN,
        R.SITE_MANAGER,
        R.BAY_OPERATOR,
    },
    "count.read": {
        R.TENANT_OWNER,
        R.TENANT_ADMIN,
        R.SITE_MANAGER,
        R.BAY_OPERATOR,
        R.AUDITOR,
        R.INTEGRATION,
    },
    "count.override": {R.SITE_MANAGER, R.BAY_OPERATOR},
    "groundtruth.enter": {R.SITE_MANAGER, R.BAY_OPERATOR},
    "reconciliation.resolve": {R.TENANT_OWNER, R.SITE_MANAGER},
    "report.export": {R.TENANT_OWNER, R.TENANT_ADMIN, R.SITE_MANAGER, R.AUDITOR, R.INTEGRATION},
    "apikey.manage": {R.TENANT_OWNER, R.TENANT_ADMIN},
    "audit.read": {R.PLATFORM_ADMIN, R.TENANT_OWNER, R.TENANT_ADMIN, R.AUDITOR},
    "assistant.query": {
        R.TENANT_OWNER,
        R.TENANT_ADMIN,
        R.SITE_MANAGER,
        R.BAY_OPERATOR,
        R.AUDITOR,
    },
}
#: §4.2 has no column for Platform Billing; §4.1 gives it plans, pricing and invoices.
#: Break-glass is the 🔓 cells, made per request from an approved grant: tested below.
NOT_IN_TABLE = {R.PLATFORM_BILLING, R.BREAK_GLASS}


@pytest.mark.parametrize("permission", sorted(MATRIX))
@pytest.mark.parametrize("role", [r for r in Role if r not in NOT_IN_TABLE])
def test_every_cell_of_the_matrix(role, permission):
    expected = role in MATRIX[permission]
    assert (Permission(permission) in ROLE_PERMISSIONS[role]) is expected, (role, permission)


def test_platform_support_has_no_standing_access_only_the_right_to_ask():
    """§4.1: support "uses time-boxed break-glass access that the tenant approves"."""
    assert ROLE_PERMISSIONS[Role.PLATFORM_SUPPORT] == {Permission.SUPPORT_REQUEST}


def test_break_glass_is_the_unlocked_cells_and_the_topology_to_reach_them():
    """§4.2's 🔓: live video and counts. Topology too, or nothing can be found."""
    assert ROLE_PERMISSIONS[Role.BREAK_GLASS] == {
        Permission.VIDEO_LIVE_VIEW,
        Permission.COUNT_READ,
        Permission.TOPOLOGY_READ,
    }


def test_additions_to_the_matrix_never_reach_platform_or_partner_staff():
    """Permissions added beyond §4.2 are for a tenant's own people only."""
    # asking for break-glass is §4.1's support workflow; deciding on it is the tenant's
    additions = set(Permission) - {Permission(p) for p in MATRIX} - {Permission.SUPPORT_REQUEST}
    for role in (R.PLATFORM_ADMIN, R.PLATFORM_SUPPORT, R.PLATFORM_BILLING, R.PARTNER_ADMIN):
        assert not (ROLE_PERMISSIONS[role] & additions), role


def test_a_role_cannot_be_bound_where_the_document_does_not_allow_it():
    with pytest.raises(BindingError):
        RoleBinding(Role.TENANT_OWNER, ScopeType.SITE, stable_id("x"))
    with pytest.raises(BindingError):
        RoleBinding(Role.PLATFORM_ADMIN, ScopeType.TENANT, BAKERS_INN_ID)
    with pytest.raises(BindingError):
        RoleBinding(Role.AUDITOR, ScopeType.BAY, stable_id("x"))


def test_bindings_inherit_downward_and_never_sideways():
    site_x, site_y = stable_id("site.x"), stable_id("site.y")
    bay_x1 = stable_id("bay.x1")
    manager_x = [RoleBinding(Role.SITE_MANAGER, ScopeType.SITE, site_x)]
    at = Permission.RECONCILIATION_RESOLVE
    here = Scope(tenant_id=BAKERS_INN_ID, site_id=site_x, bay_id=bay_x1)
    there = Scope(tenant_id=BAKERS_INN_ID, site_id=site_y)
    assert authorize(manager_x, at, here)  # a bay at their site
    assert not authorize(manager_x, at, there)  # a sibling site
    tenant_wide = [RoleBinding(Role.SITE_MANAGER, ScopeType.TENANT, BAKERS_INN_ID)]
    assert authorize(tenant_wide, at, there)
    other_tenant = Scope(tenant_id=stable_id("tenant.other"), site_id=site_y)
    assert not authorize(tenant_wide, at, other_tenant)


# --- T1.5 through the API ----------------------------------------------------------

SITE_Y = Site(id=stable_id("site.test-y"), name="Second Depot", timezone="Africa/Harare")
BAY_Y = Bay(id=stable_id("bay.test-y"), site_id=SITE_Y.id, name="Bay Y")


@pytest.fixture
def two_sites():
    """Bakers Inn with a second site, and a site manager for the first one only."""
    with make_client() as c:
        container = c.app.state.container
        a = login(c, "admin")
        bay_x = c.get("/api/v1/bays", headers=a).json()[0]

        async def seed():
            with tenant_context(BAKERS_INN_ID):
                await container.sites.save(SITE_Y)
                await container.bays.save(BAY_Y)
            now = datetime.now(UTC)
            with system_context():
                await container.users.save(
                    User(
                        username="manager-x",
                        display_name="Site X manager",
                        password_hash=container.hasher.hash("manager-x-password"),
                        tenant_id=BAKERS_INN_ID,
                        bindings=[
                            RoleBinding(Role.SITE_MANAGER, ScopeType.SITE, UUID(bay_x["site_id"]))
                        ],
                        created_at=now,
                        password_changed_at=now,
                    )
                )

        c.portal.call(seed)
        yield c, a, login(c, "manager-x", "manager-x-password"), bay_x


def test_a_site_manager_works_at_their_site(two_sites):
    c, _, manager, bay_x = two_sites
    r = c.post(
        "/api/v1/sessions", json={"bay_id": bay_x["id"], "direction": "loading"}, headers=manager
    )
    assert r.status_code == 201, r.text
    assert {b["id"] for b in c.get("/api/v1/bays", headers=manager).json()} == {bay_x["id"]}


def test_a_site_manager_cannot_reach_another_site_of_the_same_tenant(two_sites):
    c, admin, manager, _ = two_sites
    y = str(BAY_Y.id)
    # the tenant admin sees both sites, the manager only theirs
    assert y in {b["id"] for b in c.get("/api/v1/bays", headers=admin).json()}
    assert c.get(f"/api/v1/bays/{y}/cameras", headers=manager).status_code == 404
    assert c.get(f"/api/v1/sites/{SITE_Y.id}/bays", headers=manager).status_code == 404
    assert c.get(f"/api/v1/sessions?bay_id={y}", headers=manager).status_code == 404
    opened = c.post("/api/v1/sessions", json={"bay_id": y, "direction": "loading"}, headers=admin)
    session = opened.json()["id"]
    assert c.post(f"/api/v1/sessions/{session}/close", headers=manager).status_code == 404
    assert session not in c.get("/api/v1/sessions", headers=manager).text
    # tenant-wide totals include site Y's loads, so a site role does not reach them
    assert c.get("/api/v1/summary", headers=manager).status_code == 403


def test_bindings_can_be_granted_per_site_through_the_api(two_sites):
    c, admin, _, bay_x = two_sites
    made = c.post(
        "/api/v1/users",
        json={"username": "yard", "roles": ["bay_operator"]},
        headers=admin,
    )
    assert made.status_code == 201, made.text
    body = {"bindings": [{"role": "bay_operator", "scope_type": "bay", "scope_id": str(BAY_Y.id)}]}
    r = c.put("/api/v1/users/yard/bindings", json=body, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["bindings"] == [
        {"role": "bay_operator", "scope_type": "bay", "scope_id": str(BAY_Y.id)}
    ]
    # a tenant cannot grant a platform role, nor reach outside itself
    bad = {"bindings": [{"role": "platform_admin", "scope_type": "tenant"}]}
    assert c.put("/api/v1/users/yard/bindings", json=bad, headers=admin).status_code == 422
    elsewhere = {
        "bindings": [
            {"role": "site_manager", "scope_type": "site", "scope_id": str(stable_id("nope"))}
        ]
    }
    assert c.put("/api/v1/users/yard/bindings", json=elsewhere, headers=admin).status_code == 404
    audit = c.get("/api/v1/audit", params={"action": "role_bound"}, headers=admin).json()
    assert audit[0]["detail"]["before"] == ["bay_operator"]
    assert audit[0]["detail"]["after"] == [f"bay_operator@bay:{BAY_Y.id}"]


def test_platform_and_partner_staff_hold_no_standing_access_to_tenant_data():
    with make_client() as c:
        for user in ("platform", "litzim"):
            h = login(c, user)
            for path in ("/api/v1/bays", "/api/v1/sessions", "/api/v1/audit", "/api/v1/users"):
                assert c.get(path, headers=h).status_code == 403, (user, path)
