"""M8, T8.6: a silo installation for one tenant.

The tenant's export from the pooled platform (T8.4) fills a fresh silo database; the
silo then runs the same application, for that tenant alone. The acceptance run of
T1-T7 against a deployed silo is deploy/silo/silo.py's `accept`; this is the import
and the silo's own rules, end to end on Postgres.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import login, make_client
from test_billing_api import USERS

from ivaas.adapters.persistence.secrets import SecretBox
from ivaas.domain.tenancy import BAKERS_INN_ID

KEY = SecretBox.generate_key()
#: a silo has keys of its own: nothing sealed on the pooled platform opens there
SILO_KEY = SecretBox.generate_key()


def _scratch_db(c, url: str) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    name = f"silo_{uuid4().hex[:8]}"

    async def create():
        engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(f"CREATE DATABASE {name}"))
        await engine.dispose()

    c.portal.call(create)
    return url.rsplit("/", 1)[0] + f"/{name}"


def _silo_settings(url: str, objects: Path, silo: str):
    from ivaas.config.settings import Settings

    return Settings(
        storage="postgres",
        events="memory",
        database_url=url,
        secrets_keys=[SILO_KEY],
        silo_tenant=silo,
        seed_demo_data=False,
        local_users={},
        objects_dir=str(objects),
    )


@pytest.fixture
def pooled(postgres_url):
    """The pooled platform, on Postgres."""
    with make_client(
        local_users=USERS, storage="postgres", database_url=postgres_url, secrets_keys=[KEY]
    ) as c:
        yield c


def _give_bakers_secrets(c) -> str:
    """What a real tenant has that must not travel as it is: a webhook's signing
    secret, a camera's address (it carries the camera's password), an SSO client
    secret and a face enrolment."""
    from datetime import UTC, datetime

    from ivaas.domain.security import EnrolledPerson
    from ivaas.domain.sso import SsoConfig
    from ivaas.tenancy import tenant_context

    admin = login(c, "admin")
    name = f"Dock {uuid4().hex[:6]}"  # the test database is shared between tests
    hook = c.post(
        "/api/v1/webhooks",
        json={"url": "https://erp.bakers-inn.example/hooks", "events": ["session.closed"]},
        headers=admin,
    )
    assert hook.status_code in (200, 201), hook.text
    bay = c.get("/api/v1/bays", headers=admin).json()[0]
    cam = c.post(
        f"/api/v1/bays/{bay['id']}/cameras",
        json={"name": name, "role": "overhead", "source_url": "rtsp://cam:pw@10.0.0.9/s"},
        headers=admin,
    )
    assert cam.status_code == 201, cam.text
    # an enrolled edge node: its credential is a hash that stays behind (found when the
    # whole suite ran: earlier tests' nodes left markers in hash columns)
    from test_edge import enrol, token

    assert enrol(c, token(c, admin, bay["site_id"], name="Bakers edge")).status_code == 201
    container = c.app.state.container

    async def seed():
        with tenant_context(BAKERS_INN_ID):
            await container.sso_configs.save(
                SsoConfig(
                    tenant_id=BAKERS_INN_ID,
                    issuer="https://login.microsoftonline.com/t/v2.0",
                    client_id="ivaas",
                    client_secret="real-azure-secret",
                    domains=["bakersinn.co.zw"],
                    updated_by="admin",
                )
            )
            now = datetime.now(UTC)
            await container.people.save(
                EnrolledPerson("Ann", "E1", "consent-1", "admin", now, (0.1,) * 128)
            )

    c.portal.call(seed)
    return name


def _export_bakers(c, tmp_path) -> tuple[Path, str]:
    camera = _give_bakers_secrets(c)
    owner = login(c, "owner")
    r = c.get("/api/v1/account/export", headers=owner)
    assert r.status_code == 200, r.text
    path = tmp_path / "bakers-inn-export.zip"
    path.write_bytes(r.content)
    return path, camera


@pytest.mark.postgres
def test_t8_6_bakers_inns_export_fills_its_silo_and_the_silo_runs_for_it_alone(
    pooled, postgres_url, tmp_path
):
    from ivaas.config.container import build_container
    from ivaas.tools.silo import import_into_silo

    export, camera = _export_bakers(pooled, tmp_path)
    manifest = json.loads(zipfile.ZipFile(export).read("manifest.json"))
    silo_url = _scratch_db(pooled, postgres_url)
    settings = _silo_settings(silo_url, tmp_path / "silo-objects", "bakers-inn")

    async def run():
        container = await build_container(settings)
        try:
            return await import_into_silo(container, export, "bakers-inn", SecretBox([SILO_KEY]))
        finally:
            await container.aclose()

    report = pooled.portal.call(run)
    # every row of every tenant table, and every object: 100%, or the import refuses
    assert report["rows"] == sum(n for t, n in manifest["counts"].items() if t != "partners")
    # objects a row names but the store never had are listed in the export as missing
    assert report["objects"] == len([o for o in manifest["objects"] if not o.get("missing")])
    temporary = report["owner_temporary_passwords"]["owner"]
    assert report["accounts_closed_until_reset"] >= 1  # admin, operator, viewer

    from fastapi.testclient import TestClient

    from ivaas.adapters.http.app import create_app

    with TestClient(create_app(settings)) as silo:
        # the owner gets in with the password shown once, and must change it
        r = silo.post("/api/v1/auth/login", json={"username": "owner", "password": temporary})
        assert r.status_code == 200 and r.json()["must_change_password"]
        first = {"Authorization": f"Bearer {r.json()['access_token']}"}
        new = silo.post(
            "/api/v1/auth/password",
            json={"current_password": temporary, "new_password": "a long new silo password 1"},
            headers=first,
        ).json()["access_token"]
        owner = {"Authorization": f"Bearer {new}"}
        me = silo.get("/api/v1/auth/me", headers=owner).json()
        assert me["tenant"]["id"] == str(BAKERS_INN_ID)
        # Bakers Inn's own data, as it was on the pooled platform
        pooled_sites = pooled.get("/api/v1/sites", headers=login(pooled, "admin")).json()
        assert silo.get("/api/v1/sites", headers=owner).json() == pooled_sites
        # the passwords stayed behind: everyone else is closed until the owner resets them
        assert (
            silo.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 401
        )
        # the secrets did not travel, and nothing that held one is broken for it
        # at least this test's own (the test database is shared, so other tests' too)
        assert report["webhooks_given_new_secrets"] >= 1 and report["face_enrolments_removed"] >= 1
        assert report["sso_client_secret_to_enter_again"]
        assert report["cameras_needing_their_address"] >= 1
        assert report["edge_nodes_to_enrol_again"] >= 1
        # the owner holds apikey.manage and user.manage: both pages answer, no 500 on a
        # sealed column the silo's key could not open
        assert silo.get("/api/v1/webhooks", headers=owner).status_code == 200
        sso = silo.get("/api/v1/sso", headers=owner).json()
        assert sso["configured"] and not sso["secret_configured"]
        start = silo.get(
            "/api/v1/auth/sso/start", params={"org": "bakers-inn"}, follow_redirects=False
        )
        assert "client secret entered again" in start.headers["location"].replace("+", " ").replace(
            "%20", " "
        )
        silo_container = silo.app.state.container

        async def secrets_in_silo():
            from ivaas.tenancy import tenant_context

            with tenant_context(BAKERS_INN_ID):
                hooks = await silo_container.webhooks.endpoints()
                people = await silo_container.people.list()
                cams = [
                    x
                    for b in await silo_container.bays.list_all()
                    for x in await silo_container.cameras.list_for_bay(b.id)
                ]
            return hooks, people, cams

        hooks, people, cams = silo.portal.call(secrets_in_silo)
        nodes = silo.get("/api/v1/edge/nodes", headers=owner)
        assert nodes.status_code == 200 and all(n["health"] == "revoked" for n in nodes.json())
        # a fresh secret, readable with the silo's key, and not the marker everyone knows
        assert hooks and all(h.secret and h.secret != "withheld-in-export" for h in hooks)
        assert people == []  # biometrics do not move: people enrol again
        assert any(x.name == camera and x.source.url is None for x in cams)
        # a silo hosts no other tenant: not even the platform's demo ones exist here
        tenants = silo.portal.call(_tenant_slugs, silo)
        assert tenants == ["bakers-inn"]


async def _tenant_slugs(c):
    from ivaas.tenancy import system_context

    with system_context():
        return sorted(t.slug for t in await c.app.state.container.tenants.list_all())


@pytest.mark.postgres
def test_a_silo_refuses_another_tenants_export(pooled, postgres_url, tmp_path):
    from ivaas.config.container import build_container
    from ivaas.tools.silo import SiloError, import_into_silo

    export, _ = _export_bakers(pooled, tmp_path)
    settings = _silo_settings(_scratch_db(pooled, postgres_url), tmp_path / "o", "acme-foods")

    async def run():
        container = await build_container(settings)
        try:
            await import_into_silo(container, export, "acme-foods", SecretBox([SILO_KEY]))
        finally:
            await container.aclose()

    with pytest.raises(
        SiloError, match="this silo is for 'acme-foods'; the export is 'bakers-inn'"
    ):
        pooled.portal.call(run)


def test_a_silo_provisions_no_other_tenant():
    with make_client(local_users=USERS, silo_tenant="bakers-inn") as c:
        r = c.post(
            "/api/v1/platform/tenants",
            json={"slug": "acme-foods", "name": "Acme", "owner_username": "acme.owner"},
            headers={**login(c, "platform"), "Idempotency-Key": "silo-guard-0001"},
        )
        assert r.status_code == 409 and "bakers-inn's alone" in r.json()["detail"]


@pytest.mark.postgres
def test_an_export_holding_a_foreign_sealed_secret_does_not_lock_the_silo_out(
    pooled, postgres_url, tmp_path
):
    """Found live: an export made by older code carried the SSO client secret as
    ciphertext under the pooled key. The silo could not open it, and every password
    sign-in of the tenant, which reads the SSO settings, failed with a 500."""
    from ivaas.config.container import build_container
    from ivaas.tools.silo import import_into_silo

    export, _ = _export_bakers(pooled, tmp_path)
    old = tmp_path / "older-export.zip"
    with zipfile.ZipFile(export) as src, zipfile.ZipFile(old, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item == "tables/sso_configs.json":
                rows = json.loads(data)
                for r in rows:  # what older code exported: the secret, sealed under the pooled key
                    r["client_secret"] = SecretBox([KEY]).seal("real-azure-secret")
                data = json.dumps(rows).encode()
            dst.writestr(item, data)
    settings = _silo_settings(_scratch_db(pooled, postgres_url), tmp_path / "o2", "bakers-inn")

    async def run():
        container = await build_container(settings)
        try:
            return await import_into_silo(container, old, "bakers-inn", SecretBox([SILO_KEY]))
        finally:
            await container.aclose()

    report = pooled.portal.call(run)
    assert report["sso_client_secret_to_enter_again"]
    from fastapi.testclient import TestClient

    from ivaas.adapters.http.app import create_app

    with TestClient(create_app(settings)) as silo:
        temporary = report["owner_temporary_passwords"]["owner"]
        r = silo.post("/api/v1/auth/login", json={"username": "owner", "password": temporary})
        assert r.status_code == 200, r.text


def test_an_sso_secret_that_cannot_be_opened_reads_as_not_configured():
    """Password sign-in reads the SSO settings: an unopenable secret must not be an error."""
    from ivaas.adapters.persistence.sso_postgres import PostgresSsoStore

    store = PostgresSsoStore(sm=None, box=SecretBox([SILO_KEY]))
    assert store._secret(SecretBox([KEY]).seal("sealed-elsewhere")) == ""
    assert store._secret(SecretBox([SILO_KEY]).seal("ours")) == "ours"


@pytest.mark.postgres
def test_a_silo_restarted_after_its_import_holds_its_tenant_and_nothing_else(
    pooled, postgres_url, tmp_path
):
    """Found live: the API created Bakers Inn at every start, so a silo for anyone else
    had it back after its first restart."""
    from test_billing_api import direct_tenant

    from ivaas.config.container import build_container
    from ivaas.tools.silo import import_into_silo

    slug = f"acme-{uuid4().hex[:6]}"
    _, owner = direct_tenant(pooled, slug)
    export = tmp_path / f"{slug}.zip"
    export.write_bytes(pooled.get("/api/v1/account/export", headers=owner).content)
    settings = _silo_settings(_scratch_db(pooled, postgres_url), tmp_path / "o3", slug)

    async def run():
        container = await build_container(settings)
        try:
            return await import_into_silo(container, export, slug, SecretBox([SILO_KEY]))
        finally:
            await container.aclose()

    report = pooled.portal.call(run)
    assert report["stubs_removed"] == ["bakers-inn"]
    from fastapi.testclient import TestClient

    from ivaas.adapters.http.app import create_app

    for _ in range(2):  # and again: every start is the same
        with TestClient(create_app(settings)) as silo:
            assert silo.portal.call(_tenant_slugs, silo) == [slug]
