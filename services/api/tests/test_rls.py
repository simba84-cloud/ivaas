"""T1.2 and T1.3: row-level security in the real database (proposal M1).

T1.3 is the CI guard: a migration that adds a table with a tenant_id column and no
policy fails here, whoever wrote it. T1.2 proves the policies bite: the
application's role, with no tenant set, reads nothing from any tenant table.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ivaas.adapters.persistence.postgres import APP_ROLE, build_postgres_repositories
from ivaas.adapters.persistence.secrets import SecretBox
from ivaas.config.container import demo_topology
from ivaas.domain.tenancy import BAKERS_INN_ID
from ivaas.tenancy import tenant_context

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest_asyncio.fixture
async def owner(postgres_url):
    """The migrated database, seeded with Bakers Inn's topology, and an owner connection."""
    site, bay, cams = demo_topology()
    *_, dispose, _sm = await build_postgres_repositories(
        postgres_url, seed=(site, bay, cams), box=SecretBox([SecretBox.generate_key()])
    )
    await dispose()
    engine = create_async_engine(postgres_url)
    yield engine
    await engine.dispose()


TENANT_TABLES = text(
    """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
              EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy
       FROM pg_class c
       JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
       JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
       WHERE c.relkind = 'r'
       ORDER BY c.relname"""
)


async def test_every_table_with_a_tenant_id_has_rls_forced_and_a_policy(owner):
    async with owner.connect() as db:
        rows = (await db.execute(TENANT_TABLES)).all()
    assert len(rows) >= 17, "tenant tables went missing"
    unprotected = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)]
    no_policy = [r.relname for r in rows if not r.has_policy]
    assert not unprotected, f"RLS not enabled and forced on: {unprotected}"
    assert not no_policy, f"no policy on: {no_policy}"


async def test_the_application_role_cannot_bypass_the_policies(owner):
    async with owner.connect() as db:
        role = (
            await db.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
                {"r": APP_ROLE},
            )
        ).one()
    assert role == (False, False)


async def test_with_no_tenant_set_the_application_role_reads_nothing(owner):
    async with owner.connect() as db:
        tables = [r.relname for r in (await db.execute(TENANT_TABLES)).all()]
    async with owner.connect() as db:
        async with db.begin():
            await db.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
            for table in tables:
                n = (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                assert n == 0, f"{table} returned {n} rows with no tenant set"
    # and the same role, in Bakers Inn, does see Bakers Inn's rows: the policy
    # separates tenants, it does not simply hide everything
    async with owner.connect() as db:
        async with db.begin():
            await db.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(BAKERS_INN_ID)}
            )
            assert (await db.execute(text("SELECT count(*) FROM cameras"))).scalar_one() == 17


async def test_a_row_cannot_be_written_into_another_tenant(owner):
    async with owner.connect() as db:
        async with db.begin():
            await db.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": "00000000-0000-0000-0000-00000000000b"},
            )
            with pytest.raises(Exception, match="row-level security"):
                await db.execute(
                    text(
                        "INSERT INTO sites (id, name, timezone, tenant_id) "
                        "VALUES (gen_random_uuid(), 'x', 'UTC', :a)"
                    ),
                    {"a": str(BAKERS_INN_ID)},
                )


async def test_repositories_see_only_the_tenant_in_context(owner, postgres_url):
    sites, bays, *_rest, dispose, _ = await build_postgres_repositories(
        postgres_url, seed=None, box=SecretBox([SecretBox.generate_key()])
    )
    try:
        with tenant_context(BAKERS_INN_ID):
            assert len(await bays.list_all()) >= 1
        with tenant_context(uuid4()):
            assert await bays.list_all() == []
            assert await sites.list_all() == []
    finally:
        await dispose()
