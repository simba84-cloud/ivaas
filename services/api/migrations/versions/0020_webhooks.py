"""Webhooks: where a tenant wants events sent, and every attempt to send them (T6.4).

Both tables are tenant-owned and row-level secured (0012). The signing secret is
stored sealed. Deliveries are found by status and due time, so that is indexed.
"""

from alembic import op

revision = "0020_webhooks"
down_revision = "0019_daily_reports"
branch_labels = None
depends_on = None

TABLES = ("webhook_endpoints", "webhook_deliveries")

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS webhook_endpoints (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        url VARCHAR(500) NOT NULL,
        events JSONB NOT NULL,
        secret TEXT NOT NULL,
        description VARCHAR(200) NOT NULL DEFAULT '',
        created_by VARCHAR(120) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS webhook_deliveries (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        endpoint_id UUID NOT NULL REFERENCES webhook_endpoints (id) ON DELETE CASCADE,
        event_id UUID NOT NULL,
        event VARCHAR(40) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(12) NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TIMESTAMPTZ,
        last_status_code INTEGER,
        last_error VARCHAR(300),
        delivered_at TIMESTAMPTZ,
        replay_of UUID,
        created_at TIMESTAMPTZ NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_due
        ON webhook_deliveries (status, next_attempt_at)""",
    """CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_endpoint
        ON webhook_deliveries (endpoint_id, created_at)""",
]
for table in TABLES:
    UPGRADE += [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO ivaas_app",
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f"""CREATE POLICY tenant_isolation ON {table}
            USING (ivaas_tenant_visible(tenant_id))
            WITH CHECK (ivaas_tenant_visible(tenant_id))""",
    ]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS webhook_deliveries")
    op.execute("DROP TABLE IF EXISTS webhook_endpoints")
