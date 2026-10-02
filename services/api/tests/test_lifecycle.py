"""M8, T8.4 and T8.5: cancel, export, import into a scratch environment, purge.

In memory: the rules (who may, when, and what a cancelled tenant can still reach).
On Postgres: the export holds every row and object, an import into a fresh database
reproduces the counts exactly, and a purge leaves nothing but its signed certificate.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import login, make_client
from test_billing_api import USERS, Clock, direct_tenant

from ivaas.domain.lifecycle import DeletionCertificate, sign
from ivaas.domain.tenancy import BAKERS_INN_ID

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def life():
    with make_client(local_users=USERS) as c:
        c.app.state.container.clock = clock = Clock()
        clock.at = T0
        yield c, clock


def test_the_owner_cancels_and_then_only_the_account_routes_answer(life):
    c, _ = life
    owner = login(c, "owner")
    wrong = c.post("/api/v1/account/cancel", json={"confirm": "bakers"}, headers=owner)
    assert wrong.status_code == 422 and "bakers-inn" in wrong.json()["detail"]
    done = c.post(
        "/api/v1/account/cancel",
        json={"confirm": "bakers-inn", "reason": "POC over"},
        headers=owner,
    ).json()
    assert done["status"] == "cancelled" and done["cancelled_by"] == "owner"
    assert done["purge_after"].startswith("2026-12-30") and done["retention_days"] == 90

    # §3.3: export only. Nothing else answers, not even counting
    r = c.get("/api/v1/sessions", headers=login(c, "admin"))
    assert r.status_code == 403 and "only its data export remains until 2026-12-30" in r.text
    from conftest import SERVICE

    assert c.post("/api/v1/edge/heartbeat", json={}, headers=SERVICE).status_code in (403, 401)
    assert c.get("/api/v1/account/lifecycle", headers=owner).json()["status"] == "cancelled"
    # the in-memory store cannot export: it says so rather than sending half a tenant
    r = c.get("/api/v1/account/export", headers=owner)
    assert r.status_code == 501 and "Postgres" in r.json()["detail"]
    assert c.get("/api/v1/account/lifecycle", headers=owner).json()["export_available"] is False

    # Cassava reinstates it within the window, and it is a working account again
    back = c.post(
        f"/api/v1/platform/tenants/{BAKERS_INN_ID}/reinstate", headers=login(c, "platform")
    )
    assert back.status_code == 200 and back.json()["status"] == "trial"
    assert back.json()["cancelled_at"] is None
    assert c.get("/api/v1/sessions", headers=login(c, "admin")).status_code == 200
    actions = [e["action"] for e in c.get("/api/v1/audit", headers=owner).json()]
    assert {"tenant_cancelled", "tenant_reinstated"} <= set(actions)


def test_only_the_owner_cancels_or_exports_and_only_cassava_purges(life):
    c, _ = life
    for who in ("admin", "viewer", "operator"):
        assert c.get("/api/v1/account/export", headers=login(c, who)).status_code == 403
        r = c.post("/api/v1/account/cancel", json={"confirm": "bakers-inn"}, headers=login(c, who))
        assert r.status_code == 403
    # staff have no account of their own; a partner cannot cancel or purge a customer
    assert c.get("/api/v1/account/lifecycle", headers=login(c, "platform")).status_code == 403
    base = f"/api/v1/platform/tenants/{BAKERS_INN_ID}"
    for path in ("cancel", "purge"):
        r = c.post(f"{base}/{path}", json={"confirm": "bakers-inn"}, headers=login(c, "litzim"))
        assert r.status_code == 403
    assert (
        c.get("/api/v1/platform/deletion-certificates", headers=login(c, "platform")).json() == []
    )


def test_purge_waits_for_cancellation_and_the_whole_retention_window(life):
    c, clock = life
    platform = login(c, "platform")
    base = f"/api/v1/platform/tenants/{BAKERS_INN_ID}"
    sure = {"confirm": "bakers-inn"}
    r = c.post(f"{base}/purge", json=sure, headers=platform)
    assert r.status_code == 422 and "cancel it first" in r.json()["detail"]
    lc = c.post(f"{base}/cancel", json={**sure, "reason": "contract ended"}, headers=platform)
    assert lc.status_code == 200 and lc.json()["cancelled_by"] == "platform"
    assert c.post(f"{base}/cancel", json=sure, headers=platform).status_code == 409
    clock.at = T0 + timedelta(days=89)
    r = c.post(f"{base}/purge", json=sure, headers=platform)
    assert r.status_code == 422 and "kept until 2026-12-30" in r.json()["detail"]
    clock.at = T0 + timedelta(days=90)
    assert c.post(f"{base}/purge", json={"confirm": "x"}, headers=platform).status_code == 422
    # due, confirmed: only the in-memory store stops it
    assert c.post(f"{base}/purge", json=sure, headers=platform).status_code == 501
    lifecycle = c.get(f"{base}/lifecycle", headers=platform).json()
    assert lifecycle["purge_after"].startswith("2026-12-30")


def test_a_certificate_cannot_be_edited_without_breaking_its_signature():
    from uuid import uuid4

    body = {"tenant": {"slug": "gone"}, "rows_deleted": {"sessions": 12}}
    cert = DeletionCertificate(
        uuid4(), "gone", "Gone Ltd", T0, "platform", body, sign(body, "k" * 32)
    )
    assert cert.verify("k" * 32)
    edited = DeletionCertificate(
        cert.purged_tenant_id,
        "gone",
        "Gone Ltd",
        T0,
        "platform",
        {**body, "rows_deleted": {"sessions": 1}},
        cert.signature,
    )
    assert not edited.verify("k" * 32) and not cert.verify("another key" * 3)


# --- on Postgres: the real export, import and purge ------------------------------------
@pytest.fixture
def pg_life(postgres_url):
    from ivaas.adapters.persistence.secrets import SecretBox

    with make_client(
        local_users=USERS,
        storage="postgres",
        database_url=postgres_url,
        secrets_keys=[SecretBox.generate_key()],
    ) as c:
        c.app.state.container.clock = clock = Clock()
        clock.at = T0
        yield c, clock


def _with_data(c, slug):
    """A tenant with a site, a bay, a token, an owner (password hash), audit entries
    and an object in the store."""
    tenant, owner = direct_tenant(c, slug)
    platform = login(c, "platform")
    base = f"/api/v1/platform/tenants/{tenant}"
    site = c.post(
        f"{base}/onboarding/site",
        json={"site_name": "Depot", "bay_name": "Dock 1"},
        headers=platform,
    ).json()
    c.post(
        f"{base}/onboarding/enrollment-tokens",
        json={"site_id": site["site"]["id"], "name": "edge"},
        headers=platform,
    )
    container = c.app.state.container
    from uuid import UUID

    from ivaas.tenancy import object_key, tenant_context

    async def put():
        with tenant_context(UUID(tenant)):
            await container.objects.put(
                object_key("reports/day.csv"), b"day,crates\n1,40\n", "text/csv"
            )

    c.portal.call(put)
    return tenant, owner


@pytest.mark.postgres
def test_t8_4_an_export_imported_into_a_scratch_database_matches_100_percent(
    pg_life, postgres_url, tmp_path
):
    c, _ = pg_life
    tenant, owner = _with_data(c, "export-co")
    r = c.get("/api/v1/account/export", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["tenant"]["slug"] == "export-co"
    assert manifest["counts"]["sites"] == 1 and manifest["counts"]["tenants"] == 1
    assert [o["key"].endswith("reports/day.csv") for o in manifest["objects"]] == [True]
    # secrets never leave, and the manifest says which were held back
    assert "password_hash" in manifest["withheld"]["users"]
    assert "token_hash" in manifest["withheld"]["edge_enrollment_tokens"]
    assert b"$argon2" not in r.content
    assert "users.csv" in "".join(z.namelist()) and "tables/sites.csv" in z.namelist()
    exported = Path(tmp_path / "export.zip")
    exported.write_bytes(r.content)

    counts, objects = c.portal.call(_import_into_scratch, postgres_url, exported, tmp_path)
    # T8.4: record counts match 100%, table by table, and every object came back. The
    # partner row rides along only so the tenant's foreign key holds: it is not theirs
    theirs = {t: n for t, n in manifest["counts"].items() if t != "partners"}
    assert counts == theirs and len(theirs) > 30
    assert objects == len(manifest["objects"])


async def _import_into_scratch(url, path, tmp_path):
    from uuid import uuid4

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from ivaas.adapters.persistence.postgres import build_postgres_repositories
    from ivaas.adapters.persistence.secrets import SecretBox
    from ivaas.adapters.persistence.tenant_data_postgres import PostgresTenantData
    from ivaas.adapters.storage.objects import LocalObjectStore
    from ivaas.application.lifecycle import TenantLifecycle

    name = f"scratch_{uuid4().hex[:8]}"
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f"CREATE DATABASE {name}"))
    await admin.dispose()
    scratch = url.rsplit("/", 1)[0] + f"/{name}"
    *_, dispose, sm = await build_postgres_repositories(
        scratch, seed=None, box=SecretBox([SecretBox.generate_key()])
    )
    objects = LocalObjectStore(str(tmp_path / "scratch-objects"))
    lc = TenantLifecycle(
        tenants=None,
        data=PostgresTenantData(sm.unscoped),
        objects=objects,
        certificates=None,
        clock=None,
        retention=timedelta(days=90),
        secret="x" * 32,
    )
    try:
        result = await lc.import_into(path)
    finally:
        await dispose()
    return result["counts"], result["objects"]


@pytest.mark.postgres
def test_t8_5_cancel_retention_purge_leaves_nothing_and_a_signed_certificate(pg_life):
    c, clock = pg_life
    tenant, owner = _with_data(c, "purge-co")
    platform = login(c, "platform")
    base = f"/api/v1/platform/tenants/{tenant}"
    container = c.app.state.container
    from uuid import UUID

    before = c.portal.call(container.tenant_data.counts, UUID(tenant))
    assert before["sites"] == 1 and before["users"] >= 1 and before["audit_log"] >= 1

    assert (
        c.post("/api/v1/account/cancel", json={"confirm": "purge-co"}, headers=owner).status_code
        == 200
    )
    clock.at = T0 + timedelta(days=91)
    r = c.post(f"{base}/purge", json={"confirm": "purge-co"}, headers=platform)
    assert r.status_code == 200, r.text
    cert = r.json()
    assert cert["valid"] and cert["tenant_slug"] == "purge-co"
    assert cert["body"]["scan"]["rows_remaining"] == 0 and cert["body"]["objects_deleted"] == 1
    assert cert["body"]["rows_deleted"]["sites"] == 1

    # the scripted scan, done again here: every tenant table, the store, the tenant row
    after = c.portal.call(container.tenant_data.counts, UUID(tenant))
    assert set(after.values()) == {0}, {t: n for t, n in after.items() if n}
    from ivaas.tenancy import OBJECT_PREFIX, system_context

    async def objects_left():
        with system_context():
            return await container.objects.list_keys(f"{OBJECT_PREFIX}{tenant}/")

    assert c.portal.call(objects_left) == []
    assert c.get(base, headers=platform).status_code == 404
    listed = c.get("/api/v1/platform/deletion-certificates", headers=platform).json()
    assert cert["id"] in {x["id"] for x in listed}

    async def platform_log():  # the platform keeps its own record of the purge
        from ivaas.domain.audit import AuditAction

        with system_context():
            found = await container.audit.list_recent(action=AuditAction.TENANT_PURGED)
        return [(e.subject, e.detail.get("certificate")) for e in found]

    assert ("purge-co", cert["id"]) in c.portal.call(platform_log)
