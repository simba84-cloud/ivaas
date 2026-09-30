"""Tenancy: partners and tenants, scoped role bindings, and row-level security.

Proposal §3.1-3.2 and M1. Everything that existed before this migration belonged to
Bakers Inn, the only customer there was, so every row is backfilled to it.

After this migration:
- every tenant-owned table has `tenant_id`, defaulted from the transaction's
  `app.tenant_id` setting, so an insert lands in the tenant it was made for;
- row-level security is ENABLED and FORCED on each, with one policy that shows a
  row only to its own tenant (or to `app.scope = 'system'`, used deliberately);
- the application's queries run as `ivaas_app`, which is neither superuser nor
  BYPASSRLS, so the policies apply to it. The migration role keeps ownership.

The backfill runs before RLS is switched on: once it is, even the owner sees
nothing without a tenant in context. A later migration that updates data must set
`app.scope` to 'system' first.
"""

from alembic import op

revision = "0012_tenancy"
down_revision = "0011_tally_sheets"
branch_labels = None
depends_on = None

BAKERS_INN = "e31e0de2-dacc-5582-9c82-9db31fff43ab"
LITZIM = "ab4a940c-791c-5223-9635-2bb319cceab3"
APP_ROLE = "ivaas_app"

#: tenant-owned tables whose rows always belong to a tenant
OWNED = [
    "sites",
    "bays",
    "cameras",
    "loading_sessions",
    "analysis_jobs",
    "alert_acknowledgements",
    "security_zones",
    "security_incidents",
    "badge_events",
    "enrolled_people",
    "tally_sheets",
    "tally_lines",
    "platform_settings",
]
#: tenant_id is NULL for platform and partner rows: staff accounts, platform audit
NULLABLE = ["users", "audit_log"]
#: created here, already carrying tenant_id
NEW = ["role_bindings", "tenant_provisioning"]
SECURED = OWNED + NULLABLE + NEW

