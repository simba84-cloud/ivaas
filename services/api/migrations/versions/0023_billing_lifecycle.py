"""Billing, second slice (M7): due dates and payments, partner holds, and partners'
wholesale invoices.

A partner's invoice belongs to the partner, not to any one tenant, so like the
tenants table it is a platform record, reached only through routes that check the
caller may see that partner; its parts break it down by customer. It is numbered
from the same sequence as tenants' invoices.
"""

from alembic import op

revision = "0023_billing_lifecycle"
down_revision = "0022_billing"
branch_labels = None
depends_on = None

UPGRADE = [
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS due_date DATE",
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payments JSONB NOT NULL DEFAULT '[]'",
    # a partner's hold suspends one customer; paying an invoice never lifts it
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS on_hold BOOLEAN NOT NULL DEFAULT false",
    """CREATE TABLE IF NOT EXISTS partner_invoices (
        id UUID PRIMARY KEY,
        partner_id UUID NOT NULL REFERENCES partners (id),
        number VARCHAR(40) NOT NULL UNIQUE,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        currency VARCHAR(3) NOT NULL,
        parts JSONB NOT NULL,
        tax_name VARCHAR(20) NOT NULL,
        tax_rate NUMERIC(6, 4) NOT NULL,
        price_book VARCHAR(80) NOT NULL,
        placeholder BOOLEAN NOT NULL,
        issued_at TIMESTAMPTZ NOT NULL,
        due_date DATE,
        payments JSONB NOT NULL DEFAULT '[]',
        UNIQUE (partner_id, period_start, period_end))""",
    "GRANT SELECT, INSERT, UPDATE ON partner_invoices TO ivaas_app",
    # issued invoices are records: the application pays them, it never removes them
    # (0012's default privileges would otherwise grant DELETE on every new table)
    "REVOKE DELETE ON partner_invoices FROM ivaas_app",
    "REVOKE DELETE ON invoices FROM ivaas_app",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS partner_invoices")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS on_hold")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS payments")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS due_date")
