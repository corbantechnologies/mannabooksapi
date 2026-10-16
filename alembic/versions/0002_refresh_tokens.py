"""Rotating refresh tokens (15-minute access tokens + 30-day refresh families, 12-hour idle timeout).

Revision ID: 0002_refresh_tokens
Revises: 0001_baseline
Create Date: 2026-10-16
"""
from alembic import op

revision = "0002_refresh_tokens"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            family_id UUID NOT NULL,
            token_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITHOUT TIME ZONE,
            revoke_reason VARCHAR(20),
            replaced_by_id UUID,
            user_agent VARCHAR(255),
            ip_address VARCHAR(64),
            PRIMARY KEY (id),
            FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_family_id ON refresh_tokens (family_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_refresh_tokens_token_hash ON refresh_tokens (token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_tokens")
