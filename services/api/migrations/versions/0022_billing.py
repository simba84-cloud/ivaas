"""Billing (M7): each tenant's subscription, its usage ledger, and issued invoices.

All three are tenant-owned and row-level secured (0012). The ledger is append-only
with one row per (tenant, idempotency key), so a replayed event is refused by the
database itself. Invoice numbers come from one platform-wide sequence; a period is
invoiced at most once per tenant.
"""

from alembic import op

revision = "0022_billing"
down_revision = "0021_edge_availability"
branch_labels = None
depends_on = None

TABLES = ("subscriptions", "usage_events", "invoices")

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS subscriptions (
        tenant_id UUID PRIMARY KEY REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        segments JSONB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS usage_events (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        meter VARCHAR(40) NOT NULL,
        quantity NUMERIC(20, 6) NOT NULL CHECK (quantity >= 0),
        at TIMESTAMPTZ NOT NULL,
        key VARCHAR(200) NOT NULL,
        UNIQUE (tenant_id, key))""",
    "CREATE INDEX IF NOT EXISTS ix_usage_events_at ON usage_events (tenant_id, at)",
    "CREATE SEQUENCE IF NOT EXISTS invoice_number_seq",
    """CREATE TABLE IF NOT EXISTS invoices (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        number VARCHAR(40) NOT NULL UNIQUE,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        currency VARCHAR(3) NOT NULL,
        lines JSONB NOT NULL,
        tax_name VARCHAR(20) NOT NULL,
        tax_rate NUMERIC(6, 4) NOT NULL,
        price_book VARCHAR(60) NOT NULL,
        placeholder BOOLEAN NOT NULL,
        issued_at TIMESTAMPTZ NOT NULL,
        UNIQUE (tenant_id, period_start, period_end))""",
    "GRANT USAGE ON SEQUENCE invoice_number_seq TO ivaas_app",
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
# the ledger is append-only: the application may add to it, never change or remove
UPGRADE += ["REVOKE UPDATE, DELETE ON usage_events FROM ivaas_app"]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")
