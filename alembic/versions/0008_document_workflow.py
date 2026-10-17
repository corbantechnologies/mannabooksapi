"""Document workflow: Expired status for quotations past their validity date.

Also repairs loyalty earn rates saved inverted by the old settings endpoint (0.01 instead of 100).

Revision ID: 0008_document_workflow
Revises: 0007_backfill_legacy_columns
Create Date: 2026-10-17
"""
from alembic import op

revision = "0008_document_workflow"
down_revision = "0007_backfill_legacy_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE doc_status ADD VALUE IF NOT EXISTS 'EXPIRED'")
    # Quotations the old expiry job cancelled automatically were never declined by anyone.
    op.execute(
        "UPDATE documents SET status = 'EXPIRED' WHERE type = 'QUOTATION' AND status = 'CANCELLED' "
        "AND client_portal_response IS NULL AND due_date IS NOT NULL AND due_date < now()"
    )
    op.execute(
        "UPDATE loyalty_programs SET earn_rate_kes = ROUND(1 / earn_rate_kes, 2) "
        "WHERE earn_rate_kes > 0 AND earn_rate_kes < 1"
    )


def downgrade() -> None:
    op.execute("UPDATE documents SET status = 'CANCELLED' WHERE status = 'EXPIRED'")
