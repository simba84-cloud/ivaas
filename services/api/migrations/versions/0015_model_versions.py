"""Model versions for over-the-air delivery, and each node's previous configuration.

model_versions is tenant-owned and row-level secured like every tenant table (0012).
edge_nodes.previous_config is what a rollback restores.
"""

from alembic import op

revision = "0015_model_versions"
down_revision = "0014_ingested_events"
branch_labels = None
depends_on = None

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS model_versions (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        name VARCHAR(40) NOT NULL,
        version VARCHAR(40) NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        size_bytes BIGINT NOT NULL,
        object_key VARCHAR(300) NOT NULL,
        meta JSONB NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        created_by VARCHAR(120) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (tenant_id, name, version))""",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON model_versions TO ivaas_app",
    "ALTER TABLE model_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE model_versions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON model_versions",
    """CREATE POLICY tenant_isolation ON model_versions
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
    "ALTER TABLE edge_nodes ADD COLUMN IF NOT EXISTS previous_config JSONB",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE edge_nodes DROP COLUMN IF EXISTS previous_config")
    op.execute("DROP TABLE IF EXISTS model_versions")
