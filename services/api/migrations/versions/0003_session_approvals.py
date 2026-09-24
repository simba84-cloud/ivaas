"""Sign-off on disputed loads: who accepted the discrepancy, when and why."""

from alembic import op

revision = "0003_session_approvals"
down_revision = "0002_analysis_jobs"
branch_labels = None
depends_on = None

# status is a VARCHAR, not a Postgres enum, so the new "approved" value needs no type change
STATEMENTS = [
    "ALTER TABLE loading_sessions ADD COLUMN IF NOT EXISTS approved_by VARCHAR(128)",
    "ALTER TABLE loading_sessions ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ",
    "ALTER TABLE loading_sessions ADD COLUMN IF NOT EXISTS approval_reason VARCHAR(32)",
    "ALTER TABLE loading_sessions ADD COLUMN IF NOT EXISTS approval_note VARCHAR(280)",
]


def upgrade() -> None:
    for statement in STATEMENTS:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    for column in ("approved_by", "approved_at", "approval_reason", "approval_note"):
        op.execute(f"ALTER TABLE loading_sessions DROP COLUMN IF EXISTS {column}")
