"""Evidence clips: the video around each count, kept for the tenant's retention period.

Tenant-owned and row-level secured like every tenant table (0012). expires_at is
set when a clip arrives, from the tenant's retention setting at that moment, and a
sweep deletes the object and the row once it has passed.
"""

from alembic import op

revision = "0016_evidence_clips"
down_revision = "0015_model_versions"
branch_labels = None
depends_on = None

UPGRADE = [
    """CREATE TABLE IF NOT EXISTS evidence_clips (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants (id) DEFAULT ivaas_current_tenant(),
        session_id UUID REFERENCES loading_sessions (id),
        bay_id UUID NOT NULL REFERENCES bays (id),
        camera_id UUID NOT NULL,
        kind VARCHAR(16) NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ NOT NULL,
        object_key VARCHAR(300) NOT NULL,
        size_bytes BIGINT NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_evidence_clips_session ON evidence_clips (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_evidence_clips_expires ON evidence_clips (expires_at)",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON evidence_clips TO ivaas_app",
    "ALTER TABLE evidence_clips ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE evidence_clips FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS tenant_isolation ON evidence_clips",
    """CREATE POLICY tenant_isolation ON evidence_clips
        USING (ivaas_tenant_visible(tenant_id))
        WITH CHECK (ivaas_tenant_visible(tenant_id))""",
]


def upgrade() -> None:
    for statement in UPGRADE:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_clips")
