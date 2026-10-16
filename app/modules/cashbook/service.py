# app/modules/cashbook/service.py
"""
Expenses and other (non-sales) income: one "cashbook" implementation for both.

Every entry posts one balanced journal line when the workspace's general ledger is enabled:
  expense:  Dr 6000 Operating Expenses   / Cr 1200 Cash and Bank
  income:   Dr 1200 Cash and Bank        / Cr 4100 Other Income
Edits re-post the entry; deletes remove it. Every change is written to the audit log.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetime_utils import utcnow
from app.models.account import ChartOfAccount
from app.models.expense import Expense, Income
from app.models.governance import AuditLog
from app.models.identity import Shop
from app.models.journal import JournalEntry
from app.modules.accounts.gl_service import ensure_default_accounts

EXPENSE_CATEGORIES = ("RENT", "UTILITIES", "FUEL", "MARKETING", "SALARIES", "OFFICE_SUPPLIES", "OTHER")
INCOME_CATEGORIES = ("INTEREST", "DIVIDENDS", "ASSET_SALE", "REFUNDS", "COMMISSION", "RENTAL_INCOME", "GRANTS_SUBSIDIES", "OTHER")
PAYMENT_CHANNELS = ("CASH", "MPESA", "BANK", "CARD", "CHEQUE", "OTHER")

OTHER_INCOME_ACCOUNT = {"code": "4100", "name": "Other Income", "account_type": "REVENUE"}


@dataclass(frozen=True)
class Kind:
    name: str  # "expense" | "income"
    model: type
    date_field: str
    attachment_field: str
    categories: tuple[str, ...]
    journal_source: str
    label: str


EXPENSE = Kind("expense", Expense, "expense_date", "receipt_url", EXPENSE_CATEGORIES, "expense", "Expense")
INCOME = Kind("income", Income, "income_date", "attachment_url", INCOME_CATEGORIES, "income", "Other income")


def serialize(kind: Kind, row: Any) -> dict:
    data = {
        "id": str(row.id),
        "shopId": str(row.shop_id),
        "description": row.description,
        "amount": str(row.amount),
        "currency": row.currency,
        "category": row.category,
        "date": getattr(row, kind.date_field).isoformat() if getattr(row, kind.date_field) else None,
        "paymentChannel": row.payment_channel,
        "paymentReference": row.payment_reference,
        "attachmentUrl": getattr(row, kind.attachment_field),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }
    # Field names the existing screens/reports already use.
    data[f"{kind.name}Date"] = data["date"]
    if kind is EXPENSE:
        data["receiptUrl"] = data["attachmentUrl"]
        data["isNonDeductible"] = bool(row.is_non_deductible)
    return data


def _snapshot(kind: Kind, row: Any) -> dict:
    snap = serialize(kind, row)
    snap.pop("shopId", None)
    return snap


async def list_entries(
    db: AsyncSession,
    kind: Kind,
    shop_id: uuid.UUID,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    category: Optional[str],
    search: Optional[str],
    limit: int,
    offset: int,
) -> dict:
    model = kind.model
    date_col = getattr(model, kind.date_field)
    conditions = [model.shop_id == shop_id]
    if date_from:
        conditions.append(date_col >= date_from)
    if date_to:
        conditions.append(date_col <= date_to)
    if category:
        conditions.append(model.category == category)
    if search:
        like = f"%{search.strip().lower()}%"
        conditions.append(
            or_(func.lower(model.description).like(like), func.lower(func.coalesce(model.payment_reference, "")).like(like))
        )

    total_count, total_amount = (
        await db.execute(select(func.count(model.id), func.coalesce(func.sum(model.amount), 0)).where(*conditions))
    ).one()
    by_category = (
        await db.execute(
            select(model.category, func.coalesce(func.sum(model.amount), 0))
            .where(*conditions)
            .group_by(model.category)
            .order_by(func.sum(model.amount).desc())
        )
    ).all()
    rows = (
        await db.execute(
            select(model).where(*conditions).order_by(date_col.desc(), model.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    return {
        "success": True,
        "items": [serialize(kind, r) for r in rows],
        "total": int(total_count),
        "limit": limit,
        "offset": offset,
        "summary": {
            "count": int(total_count),
            "totalAmount": str(Decimal(total_amount)),
            "byCategory": [{"category": c, "amount": str(Decimal(a))} for c, a in by_category],
        },
    }


async def _post_to_gl(db: AsyncSession, kind: Kind, row: Any, user_id: Optional[uuid.UUID]) -> None:
    """(Re)post the journal entry for this record when the workspace uses the general ledger."""
    from app.modules.accounts.posting import assert_period_open, remove_source_entries

    shop = await db.get(Shop, row.shop_id)
    if not shop or not shop.is_gl_enabled:
        return
    await remove_source_entries(db, row.shop_id, row.id)
    if Decimal(row.amount) <= 0:
        return
    await assert_period_open(db, row.shop_id, getattr(row, kind.date_field))

    accounts = await ensure_default_accounts(db, row.shop_id)
    if kind is INCOME and OTHER_INCOME_ACCOUNT["code"] not in accounts:
        acc = ChartOfAccount(shop_id=row.shop_id, is_system=True, **OTHER_INCOME_ACCOUNT)
        db.add(acc)
        await db.flush()
        accounts[acc.code] = acc

    if kind is EXPENSE:
        debit, credit = accounts["6000"], accounts["1200"]
    else:
        debit, credit = accounts["1200"], accounts[OTHER_INCOME_ACCOUNT["code"]]

    db.add(
        JournalEntry(
            shop_id=row.shop_id,
            entry_date=getattr(row, kind.date_field),
            description=f"{kind.label}: {row.description}"[:500],
            debit_account_id=debit.id,
            credit_account_id=credit.id,
            amount=row.amount,
            reference_number=row.payment_reference,
            source_type=kind.journal_source,
            source_id=row.id,
            created_by_id=user_id,
        )
    )


def _audit(kind: Kind, row: Any, action: str, user_id, before=None, after=None) -> AuditLog:
    return AuditLog(
        shop_id=row.shop_id,
        user_id=user_id,
        action=action,
        table_name=kind.model.__tablename__,
        record_id=row.id,
        record_label=f"{kind.label}: {row.description}"[:255],
        before_state=before,
        after_state=after,
    )


async def create_entry(db: AsyncSession, kind: Kind, shop_id: uuid.UUID, values: dict, user_id) -> Any:
    row = kind.model(shop_id=shop_id, **values)
    db.add(row)
    await db.flush()
    await _post_to_gl(db, kind, row, user_id)
    db.add(_audit(kind, row, "CREATE", user_id, after=_snapshot(kind, row)))
    await db.commit()
    return row


async def update_entry(db: AsyncSession, kind: Kind, row: Any, values: dict, user_id) -> Any:
    before = _snapshot(kind, row)
    for key, value in values.items():
        setattr(row, key, value)
    await db.flush()
    await _post_to_gl(db, kind, row, user_id)
    db.add(_audit(kind, row, "UPDATE", user_id, before=before, after=_snapshot(kind, row)))
    await db.commit()
    return row


async def delete_entry(db: AsyncSession, kind: Kind, row: Any, user_id) -> None:
    from app.modules.accounts.posting import gl_enabled, remove_source_entries

    db.add(_audit(kind, row, "DELETE", user_id, before=_snapshot(kind, row)))
    if await gl_enabled(db, row.shop_id):
        await remove_source_entries(db, row.shop_id, row.id)
    await db.delete(row)
    await db.commit()


def now() -> datetime:
    return utcnow()
