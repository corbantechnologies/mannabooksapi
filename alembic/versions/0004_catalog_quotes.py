"""Public catalog quote requests: per-shop auto-issue setting.

Revision ID: 0004_catalog_quotes
Revises: 0003_payroll_statutory
Create Date: 2026-10-16
"""
from alembic import op

revision = "0004_catalog_quotes"
down_revision = "0003_payroll_statutory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS catalog_auto_issue_quotes BOOLEAN DEFAULT FALSE NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE shops DROP COLUMN IF EXISTS catalog_auto_issue_quotes")
