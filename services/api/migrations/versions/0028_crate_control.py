"""Controlled bays (ADR 0004): drivers, bay standards, crate transactions and the
columns the cameras measured at a controlled bay.

Every table is tenant-owned and row-level secured like every tenant table (0012).
A driver's PIN is held only as an Argon2id hash. A badge code is unique per tenant,
so a scan names exactly one driver.
"""

from alembic import op

revision = "0028_crate_control"
down_revision = "0027_report_workbooks"
branch_labels = None
depends_on = None

TABLES = {
    "drivers": """CREATE TABLE IF NOT EXISTS drivers (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        name VARCHAR(120) NOT NULL,
        badge_code VARCHAR(32) NOT NULL,
        employee_ref VARCHAR(40) NOT NULL DEFAULT '',
        phone VARCHAR(32) NOT NULL DEFAULT '',
        active BOOLEAN NOT NULL DEFAULT true,
        pin_hash TEXT,
        created_at TIMESTAMPTZ,
        UNIQUE (tenant_id, badge_code))""",
    "bay_standards": """CREATE TABLE IF NOT EXISTS bay_standards (
        bay_id UUID PRIMARY KEY REFERENCES bays (id) ON DELETE CASCADE,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        columns_per_unit INTEGER NOT NULL CHECK (columns_per_unit BETWEEN 1 AND 20),
        layers INTEGER NOT NULL CHECK (layers BETWEEN 1 AND 40),
        controlled BOOLEAN NOT NULL DEFAULT true)""",
    "crate_transactions": """CREATE TABLE IF NOT EXISTS crate_transactions (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        bay_id UUID NOT NULL REFERENCES bays (id),
        driver_id UUID NOT NULL REFERENCES drivers (id),
        session_id UUID NOT NULL REFERENCES loading_sessions (id),
        direction VARCHAR(16) NOT NULL,
        declared_units INTEGER NOT NULL,
        standard_columns INTEGER NOT NULL,
        standard_layers INTEGER NOT NULL,
        opened_at TIMESTAMPTZ NOT NULL,
        checked_in_by VARCHAR(128) NOT NULL,
        status VARCHAR(16) NOT NULL,
        closed_at TIMESTAMPTZ,
        full_columns INTEGER,
        non_standard_columns INTEGER,
        unsure_columns INTEGER,
        counted_crates INTEGER,
        validated_status VARCHAR(16),
        accepted_by VARCHAR(128),
        accepted_at TIMESTAMPTZ,
        accept_reason VARCHAR(32),
        accept_note VARCHAR(280))""",
    "stack_observations": """CREATE TABLE IF NOT EXISTS stack_observations (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        session_id UUID NOT NULL REFERENCES loading_sessions (id),
        track_id INTEGER NOT NULL,
        camera_id UUID NOT NULL,
        direction VARCHAR(16) NOT NULL,
        crates INTEGER NOT NULL,
        confidence DOUBLE PRECISION NOT NULL,
        agreement DOUBLE PRECISION,
        at TIMESTAMPTZ NOT NULL)""",
}

UPGRADE: list[str] = []
for _name, _create in TABLES.items():
    UPGRADE += [
        _create,
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_name} TO ivaas_app",
        f"ALTER TABLE {_name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {_name} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS tenant_isolation ON {_name}",
        f"""CREATE POLICY tenant_isolation ON {_name}
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
    ]
UPGRADE += [
    "CREATE INDEX IF NOT EXISTS crate_transactions_opened ON crate_transactions (opened_at)",
    "CREATE INDEX IF NOT EXISTS crate_transactions_bay_status "
    "ON crate_transactions (bay_id, status)",
    "CREATE INDEX IF NOT EXISTS stack_observations_session ON stack_observations (session_id)",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    for name in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
