"""Analysis jobs: uploaded-video reports move from JSON-in-MinIO to a table."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_analysis_jobs"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bay_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bays.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("filename", sa.String(120), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("progress", sa.Float, nullable=False, server_default="0"),
        sa.Column("error", sa.String(500)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_s", sa.Float),
        sa.Column("loads", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("timeline", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("analysis_jobs")
