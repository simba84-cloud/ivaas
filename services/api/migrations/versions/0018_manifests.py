"""Dispatch manifests and the exceptions raised against them (proposal M5, T5.4).

Both tables are tenant-owned and row-level secured like every tenant table (0012).
A manifest line is unique per tenant by (reference, direction), so re-importing a
manifest updates its lines rather than duplicating them.
"""

from alembic import op

revision = "0018_manifests"
down_revision = "0017_fleet"
branch_labels = None
depends_on = None

TABLES = ["manifest_lines", "manifest_exceptions"]

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS manifest_lines (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        reference VARCHAR(80) NOT NULL,
        day DATE NOT NULL,
        plate VARCHAR(16) NOT NULL,
        direction VARCHAR(16) NOT NULL,
        expected INTEGER NOT NULL,
        site_id UUID NOT NULL REFERENCES sites (id),
        route VARCHAR(80) NOT NULL DEFAULT '',
        session_id UUID REFERENCES loading_sessions (id),
        status VARCHAR(16) NOT NULL,
        imported_by VARCHAR(120) NOT NULL DEFAULT '',
        imported_at TIMESTAMPTZ,
        UNIQUE (tenant_id, reference, direction))""",
    "CREATE INDEX IF NOT EXISTS ix_manifest_lines_day ON manifest_lines (day)",
    """CREATE TABLE IF NOT EXISTS manifest_exceptions (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        kind VARCHAR(20) NOT NULL,
        day DATE NOT NULL,
        raised_at TIMESTAMPTZ NOT NULL,
        plate VARCHAR(16),
        route VARCHAR(80) NOT NULL DEFAULT '',
        session_id UUID REFERENCES loading_sessions (id),
        line_id UUID REFERENCES manifest_lines (id),
        expected INTEGER,
        counted INTEGER,
        status VARCHAR(16) NOT NULL,
        resolved_by VARCHAR(120),
        resolved_at TIMESTAMPTZ,
        resolution_note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_manifest_exceptions_status ON manifest_exceptions (status)",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON manifest_lines, manifest_exceptions TO ivaas_app",
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
    op.execute("DROP TABLE IF EXISTS manifest_exceptions")
    op.execute("DROP TABLE IF EXISTS manifest_lines")
