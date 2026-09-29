"""Who acknowledged which alert, and when."""

from alembic import op

revision = "0009_alert_acknowledgements"
down_revision = "0008_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # one statement per execute: asyncpg rejects multi-statement strings
    op.execute(
        """CREATE TABLE IF NOT EXISTS alert_acknowledgements (
            key VARCHAR(200) PRIMARY KEY,
            acknowledged_by VARCHAR(120) NOT NULL,
            acknowledged_at TIMESTAMPTZ NOT NULL,
            note TEXT)"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_alert_acknowledgements_at "
        "ON alert_acknowledgements (acknowledged_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_acknowledgements")
