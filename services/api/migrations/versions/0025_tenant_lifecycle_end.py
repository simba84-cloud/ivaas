"""The end of a tenant's life (M8, T8.4 and T8.5): cancellation, and the certificate
left behind when its data is purged.

`tenants` records when and by whom it was cancelled; its data is kept until the
retention window elapses, then purged. The deletion certificate is the platform's
record that it happened, so it outlives the tenant: it names the tenant in
`purged_tenant_id`, deliberately not `tenant_id`, which would make it a row of the
tenant it certifies is gone. Certificates are never changed or deleted.
"""

from alembic import op

revision = "0025_tenant_lifecycle_end"
down_revision = "0024_break_glass"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cancelled_by VARCHAR(120)",
    """CREATE TABLE IF NOT EXISTS deletion_certificates (
        id UUID PRIMARY KEY,
        purged_tenant_id UUID NOT NULL,
        tenant_slug VARCHAR(64) NOT NULL,
        tenant_name VARCHAR(120) NOT NULL,
        purged_at TIMESTAMPTZ NOT NULL,
        purged_by VARCHAR(120) NOT NULL,
        body JSONB NOT NULL,
        signature VARCHAR(64) NOT NULL)""",
    "GRANT SELECT, INSERT ON deletion_certificates TO ivaas_app",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS deletion_certificates")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS cancelled_by")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS cancelled_at")
