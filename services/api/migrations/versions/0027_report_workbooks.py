"""Filed daily reports keep an Excel workbook beside the PDF and CSV.

Reports filed before this have none, so the column is nullable and the portal shows
the workbook only where there is one.
"""

from alembic import op

revision = "0027_report_workbooks"
down_revision = "0026_sso"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE daily_reports ADD COLUMN IF NOT EXISTS xlsx_key VARCHAR(300)")


def downgrade() -> None:
    op.execute("ALTER TABLE daily_reports DROP COLUMN IF EXISTS xlsx_key")
