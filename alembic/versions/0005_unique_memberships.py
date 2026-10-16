"""One membership per (workspace, user).

Duplicates were possible because the "auto-heal owner membership" code could insert twice.
Keeps one row per pair: an active row over an inactive one, then the oldest.

Revision ID: 0005_unique_memberships
Revises: 0004_catalog_quotes
Create Date: 2026-10-16
"""
from alembic import op

revision = "0005_unique_memberships"
down_revision = "0004_catalog_quotes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM shop_members m
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY shop_id, user_id
                       ORDER BY is_active DESC, created_at ASC, id ASC
                   ) AS rn
            FROM shop_members
        ) ranked
        WHERE m.id = ranked.id AND ranked.rn > 1
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_shop_members_shop_user ON shop_members (shop_id, user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_shop_members_shop_user")
