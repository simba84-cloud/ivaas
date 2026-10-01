"""Edge availability: periods in which each node, and each of its cameras, was up or
down, from their heartbeats. Kept for the POC's reliability figure (uptime, outages).

Tenant-owned and row-level secured like every tenant table (0012). Read by node and
time, and the latest per subject on every heartbeat, so both are indexed.
"""

from alembic import op

revision = "0021_edge_availability"
down_revision = "0020_webhooks"
branch_labels = None
depends_on = None

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS edge_availability (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        node_id UUID NOT NULL REFERENCES edge_nodes (id),
        camera_id UUID,
        up BOOLEAN NOT NULL,
        since TIMESTAMPTZ NOT NULL,
        until TIMESTAMPTZ NOT NULL,
        peak_spool INTEGER NOT NULL DEFAULT 0,
        last_spool INTEGER NOT NULL DEFAULT 0)""",
    """CREATE INDEX IF NOT EXISTS ix_edge_availability_latest
        ON edge_availability (node_id, camera_id, until DESC)""",
    """CREATE INDEX IF NOT EXISTS ix_edge_availability_window
        ON edge_availability (node_id, since, until)""",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON edge_availability TO ivaas_app",
    "ALTER TABLE edge_availability ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE edge_availability FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON edge_availability",
    """CREATE POLICY tenant_isolation ON edge_availability
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS edge_availability")