UPGRADE = [
    # --- the hierarchy -----------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS partners (
        id UUID PRIMARY KEY,
        slug VARCHAR(64) NOT NULL UNIQUE,
        name VARCHAR(120) NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tenants (
        id UUID PRIMARY KEY,
        slug VARCHAR(64) NOT NULL UNIQUE,
        name VARCHAR(120) NOT NULL,
        partner_id UUID REFERENCES partners (id),
        status VARCHAR(16) NOT NULL,
        created_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS ix_tenants_partner_id ON tenants (partner_id)",
    f"""INSERT INTO partners (id, slug, name) VALUES ('{LITZIM}', 'litzim', 'LITZIM')
        ON CONFLICT (id) DO NOTHING""",
    f"""INSERT INTO tenants (id, slug, name, partner_id, status, created_at)
        VALUES ('{BAKERS_INN}', 'bakers-inn', 'Bakers Inn', '{LITZIM}', 'trial', now())
        ON CONFLICT (id) DO NOTHING""",
    # --- the tenant in context -----------------------------------------------------
    """CREATE OR REPLACE FUNCTION ivaas_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$""",
    """CREATE OR REPLACE FUNCTION ivaas_tenant_visible(row_tenant uuid) RETURNS boolean
        LANGUAGE sql STABLE
        AS $$ SELECT coalesce(current_setting('app.scope', true), '') = 'system'
                  OR row_tenant = nullif(current_setting('app.tenant_id', true), '')::uuid $$""",
    # --- accounts: roles move into scoped bindings -----------------------------------
    """CREATE TABLE IF NOT EXISTS role_bindings (
        id UUID PRIMARY KEY,
        username VARCHAR(64) NOT NULL REFERENCES users (username) ON DELETE CASCADE,
        tenant_id UUID REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        role VARCHAR(32) NOT NULL,
        scope_type VARCHAR(16) NOT NULL,
        scope_id UUID,
        UNIQUE NULLS NOT DISTINCT (username, role, scope_type, scope_id))""",
    "CREATE INDEX IF NOT EXISTS ix_role_bindings_username ON role_bindings (username)",
    """CREATE TABLE IF NOT EXISTS tenant_provisioning (
        id UUID PRIMARY KEY,
        idempotency_key VARCHAR(128) NOT NULL UNIQUE,
        tenant_id UUID NOT NULL REFERENCES tenants (id),
        owner_username VARCHAR(64) NOT NULL,
        steps JSONB NOT NULL,
        created_at TIMESTAMPTZ)""",
]

for _table in OWNED + NULLABLE:
    UPGRADE += [
        f"ALTER TABLE {_table} ADD COLUMN IF NOT EXISTS tenant_id UUID",
        f"UPDATE {_table} SET tenant_id = '{BAKERS_INN}' WHERE tenant_id IS NULL",
        f"ALTER TABLE {_table} ALTER COLUMN tenant_id SET DEFAULT ivaas_current_tenant()",
        f"""ALTER TABLE {_table} ADD CONSTRAINT {_table}_tenant_id_fkey
            FOREIGN KEY (tenant_id) REFERENCES tenants (id)""",
        f"CREATE INDEX IF NOT EXISTS ix_{_table}_tenant_id ON {_table} (tenant_id)",
    ]
for _table in OWNED:
    UPGRADE.append(f"ALTER TABLE {_table} ALTER COLUMN tenant_id SET NOT NULL")

UPGRADE += [
    # the pre-tenancy roles, as proposal §4 roles (see domain.rbac.LEGACY_ROLES)
    f"""INSERT INTO role_bindings (id, username, tenant_id, role, scope_type, scope_id)
        SELECT gen_random_uuid(), u.username, '{BAKERS_INN}', m.role, 'tenant', '{BAKERS_INN}'
        FROM users u
        CROSS JOIN LATERAL unnest(u.roles) AS r(name)
        JOIN (VALUES ('admin', 'tenant_admin'), ('admin', 'site_manager'),
                     ('operator', 'bay_operator'), ('viewer', 'auditor')) AS m(old, role)
          ON m.old = r.name
        ON CONFLICT DO NOTHING""",
    "ALTER TABLE users DROP COLUMN IF EXISTS roles",
    # keys that were unique platform-wide are unique per tenant now
    "ALTER TABLE platform_settings DROP CONSTRAINT IF EXISTS platform_settings_pkey",
    "ALTER TABLE platform_settings ADD PRIMARY KEY (tenant_id, key)",
    "ALTER TABLE alert_acknowledgements DROP CONSTRAINT IF EXISTS alert_acknowledgements_pkey",
    "ALTER TABLE alert_acknowledgements ADD PRIMARY KEY (tenant_id, key)",
    "ALTER TABLE tally_sheets DROP CONSTRAINT IF EXISTS tally_sheets_sheet_id_key",
    """ALTER TABLE tally_sheets ADD CONSTRAINT tally_sheets_tenant_sheet_key
        UNIQUE (tenant_id, sheet_id)""",
    # --- the application role --------------------------------------------------------
    f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
        END IF;
    END $$""",
    f"GRANT {APP_ROLE} TO CURRENT_USER",
    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    f"REVOKE ALL ON alembic_version FROM {APP_ROLE}",
    # tables a later migration creates are the application's too
    f"""ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}""",
]

for _table in SECURED:
    UPGRADE += [
        f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {_table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS tenant_isolation ON {_table}",
        f"""CREATE POLICY tenant_isolation ON {_table}
            USING (ivaas_tenant_visible(tenant_id))
            WITH CHECK (ivaas_tenant_visible(tenant_id))""",
    ]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("SELECT set_config('app.scope', 'system', true)")
    for table in SECURED:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS roles VARCHAR(16)[]")
    op.execute(
        """UPDATE users u SET roles = coalesce((
            SELECT array_agg(DISTINCT CASE b.role
                WHEN 'tenant_admin' THEN 'admin' WHEN 'tenant_owner' THEN 'admin'
                WHEN 'site_manager' THEN 'admin' WHEN 'bay_operator' THEN 'operator'
                ELSE 'viewer' END)
            FROM role_bindings b WHERE b.username = u.username), ARRAY['viewer'])"""
    )
    op.execute("ALTER TABLE users ALTER COLUMN roles SET NOT NULL")
    # other tenants' rows cannot survive the loss of the column that separates them
    for table in reversed(OWNED + NULLABLE):  # children before the rows they reference
        op.execute(
            f"DELETE FROM {table} WHERE tenant_id IS NOT NULL AND tenant_id <> '{BAKERS_INN}'"
        )
    op.execute("ALTER TABLE tally_sheets DROP CONSTRAINT IF EXISTS tally_sheets_tenant_sheet_key")
    op.execute(
        "ALTER TABLE tally_sheets ADD CONSTRAINT tally_sheets_sheet_id_key UNIQUE (sheet_id)"
    )
    op.execute("ALTER TABLE alert_acknowledgements DROP CONSTRAINT alert_acknowledgements_pkey")
    op.execute("ALTER TABLE alert_acknowledgements ADD PRIMARY KEY (key)")
    op.execute("ALTER TABLE platform_settings DROP CONSTRAINT platform_settings_pkey")
    op.execute("ALTER TABLE platform_settings ADD PRIMARY KEY (key)")
    for table in OWNED + NULLABLE:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id")
    op.execute("DROP TABLE IF EXISTS tenant_provisioning")
    op.execute("DROP TABLE IF EXISTS role_bindings")
    op.execute("DROP TABLE IF EXISTS tenants")
    op.execute("DROP TABLE IF EXISTS partners")
    op.execute("DROP FUNCTION IF EXISTS ivaas_tenant_visible(uuid)")
    op.execute("DROP FUNCTION IF EXISTS ivaas_current_tenant()")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
