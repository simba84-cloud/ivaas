"""Email alerts: an address on each account, and the outbox alert emails wait in.

The outbox is tenant-owned and row-level secured like every tenant table (0012). One
alert is one email: (tenant_id, dedupe_key) is unique, so a repeated event or sweep
cannot email the same thing twice.
"""

from alembic import op

revision = "0029_email_alerts"
down_revision = "0028_crate_control"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(254)",
    """CREATE TABLE IF NOT EXISTS alert_emails (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        kind VARCHAR(32) NOT NULL,
        dedupe_key VARCHAR(200) NOT NULL,
        recipients JSONB NOT NULL,
        subject VARCHAR(200) NOT NULL,
        body_text TEXT NOT NULL,
        body_html TEXT,
        status VARCHAR(12) NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TIMESTAMPTZ,
        last_error VARCHAR(300),
        created_at TIMESTAMPTZ NOT NULL,
        sent_at TIMESTAMPTZ,
        UNIQUE (tenant_id, dedupe_key)
    )""",
    """CREATE INDEX IF NOT EXISTS alert_emails_due
        ON alert_emails (next_attempt_at) WHERE status = 'pending'""",
    "CREATE INDEX IF NOT EXISTS alert_emails_recent ON alert_emails (tenant_id, created_at)",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON alert_emails TO ivaas_app",
    "ALTER TABLE alert_emails ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE alert_emails FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON alert_emails",
    """CREATE POLICY tenant_isolation ON alert_emails
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_emails")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email")
