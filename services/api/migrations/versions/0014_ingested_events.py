"""The ingest ledger: edge events already applied, so a replay is not counted twice.

Tenant-owned and row-level secured like every tenant table (see 0012). Event ids
are unique per tenant; rows are pruned once no node could still be replaying them.
"""

from alembic import op

revision = "0014_ingested_events"
down_revision = "0013_edge_nodes"
branch_labels = None
depends_on = None

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS ingested_events (
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        event_id UUID NOT NULL,
        kind VARCHAR(16) NOT NULL,
        received_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (tenant_id, event_id))""",
    """CREATE INDEX IF NOT EXISTS ix_ingested_events_received_at
        ON ingested_events (received_at)""",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ingested_events TO ivaas_app",
    "ALTER TABLE ingested_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE ingested_events FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON ingested_events",
    """CREATE POLICY tenant_isolation ON ingested_events
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingested_events")
