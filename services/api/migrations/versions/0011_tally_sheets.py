"""Tally sheets: the paper counts entered as ground truth, with every stack line."""

from alembic import op

revision = "0011_tally_sheets"
down_revision = "0010_security"
branch_labels = None
depends_on = None

# one statement per execute: asyncpg rejects multi-statement strings
UPGRADE = [
    """CREATE TABLE IF NOT EXISTS tally_sheets (
        id UUID PRIMARY KEY,
        sheet_id VARCHAR(80) NOT NULL UNIQUE,
        bay_id UUID NOT NULL REFERENCES bays (id),
        date DATE NOT NULL,
        plate VARCHAR(32) NOT NULL,
        direction VARCHAR(16) NOT NULL,
        start_time TIME,
        end_time TIME,
        total_on_paper INTEGER,
        pages INTEGER,
        counted_by VARCHAR(120),
        verified_by VARCHAR(120),
        entered_by VARCHAR(120),
        notes TEXT,
        entered_by_user VARCHAR(120) NOT NULL,
        entered_at TIMESTAMPTZ NOT NULL,
        session_id UUID REFERENCES loading_sessions (id),
        status VARCHAR(16) NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_tally_sheets_status ON tally_sheets (status)",
    "CREATE INDEX IF NOT EXISTS ix_tally_sheets_session ON tally_sheets (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_tally_sheets_entered_at ON tally_sheets (entered_at)",
    """CREATE TABLE IF NOT EXISTS tally_lines (
        sheet_pk UUID NOT NULL REFERENCES tally_sheets (id) ON DELETE CASCADE,
        line_no INTEGER NOT NULL,
        crates INTEGER NOT NULL,
        note VARCHAR(40),
        PRIMARY KEY (sheet_pk, line_no))""",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for table in ("tally_lines", "tally_sheets"):  # one per call, for asyncpg
        op.execute(f"DROP TABLE IF EXISTS {table}")
