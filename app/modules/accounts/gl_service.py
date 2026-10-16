import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models.identity import Shop
from app.models.account import ChartOfAccount
from app.models.journal import JournalEntry
from app.models.document import Document
from app.models.expense import Expense
from app.core.datetime_utils import utcnow


DEFAULT_ACCOUNTS = [
    {"code": "1000", "name": "Current Assets", "account_type": "ASSET", "is_system": True},
    {"code": "1200", "name": "Cash and Bank", "account_type": "ASSET", "is_system": True},
    {"code": "1300", "name": "Accounts Receivable", "account_type": "ASSET", "is_system": True},
    {"code": "1400", "name": "Inventory Asset", "account_type": "ASSET", "is_system": True},
    {"code": "2000", "name": "Current Liabilities", "account_type": "LIABILITY", "is_system": True},
    {"code": "2100", "name": "Accounts Payable", "account_type": "LIABILITY", "is_system": True},
    {"code": "2200", "name": "VAT Payable (Output VAT)", "account_type": "LIABILITY", "is_system": True},
    {"code": "3000", "name": "Owner's Equity", "account_type": "EQUITY", "is_system": True},
    {"code": "3100", "name": "Retained Earnings", "account_type": "EQUITY", "is_system": True},
    {"code": "3200", "name": "Opening Balances Equity", "account_type": "EQUITY", "is_system": True},
    {"code": "4000", "name": "Sales Revenue", "account_type": "REVENUE", "is_system": True},
    {"code": "5000", "name": "Cost of Goods Sold (COGS)", "account_type": "EXPENSE", "is_system": True},
    {"code": "6000", "name": "Operating Expenses", "account_type": "EXPENSE", "is_system": True},
]


async def ensure_default_accounts(db: AsyncSession, shop_id: uuid.UUID) -> Dict[str, ChartOfAccount]:
    """Ensures standard Chart of Accounts exist for a workspace and returns account mapping by code."""
    res = await db.execute(select(ChartOfAccount).where(ChartOfAccount.shop_id == shop_id))
    existing_accounts = {acc.code: acc for acc in res.scalars().all()}

    for da in DEFAULT_ACCOUNTS:
        code = da["code"]
        if code not in existing_accounts:
            acc = ChartOfAccount(
                shop_id=shop_id,
                code=code,
                name=da["name"],
                account_type=da["account_type"],
                is_system=da["is_system"],
            )
            db.add(acc)
            existing_accounts[code] = acc

    await db.flush()
    return existing_accounts


async def post_document_to_gl(
    db: AsyncSession,
    shop_id: uuid.UUID,
    doc: Document,
) -> List[JournalEntry]:
    """Backwards-compatible name; see app.modules.accounts.posting.sync_document_gl."""
    from app.modules.accounts.posting import sync_document_gl

    return await sync_document_gl(db, shop_id, doc)


async def run_full_gl_migration(db: AsyncSession, shop_id: uuid.UUID) -> Dict[str, Any]:
    """
    Post everything that should be in the ledger but isn't yet (documents, invoice payments, expenses,
    other income). Safe to run repeatedly; records in closed periods are skipped and reported.
    """
    from app.modules.accounts.posting import repair_ledger

    await ensure_default_accounts(db, shop_id)
    result = await repair_ledger(db, shop_id)
    skipped = len(result["skipped"])
    return {
        "success": True,
        "posted": result["fixed"],
        "skipped": result["skipped"],
        "docsMigrated": result["fixed"],
        "expensesMigrated": 0,
        "summary": f"General Ledger sync complete: {result['fixed']} records posted"
        + (f", {skipped} skipped because their period is closed." if skipped else "."),
        "integrity": result["after"],
    }
