"""A heartbeat on analysis jobs, so a dead worker can be told from a busy one.

Reads used to rewrite any RUNNING job to QUEUED on the assumption that seeing one
meant the process had died mid-run. That held while the worker lived inside the API.
With a separate worker it made the API report a running job as queued forever, and
would have handed the same job to a second worker. Staleness is now measured from
this column, which every progress tick touches.
"""

from alembic import op

revision = "0005_job_heartbeat"
down_revision = "0004_sites"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
    "UPDATE analysis_jobs SET updated_at = COALESCE(finished_at, started_at, created_at) "
    "WHERE updated_at IS NULL",
]


def upgrade() -> None:
    for statement in STATEMENTS:  # one per call: asyncpg rejects multi-statement strings
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE analysis_jobs DROP COLUMN IF EXISTS updated_at")
