"""The fleet register, how each load was identified, and people's count corrections.

vehicles is tenant-owned and row-level secured like every tenant table (0012);
plate_key (the confusable-folded plate) is unique per tenant, so one truck cannot be
registered twice under two spellings. loading_sessions gains the registered truck,
the raw plate read, who identified it, and a correction that sits beside the AI
count rather than replacing it.
"""

from alembic import op

revision = "0017_fleet"
down_revision = "0016_evidence_clips"
branch_labels = None
depends_on = None

SESSION_COLUMNS = [
    ("vehicle_id", "UUID"),
    ("plate_read", "VARCHAR(16)"),
    ("identified_by", "VARCHAR(16)"),
    ("override_count", "INTEGER"),
    ("override_reason", "VARCHAR(32)"),
    ("override_note", "VARCHAR(280)"),
    ("override_by", "VARCHAR(128)"),
    ("override_at", "TIMESTAMPTZ"),
]

UPGRADE = (
    [
        """CREATE TABLE IF NOT EXISTS vehicles (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        plate VARCHAR(16) NOT NULL,
        plate_key VARCHAR(16) NOT NULL,
        fleet_number VARCHAR(40) NOT NULL DEFAULT '',
        operator VARCHAR(120) NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ,
        UNIQUE (tenant_id, plate_key))""",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON vehicles TO ivaas_app",
        "ALTER TABLE vehicles ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE vehicles FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS tenant_isolation ON vehicles",
        """CREATE POLICY tenant_isolation ON vehicles
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
    ]
    + [
        f"ALTER TABLE loading_sessions ADD COLUMN IF NOT EXISTS {name} {kind}"
        for name, kind in SESSION_COLUMNS
    ]
    + [
        "ALTER TABLE loading_sessions ADD CONSTRAINT loading_sessions_vehicle_fkey "
        "FOREIGN KEY (vehicle_id) REFERENCES vehicles (id)",
        # RLS is forced on loading_sessions: a data update must see every tenant's rows
        "SELECT set_config('app.scope', 'system', true)",
        # reads made before the register existed were the plate as read
        "UPDATE loading_sessions SET plate_read = plate "
        "WHERE plate IS NOT NULL AND plate_read IS NULL",
    ]
)


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE loading_sessions DROP CONSTRAINT IF EXISTS loading_sessions_vehicle_fkey"
    )
    for name, _ in SESSION_COLUMNS:
        op.execute(f"ALTER TABLE loading_sessions DROP COLUMN IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS vehicles")
