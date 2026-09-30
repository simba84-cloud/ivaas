"""Edge nodes and their enrollment tokens (proposal M2).

Both are tenant-owned: row-level security enabled, forced, and the same
`tenant_isolation` policy as every other tenant table (see 0012). The application
role gets DML on them through 0012's default privileges.
"""

from alembic import op

revision = "0013_edge_nodes"
down_revision = "0012_tenancy"
branch_labels = None
depends_on = None

TABLES = ["edge_enrollment_tokens", "edge_nodes"]

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS edge_nodes (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        site_id UUID NOT NULL REFERENCES sites (id),
        bay_id UUID REFERENCES bays (id),
        name VARCHAR(120) NOT NULL,
        hostname VARCHAR(120) NOT NULL DEFAULT '',
        status VARCHAR(16) NOT NULL,
        credential_hash VARCHAR(64) NOT NULL,
        enrolled_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ,
        last_report JSONB NOT NULL DEFAULT '{}',
        config JSONB NOT NULL DEFAULT '{}',
        config_version VARCHAR(16) NOT NULL,
        revoked_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS ix_edge_nodes_tenant_id ON edge_nodes (tenant_id)",
    """CREATE TABLE IF NOT EXISTS edge_enrollment_tokens (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        site_id UUID NOT NULL REFERENCES sites (id),
        bay_id UUID REFERENCES bays (id),
        name VARCHAR(120) NOT NULL,
        token_hash VARCHAR(64) NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        used_at TIMESTAMPTZ,
        used_by_node UUID REFERENCES edge_nodes (id),
        created_by VARCHAR(120) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS ix_edge_enrollment_tokens_tenant_id
        ON edge_enrollment_tokens (tenant_id)""",
    # explicit, not relying on default privileges: the table may predate them
    "GRANT SELECT, INSERT, UPDATE, DELETE ON edge_nodes, edge_enrollment_tokens TO ivaas_app",
]
for _table in TABLES:
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
    op.execute("DROP TABLE IF EXISTS edge_enrollment_tokens")
    op.execute("DROP TABLE IF EXISTS edge_nodes")
