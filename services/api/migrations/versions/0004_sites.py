"""Persist sites.

Site has always existed in the domain and in bays.site_id, but there was no table
behind it, so a bay pointed at a UUID with no row and the portal had to hardcode
the site name. Backfills a row for every site_id already referenced before adding
the foreign key, so existing bays keep working.
"""

from alembic import op

revision = "0004_sites"
down_revision = "0003_session_approvals"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS sites (
        id UUID PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        timezone VARCHAR(64) NOT NULL DEFAULT 'UTC')""",
    # every site_id a bay already references must exist before the FK can be added
    """INSERT INTO sites (id, name, timezone)
       SELECT DISTINCT b.site_id, 'Site', 'UTC' FROM bays b
       WHERE NOT EXISTS (SELECT 1 FROM sites s WHERE s.id = b.site_id)""",
    """ALTER TABLE bays DROP CONSTRAINT IF EXISTS bays_site_id_fkey""",
    """ALTER TABLE bays ADD CONSTRAINT bays_site_id_fkey
       FOREIGN KEY (site_id) REFERENCES sites (id)""",
]


def upgrade() -> None:
    for statement in STATEMENTS:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE bays DROP CONSTRAINT IF EXISTS bays_site_id_fkey")
    op.execute("DROP TABLE IF EXISTS sites")
