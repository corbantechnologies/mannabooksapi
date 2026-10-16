"""Journal source types for opening balances, year-end closing and customer payments.

Revision ID: 0006_journal_sources
Revises: 0005_unique_memberships
Create Date: 2026-10-16
"""
from alembic import op

revision = "0006_journal_sources"
down_revision = "0005_unique_memberships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE cannot run inside a transaction block on older PostgreSQL versions.
    with op.get_context().autocommit_block():
        for value in ("opening_balance", "year_end_close", "payment"):
            op.execute(f"ALTER TYPE journal_source ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Enum values cannot be removed safely once used.
    pass
