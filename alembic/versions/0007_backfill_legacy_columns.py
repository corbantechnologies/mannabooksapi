"""Bring tables that pre-date the baseline up to the current models.

The baseline used CREATE TABLE IF NOT EXISTS, so tables that already existed (CRM, loyalty, budgets,
cost centres) kept their old columns. This adds every column the code uses, copies data across from
the old column where one has an obvious successor, and relaxes NOT NULL on the old columns so inserts
that no longer set them succeed. Old columns are kept (no data is dropped). Safe to run on a database
that is already up to date.

Revision ID: 0007_backfill_legacy_columns
Revises: 0006_journal_sources
Create Date: 2026-10-17
"""
from alembic import op
from sqlalchemy import text

revision = "0007_backfill_legacy_columns"
down_revision = "0006_journal_sources"
branch_labels = None
depends_on = None

# table -> [(column, definition)]
NEW_COLUMNS = {
    "cost_centers": [("department", "VARCHAR(100)")],
    "loyalty_programs": [("updated_at", "TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()")],
    "membership_tiers": [
        ("min_spend_kes", "NUMERIC(12, 2) NOT NULL DEFAULT 0"),
        ("discount_percent", "NUMERIC(5, 2) NOT NULL DEFAULT 0"),
        ("points_multiplier", "NUMERIC(4, 2) NOT NULL DEFAULT 1"),
        ("badge_color", "VARCHAR(20)"),
    ],
    "budgets": [
        ("month", "INTEGER NOT NULL DEFAULT EXTRACT(MONTH FROM now())::int"),
        ("year", "INTEGER NOT NULL DEFAULT EXTRACT(YEAR FROM now())::int"),
        ("monthly_limit", "NUMERIC(15, 2) NOT NULL DEFAULT 0"),
    ],
    "client_loyalty_accounts": [
        ("member_number", "VARCHAR(50)"),
        ("lifetime_points", "INTEGER NOT NULL DEFAULT 0"),
        ("wallet_balance_kes", "NUMERIC(12, 2) NOT NULL DEFAULT 0"),
        ("membership_status", "VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'"),
        ("expires_at", "TIMESTAMP WITHOUT TIME ZONE"),
    ],
    "deals": [
        ("contact_name", "VARCHAR(255) NOT NULL DEFAULT 'Primary Contact'"),
        ("contact_email", "VARCHAR(255)"),
        ("contact_phone", "VARCHAR(50)"),
        ("estimated_value", "NUMERIC(14, 2) NOT NULL DEFAULT 0"),
        ("win_probability", "INTEGER NOT NULL DEFAULT 10"),
        ("expected_close_date", "TIMESTAMP WITHOUT TIME ZONE"),
        ("loss_reason", "TEXT"),
        ("notes", "TEXT"),
    ],
    "contracts": [
        ("alert_30_day_sent_at", "TIMESTAMP WITHOUT TIME ZONE"),
        ("alert_60_day_sent_at", "TIMESTAMP WITHOUT TIME ZONE"),
    ],
    "deal_activities": [
        ("activity_type", "deal_activity_type NOT NULL DEFAULT 'NOTE'"),
        ("title", "VARCHAR(255) NOT NULL DEFAULT 'Activity'"),
        ("body", "TEXT NOT NULL DEFAULT ''"),
    ],
    "loyalty_ledger": [
        ("points_delta", "INTEGER NOT NULL DEFAULT 0"),
        ("wallet_delta_kes", "NUMERIC(12, 2) NOT NULL DEFAULT 0"),
        ("running_points_balance", "INTEGER NOT NULL DEFAULT 0"),
        ("running_wallet_balance_kes", "NUMERIC(12, 2) NOT NULL DEFAULT 0"),
        ("notes", "TEXT"),
        ("created_by_id", "UUID REFERENCES users(id) ON DELETE SET NULL"),
    ],
    "proposals": [
        ("executive_summary", "TEXT"),
        ("scope_of_work", "TEXT"),
        ("validity_days", "INTEGER NOT NULL DEFAULT 30"),
        ("viewed_at", "TIMESTAMP WITHOUT TIME ZONE"),
        ("view_count", "INTEGER NOT NULL DEFAULT 0"),
        ("responded_at", "TIMESTAMP WITHOUT TIME ZONE"),
        ("signer_name", "VARCHAR(255)"),
        ("signature_data_url", "TEXT"),
        ("amendment_notes", "TEXT"),
        ("expires_at", "TIMESTAMP WITHOUT TIME ZONE"),
    ],
    "proposal_items": [
        ("package_label", "VARCHAR(100)"),
        ("notes", "TEXT"),
        ("display_order", "INTEGER NOT NULL DEFAULT 0"),
    ],
}

