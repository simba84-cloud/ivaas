"""Per-tenant SSO (M8, T8.2): each tenant's identity provider, and the provider's id
for each person who has signed in through it.

`sso_configs` is a tenant table like any other: row-level secured, so a tenant reads
only its own settings, with the client secret sealed by the application. Sign-in reads
it in the system scope before the person is known. `users.sso_issuer`/`sso_subject`
link an account to one person at one provider; the pair is unique, so one provider
identity cannot sign into two accounts.
"""

from alembic import op

revision = "0026_sso"
down_revision = "0025_tenant_lifecycle_end"
branch_labels = None
depends_on = None

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS sso_configs (
        tenant_id UUID PRIMARY KEY REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        issuer VARCHAR(300) NOT NULL,
        client_id VARCHAR(300) NOT NULL,
        client_secret VARCHAR(2048) NOT NULL,
        domains JSONB NOT NULL,
        default_role VARCHAR(32),
        required BOOLEAN NOT NULL DEFAULT FALSE,
        updated_by VARCHAR(120) NOT NULL,
        updated_at TIMESTAMPTZ)""",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON sso_configs TO ivaas_app",
    "ALTER TABLE sso_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE sso_configs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON sso_configs",
    """CREATE POLICY tenant_isolation ON sso_configs
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sso_issuer VARCHAR(300)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sso_subject VARCHAR(255)",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_users_sso_identity
        ON users (sso_issuer, sso_subject) WHERE sso_subject IS NOT NULL""",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_users_sso_identity")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS sso_subject")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS sso_issuer")
    op.execute("DROP TABLE IF EXISTS sso_configs")
