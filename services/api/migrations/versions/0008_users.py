"""Accounts in the database rather than in configuration.

Passwords were plaintext in settings, which makes a reset impossible and a leak
total. They are Argon2id hashes here. The seed carries the configured accounts
across so an existing deployment keeps working, marked as still holding their
default password so the portal can say so.
"""

from alembic import op

revision = "0008_users"
down_revision = "0007_platform_settings"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        username VARCHAR(64) PRIMARY KEY,
        display_name VARCHAR(120) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        roles VARCHAR(16)[] NOT NULL,
        disabled BOOLEAN NOT NULL DEFAULT FALSE,
        must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
        password_is_default BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL,
        password_changed_at TIMESTAMPTZ,
        last_login_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS ix_users_disabled ON users (disabled)",
]


def upgrade() -> None:
    for statement in STATEMENTS:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")