# (table, new column, SQL expression over the old columns, old columns it needs)
BACKFILLS = [
    ("deals", "estimated_value", "COALESCE(value, 0)", ["value"]),
    ("deals", "win_probability", "COALESCE(probability, 10)", ["probability"]),
    ("deals", "expected_close_date", "close_date::timestamp", ["close_date"]),
    ("deals", "loss_reason", "lost_reason", ["lost_reason"]),
    ("deal_activities", "body", "COALESCE(note, '')", ["note"]),
    ("loyalty_ledger", "points_delta", "COALESCE(points, 0)", ["points"]),
    ("loyalty_ledger", "running_points_balance", "COALESCE(running_balance, 0)", ["running_balance"]),
    ("loyalty_ledger", "notes", "reason", ["reason"]),
    ("membership_tiers", "discount_percent", "COALESCE(discount_percentage, 0)", ["discount_percentage"]),
    ("client_loyalty_accounts", "lifetime_points", "COALESCE(lifetime_points_earned, 0)", ["lifetime_points_earned"]),
    ("budgets", "monthly_limit", "COALESCE(budget_amount, 0)", ["budget_amount"]),
    ("proposals", "expires_at", "valid_until::timestamp", ["valid_until"]),
    ("proposals", "responded_at", "COALESCE(accepted_at, rejected_at)", ["accepted_at", "rejected_at"]),
]

# Old NOT NULL columns the code no longer writes; inserts fail unless they accept NULL.
LEGACY_REQUIRED = {
    "shop_terms": ["is_default_quote", "is_default_receipt", "is_default_delivery"],
    "deals": ["value", "probability"],
    "budgets": ["budget_amount", "actual_amount", "period"],
    "cost_centers": ["budget_amount"],
    "membership_tiers": ["min_points", "discount_percentage"],
    "loyalty_programs": ["points_per_kes", "kes_per_point", "is_active", "name"],
    "proposals": ["tax_total", "grand_total"],
    "client_loyalty_accounts": ["lifetime_points_earned", "lifetime_points_redeemed"],
    "proposal_items": ["discount_amount", "tax_amount", "tax_type"],
    "loyalty_ledger": ["points", "running_balance"],
    "deal_activities": ["type", "note"],
    "payment_methods": ["type"],
}


def _columns(bind, table: str) -> set[str]:
    rows = bind.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :t"),
        {"t": table},
    )
    return {r[0] for r in rows}


def upgrade() -> None:
    bind = op.get_bind()
    before = {t: _columns(bind, t) for t in set(NEW_COLUMNS) | set(LEGACY_REQUIRED)}

    for table, cols in NEW_COLUMNS.items():
        if not before[table]:
            continue  # table absent (created fresh by the baseline with all columns)
        for name, definition in cols:
            op.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "{name}" {definition}')

    # Copy data only into columns this migration just created, so re-runs never overwrite edits.
    for table, new_col, expr, old_cols in BACKFILLS:
        cols = before[table]
        if cols and new_col not in cols and all(c in cols for c in old_cols):
            op.execute(f'UPDATE {table} SET "{new_col}" = {expr}')

    for table, cols in LEGACY_REQUIRED.items():
        for col in cols:
            if col in before[table]:
                op.execute(f'ALTER TABLE {table} ALTER COLUMN "{col}" DROP NOT NULL')


def downgrade() -> None:
    # Columns added here are used by the current code; dropping them would lose data.
    pass
