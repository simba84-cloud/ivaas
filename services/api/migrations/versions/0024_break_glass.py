"""Break-glass grants (M8, T8.3): support asks one tenant for time-boxed, read-only
access, the tenant's owner decides, and the grant ends on its own.

Tenant-owned and row-level secured like every tenant table (0012): a tenant sees its
own grants. Authentication reads one by id in the system scope, before the tenant is
known. Grants are never deleted: what support was allowed, and when, is evidence.
"""

from alembic import op

revision = "0024_break_glass"
down_revision = "0023_billing_lifecycle"
branch_labels = None
depends_on = None

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS break_glass_grants (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        requested_by VARCHAR(120) NOT NULL,
        reason VARCHAR(500) NOT NULL,
        duration_s INTEGER NOT NULL CHECK (duration_s BETWEEN 900 AND 28800),
        requested_at TIMESTAMPTZ NOT NULL,
        decided_by VARCHAR(120),
        decided_at TIMESTAMPTZ,
        approved BOOLEAN,
        ended_by VARCHAR(120),
        ended_at TIMESTAMPTZ)""",
    """CREATE INDEX IF NOT EXISTS ix_break_glass_grants_tenant
        ON break_glass_grants (tenant_id, requested_at DESC)""",
    "GRANT SELECT, INSERT, UPDATE ON break_glass_grants TO ivaas_app",
    "ALTER TABLE break_glass_grants ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE break_glass_grants FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON break_glass_grants",
    """CREATE POLICY tenant_isolation ON break_glass_grants
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS break_glass_grants")
