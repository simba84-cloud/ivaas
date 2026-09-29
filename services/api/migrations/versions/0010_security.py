"""Site security: zones, incidents, the badge log, enrolled people."""

from alembic import op

revision = "0010_security"
down_revision = "0009_alert_acknowledgements"
branch_labels = None
depends_on = None

# one statement per execute: asyncpg rejects multi-statement strings
UPGRADE = [
    """CREATE TABLE IF NOT EXISTS security_zones (
        id UUID PRIMARY KEY,
        camera_id UUID NOT NULL,
        name VARCHAR(120) NOT NULL,
        polygon JSONB NOT NULL,
        rules JSONB NOT NULL,
        schedule JSONB NOT NULL,
        min_dwell_s DOUBLE PRECISION NOT NULL,
        exclude BOOLEAN NOT NULL,
        badge_door VARCHAR(120))""",
    "CREATE INDEX IF NOT EXISTS ix_security_zones_camera ON security_zones (camera_id)",
    """CREATE TABLE IF NOT EXISTS security_incidents (
        id UUID PRIMARY KEY,
        bay_id UUID NOT NULL,
        camera_id UUID NOT NULL,
        kind VARCHAR(32) NOT NULL,
        detected_at TIMESTAMPTZ NOT NULL,
        confidence DOUBLE PRECISION NOT NULL,
        zone_id UUID,
        zone_name VARCHAR(120),
        snapshot_key VARCHAR(300),
        detail JSONB NOT NULL,
        status VARCHAR(16) NOT NULL,
        acknowledged_by VARCHAR(120),
        acknowledged_at TIMESTAMPTZ,
        resolved_by VARCHAR(120),
        resolved_at TIMESTAMPTZ,
        resolution_note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_security_incidents_bay_at "
    "ON security_incidents (bay_id, detected_at DESC)",
    """CREATE TABLE IF NOT EXISTS badge_events (
        id UUID PRIMARY KEY,
        badge_id VARCHAR(120) NOT NULL,
        door VARCHAR(120) NOT NULL,
        at TIMESTAMPTZ NOT NULL,
        granted BOOLEAN NOT NULL,
        holder VARCHAR(160))""",
    "CREATE INDEX IF NOT EXISTS ix_badge_events_at ON badge_events (at)",
    """CREATE TABLE IF NOT EXISTS enrolled_people (
        id UUID PRIMARY KEY,
        name VARCHAR(160) NOT NULL,
        employee_ref VARCHAR(80) NOT NULL,
        consent_reference VARCHAR(300) NOT NULL,
        enrolled_by VARCHAR(120) NOT NULL,
        enrolled_at TIMESTAMPTZ NOT NULL,
        embedding TEXT NOT NULL)""",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for table in ("enrolled_people", "badge_events", "security_incidents", "security_zones"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
