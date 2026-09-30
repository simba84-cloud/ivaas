"""Filed daily reports: which site, which day, and where its PDF and CSV are.

Tenant-owned and row-level secured like every tenant table (0012); one report per
site per day, so filing it again replaces it.
"""

from alembic import op

revision = "0019_daily_reports"
down_revision = "0018_manifests"
branch_labels = None
depends_on = None

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS daily_reports (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        site_id UUID NOT NULL REFERENCES sites (id),
        day DATE NOT NULL,
        pdf_key VARCHAR(300) NOT NULL,
        csv_key VARCHAR(300) NOT NULL,
        loads INTEGER NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (tenant_id, site_id, day))""",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON daily_reports TO ivaas_app",
    "ALTER TABLE daily_reports ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE daily_reports FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON daily_reports",
    """CREATE POLICY tenant_isolation ON daily_reports
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_reports")
