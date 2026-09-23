"""Baseline: the schema as it stood before migrations were introduced.

Idempotent on purpose: the running POC database already has these tables (created by
create_all), so every statement checks first. A fresh database gets the same result.
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS bays (
        id UUID PRIMARY KEY,
        site_id UUID NOT NULL,
        name VARCHAR(120) NOT NULL,
        height_m FLOAT NOT NULL,
        width_m FLOAT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_bays_site_id ON bays (site_id)",
    """CREATE TABLE IF NOT EXISTS cameras (
        id UUID PRIMARY KEY,
        bay_id UUID NOT NULL REFERENCES bays (id),
        name VARCHAR(120) NOT NULL,
        role VARCHAR(32) NOT NULL,
        stream_path VARCHAR(255) NOT NULL UNIQUE,
        source_url VARCHAR(2048),
        status VARCHAR(16) NOT NULL,
        last_seen_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS ix_cameras_bay_id ON cameras (bay_id)",
    """CREATE TABLE IF NOT EXISTS loading_sessions (
        id UUID PRIMARY KEY,
        bay_id UUID NOT NULL REFERENCES bays (id),
        direction VARCHAR(16) NOT NULL,
        status VARCHAR(16) NOT NULL,
        plate VARCHAR(16),
        ai_count INTEGER NOT NULL,
        manual_count INTEGER,
        opened_at TIMESTAMPTZ NOT NULL,
        closed_at TIMESTAMPTZ,
        plate_last_seen_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS ix_loading_sessions_bay_id ON loading_sessions (bay_id)",
    "CREATE INDEX IF NOT EXISTS ix_loading_sessions_status ON loading_sessions (status)",
    "CREATE INDEX IF NOT EXISTS ix_loading_sessions_plate ON loading_sessions (plate)",
    "CREATE INDEX IF NOT EXISTS ix_loading_sessions_opened_at ON loading_sessions (opened_at)",
    # databases created by the pre-migration create_all may lack these
    "ALTER TABLE loading_sessions ADD COLUMN IF NOT EXISTS plate_last_seen_at TIMESTAMPTZ",
    "ALTER TABLE cameras ALTER COLUMN source_url TYPE VARCHAR(2048)",
]


def upgrade() -> None:
    for statement in STATEMENTS:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    for table in ("loading_sessions", "cameras", "bays"):  # one per call, for asyncpg
        op.execute(f"DROP TABLE IF EXISTS {table}")
