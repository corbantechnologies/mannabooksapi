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
    """Posts balanced double-entry accounting records for an issued or paid commercial document."""
    # 1. Verify shop has GL enabled
    shop = await db.get(Shop, shop_id)
    if not shop or not shop.is_gl_enabled:
        return []

    # 2. Only post financial document types
    doc_type = str(doc.type).upper()
    doc_status = str(doc.status).upper()
    if doc_type not in ("INVOICE", "RECEIPT", "CREDIT_NOTE"):
        return []
    if doc_status in ("DRAFT", "CANCELLED"):
        return []

    # 3. Check if already posted (idempotent)
    q_existing = select(JournalEntry).where(
        and_(JournalEntry.shop_id == shop_id, JournalEntry.source_id == doc.id)
    )
    existing_res = await db.execute(q_existing)
    if existing_res.scalars().first():
        return []

    # 4. Resolve default accounts
    accounts = await ensure_default_accounts(db, shop_id)
    cash_acc = accounts.get("1200")
    ar_acc = accounts.get("1300")
    vat_acc = accounts.get("2200")
    revenue_acc = accounts.get("4000")

    if not (cash_acc and ar_acc and vat_acc and revenue_acc):
        return []

    entries: List[JournalEntry] = []
    sub_total = Decimal(str(doc.sub_total or "0.00"))
    tax_amount = Decimal(str(doc.tax_amount or "0.00"))
    grand_total = Decimal(str(doc.grand_total or "0.00"))
    entry_date = doc.issue_date or datetime.utcnow()

    if doc_type == "INVOICE":
        debit_target = cash_acc.id if doc_status == "PAID" else ar_acc.id
        desc_label = f"Invoice {doc.doc_number} (Settled)" if doc_status == "PAID" else f"Invoice {doc.doc_number}"

        if sub_total > Decimal("0.00"):
            entries.append(
                JournalEntry(
                    shop_id=shop_id,
                    entry_date=entry_date,
                    description=f"{desc_label} - Sales Revenue",
                    debit_account_id=debit_target,
                    credit_account_id=revenue_acc.id,
                    amount=sub_total,
                    reference_number=doc.doc_number,
                    source_type="document",
                    source_id=doc.id,
                )
            )
        if tax_amount > Decimal("0.00"):
            entries.append(
                JournalEntry(
                    shop_id=shop_id,
                    entry_date=entry_date,
                    description=f"{desc_label} - Output VAT (16%)",
                    debit_account_id=debit_target,
                    credit_account_id=vat_acc.id,
                    amount=tax_amount,
                    reference_number=doc.doc_number,
                    source_type="document",
                    source_id=doc.id,
                )
            )

    elif doc_type == "RECEIPT":
        # Direct POS or Cash sales
        desc_label = f"Receipt {doc.doc_number}"
        if sub_total > Decimal("0.00"):
            entries.append(
                JournalEntry(
                    shop_id=shop_id,
                    entry_date=entry_date,
                    description=f"{desc_label} - Sales Revenue",
                    debit_account_id=cash_acc.id,
                    credit_account_id=revenue_acc.id,
                    amount=sub_total,
                    reference_number=doc.doc_number,
                    source_type="document",
                    source_id=doc.id,
                )
            )
        if tax_amount > Decimal("0.00"):
            entries.append(
                JournalEntry(
                    shop_id=shop_id,
                    entry_date=entry_date,
                    description=f"{desc_label} - Output VAT",
                    debit_account_id=cash_acc.id,
                    credit_account_id=vat_acc.id,
                    amount=tax_amount,
                    reference_number=doc.doc_number,
                    source_type="document",
                    source_id=doc.id,
                )
            )

    elif doc_type == "CREDIT_NOTE":
        credit_target = cash_acc.id if doc_status == "PAID" else ar_acc.id
        desc_label = f"Credit Note {doc.doc_number}"

        if sub_total > Decimal("0.00"):
            entries.append(
                JournalEntry(
                    shop_id=shop_id,
                    entry_date=entry_date,
                    description=f"{desc_label} - Revenue Reversal",
                    debit_account_id=revenue_acc.id,
                    credit_account_id=credit_target,
                    amount=sub_total,
                    reference_number=doc.doc_number,
                    source_type="document",
                    source_id=doc.id,
                )
            )
        if tax_amount > Decimal("0.00"):
            entries.append(
                JournalEntry(
                    shop_id=shop_id,
                    entry_date=entry_date,
                    description=f"{desc_label} - Output VAT Reversal",
                    debit_account_id=vat_acc.id,
                    credit_account_id=credit_target,
                    amount=tax_amount,
                    reference_number=doc.doc_number,
                    source_type="document",
                    source_id=doc.id,
                )
            )

    for entry in entries:
        db.add(entry)

    return entries


async def run_full_gl_migration(db: AsyncSession, shop_id: uuid.UUID) -> Dict[str, Any]:
    """
    Chronologically migrates historical documents and operational expenses into the double-entry
    General Ledger without duplicates.
    """
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise ValueError("Shop not found")

    # 1. Ensure Standard Chart of Accounts
    accounts = await ensure_default_accounts(db, shop_id)
    cash_acc = accounts["1200"]
    exp_acc = accounts["6000"]

    # 2. Activate GL for shop
    shop.is_gl_enabled = True

    # 3. Migrate Documents (Invoices, Receipts, Credit Notes)
    q_docs = (
        select(Document)
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.type.in_(["INVOICE", "RECEIPT", "CREDIT_NOTE"]),
                Document.status.not_in(["DRAFT", "CANCELLED"]),
            )
        )
        .order_by(Document.issue_date.asc())
    )
    docs_res = await db.execute(q_docs)
    documents = docs_res.scalars().all()

    docs_migrated = 0
    for doc in documents:
        posted = await post_document_to_gl(db, shop_id, doc)
        if posted:
            docs_migrated += 1

    # 4. Migrate Expenses
    q_exp = select(Expense).where(Expense.shop_id == shop_id).order_by(Expense.expense_date.asc())
    exp_res = await db.execute(q_exp)
    expenses = exp_res.scalars().all()

    exp_migrated = 0
    for exp in expenses:
        # Check if already migrated
        q_chk = select(JournalEntry).where(
            and_(JournalEntry.shop_id == shop_id, JournalEntry.source_id == exp.id)
        )
        if (await db.execute(q_chk)).scalars().first():
            continue

        exp_amt = Decimal(str(exp.amount or "0.00"))
        if exp_amt > Decimal("0.00"):
            je = JournalEntry(
                shop_id=shop_id,
                entry_date=exp.expense_date or exp.created_at or datetime.utcnow(),
                description=f"Expense Voucher: {exp.title or exp.category}",
                debit_account_id=exp_acc.id,
                credit_account_id=cash_acc.id,
                amount=exp_amt,
                reference_number=exp.payment_reference,
                source_type="expense",
                source_id=exp.id,
            )
            db.add(je)
            exp_migrated += 1

    await db.commit()

    return {
        "success": True,
        "isGlEnabled": True,
        "accountsConfigured": len(accounts),
        "documentsMigrated": docs_migrated,
        "expensesMigrated": exp_migrated,
        "summary": f"General Ledger migration complete. {docs_migrated} documents and {exp_migrated} expenses posted.",
    }
