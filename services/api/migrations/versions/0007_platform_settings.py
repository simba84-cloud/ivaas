"""Operational settings that outlive a restart and do not need one to change."""

from alembic import op

revision = "0007_platform_settings"
down_revision = "0006_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS platform_settings (
            key VARCHAR(64) PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by VARCHAR(120) NOT NULL)"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_settings")
