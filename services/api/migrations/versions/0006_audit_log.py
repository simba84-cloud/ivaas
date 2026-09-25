"""Who did what: an append-only record of actions that change a number or the setup."""

from alembic import op

revision = "0006_audit_log"
down_revision = "0005_job_heartbeat"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS audit_log (
        id UUID PRIMARY KEY,
        at TIMESTAMPTZ NOT NULL,
        actor VARCHAR(120) NOT NULL,
        action VARCHAR(32) NOT NULL,
        subject VARCHAR(200) NOT NULL,
        detail JSONB NOT NULL DEFAULT '{}'::jsonb)""",
    "CREATE INDEX IF NOT EXISTS ix_audit_log_at ON audit_log (at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_audit_log_actor ON audit_log (actor)",
    "CREATE INDEX IF NOT EXISTS ix_audit_log_action ON audit_log (action)",
]


def upgrade() -> None:
    for statement in STATEMENTS:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log")
