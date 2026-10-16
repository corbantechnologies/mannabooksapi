# app/modules/accounts/posting.py
"""
Single entry point for keeping the general ledger in step with business records.

Model: journal entries are balanced debit/credit pairs. Entries generated from a record (document,
payment, expense, payroll run, ...) carry `source_id = record.id`, so "sync" means: remove the record's
derived entries and post them again from its current state. That makes issuing, editing, cancelling
and deleting records all consistent, and lets an integrity check rebuild missing postings.

Period locks: nothing may be posted to, or removed from, a date that falls in a CLOSED accounting
period or a closed fiscal year. Reopen the period (audited) to make corrections.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import AccountingPeriod, ChartOfAccount, FiscalYear
from app.models.document import Document
from app.models.identity import Shop
from app.models.journal import JournalEntry
from app.models.payment import DocumentPayment

ZERO = Decimal("0.00")


class PeriodLockedError(ValueError):
    """Raised when a posting would touch a closed accounting period or fiscal year."""


def _as_date(when: datetime | date) -> date:
    return when.date() if isinstance(when, datetime) else when


async def locked_reason(db: AsyncSession, shop_id: uuid.UUID, when: datetime | date) -> Optional[str]:
    d = _as_date(when)
    period = (
        await db.execute(
            select(AccountingPeriod.period_name).where(
                AccountingPeriod.shop_id == shop_id,
                AccountingPeriod.status == "CLOSED",
                AccountingPeriod.start_date <= d,
                AccountingPeriod.end_date >= d,
            )
        )
    ).scalars().first()
    if period:
        return f"the accounting period '{period}' is closed"
    fy = (
        await db.execute(
            select(FiscalYear.label).where(
                FiscalYear.shop_id == shop_id,
                FiscalYear.is_closed == True,  # noqa: E712
                FiscalYear.start_date <= d,
                FiscalYear.end_date >= d,
            )
        )
    ).scalars().first()
    if fy:
        return f"the fiscal year '{fy}' is closed"
    return None


async def assert_period_open(db: AsyncSession, shop_id: uuid.UUID, when: datetime | date) -> None:
    reason = await locked_reason(db, shop_id, when)
    if reason:
        raise PeriodLockedError(
            f"Cannot post to {_as_date(when).isoformat()}: {reason}. Reopen it first or use a date in an open period."
        )


async def gl_enabled(db: AsyncSession, shop_id: uuid.UUID) -> bool:
    shop = await db.get(Shop, shop_id)
    return bool(shop and shop.is_gl_enabled)


async def remove_source_entries(db: AsyncSession, shop_id: uuid.UUID, source_id: uuid.UUID) -> None:
    """Delete a record's derived entries, refusing if any of them sit in a closed period."""
    dates = (
        await db.execute(
            select(JournalEntry.entry_date).where(JournalEntry.shop_id == shop_id, JournalEntry.source_id == source_id)
        )
    ).scalars().all()
    for d in {_as_date(x) for x in dates}:
        await assert_period_open(db, shop_id, d)
    await db.execute(delete(JournalEntry).where(JournalEntry.shop_id == shop_id, JournalEntry.source_id == source_id))


async def account_map(db: AsyncSession, shop_id: uuid.UUID) -> dict[str, ChartOfAccount]:
    from app.modules.accounts.gl_service import ensure_default_accounts

    return await ensure_default_accounts(db, shop_id)


def pair(
    shop_id: uuid.UUID,
    when: datetime,
    description: str,
    debit: ChartOfAccount,
    credit: ChartOfAccount,
    amount: Decimal,
    source_type: str,
    source_id: uuid.UUID,
    reference: Optional[str] = None,
    created_by_id: Optional[uuid.UUID] = None,
) -> Optional[JournalEntry]:
    if amount is None or Decimal(amount) <= 0:
        return None
    return JournalEntry(
        shop_id=shop_id,
        entry_date=when,
        description=description[:500],
        debit_account_id=debit.id,
        credit_account_id=credit.id,
        amount=Decimal(amount),
        reference_number=reference,
        source_type=source_type,
        source_id=source_id,
        created_by_id=created_by_id,
    )


async def add_entries(db: AsyncSession, shop_id: uuid.UUID, entries: Iterable[Optional[JournalEntry]]) -> list[JournalEntry]:
    out = [e for e in entries if e is not None]
    for e in out:
        await assert_period_open(db, shop_id, e.entry_date)
        db.add(e)
    return out


# ---------------------------------------------------------------------------
# Documents (sales side)
# ---------------------------------------------------------------------------
POSTED_TYPES = ("INVOICE", "RECEIPT", "CREDIT_NOTE")


async def _is_payment_receipt(db: AsyncSession, doc: Document) -> bool:
    """A receipt issued against an invoice acknowledges a payment; revenue was booked by the invoice."""
    if not doc.parent_document_id:
        return False
    parent_type = (await db.execute(select(Document.type).where(Document.id == doc.parent_document_id))).scalar_one_or_none()
    return str(parent_type or "").upper() == "INVOICE"


async def sync_document_gl(db: AsyncSession, shop_id: uuid.UUID, doc: Document) -> list[JournalEntry]:
    """
    Make the ledger match the document's current state.
      INVOICE      Dr Accounts Receivable / Cr Sales Revenue (+ Cr Output VAT); if created already PAID
                   with no recorded payments, also Dr Cash / Cr Accounts Receivable.
      RECEIPT      Dr Cash / Cr Sales Revenue (+ Cr Output VAT)  (cash sale; not for receipts of invoices)
      CREDIT_NOTE  Dr Sales Revenue (+ Dr Output VAT) / Cr Accounts Receivable (Cash if refunded/PAID)
    Drafts, cancelled and deleted documents have no entries.
    """
    if not await gl_enabled(db, shop_id):
        return []
    await remove_source_entries(db, shop_id, doc.id)

    doc_type = str(doc.type).upper()
    status = str(doc.status).upper()
    if doc_type not in POSTED_TYPES or status in ("DRAFT", "CANCELLED"):
        return []
    if doc_type == "RECEIPT" and await _is_payment_receipt(db, doc):
        return []

    acc = await account_map(db, shop_id)
    cash, ar, vat, revenue = acc["1200"], acc["1300"], acc["2200"], acc["4000"]
    net = Decimal(str(doc.sub_total or 0))
    tax = Decimal(str(doc.tax_amount or 0))
    gross = Decimal(str(doc.grand_total or 0))
    when = doc.issue_date
    ref = doc.doc_number

    def mk(desc, d, c, amt):
        return pair(shop_id, when, f"{desc} {doc.doc_number}", d, c, amt, "document", doc.id, ref)

    entries: list[Optional[JournalEntry]] = []
    if doc_type == "INVOICE":
        entries += [mk("Invoice", ar, revenue, net), mk("Invoice VAT", ar, vat, tax)]
        if status == "PAID":
            has_payments = (
                await db.execute(select(func.count(DocumentPayment.id)).where(DocumentPayment.document_id == doc.id))
            ).scalar()
            if not has_payments:
                entries.append(mk("Invoice settled", cash, ar, gross))
    elif doc_type == "RECEIPT":
        entries += [mk("Cash sale", cash, revenue, net), mk("Cash sale VAT", cash, vat, tax)]
    elif doc_type == "CREDIT_NOTE":
        target = cash if status == "PAID" else ar
        entries += [mk("Credit note", revenue, target, net), mk("Credit note VAT", vat, target, tax)]

    return await add_entries(db, shop_id, entries)


async def remove_document_gl(db: AsyncSession, shop_id: uuid.UUID, doc_id: uuid.UUID) -> None:
    if await gl_enabled(db, shop_id):
        await remove_source_entries(db, shop_id, doc_id)


# ---------------------------------------------------------------------------
# Customer payments against invoices
# ---------------------------------------------------------------------------
async def sync_payment_gl(db: AsyncSession, shop_id: uuid.UUID, payment: DocumentPayment, doc: Document) -> list[JournalEntry]:
    """Dr Cash / Cr Accounts Receivable for a payment recorded against an invoice."""
    if not await gl_enabled(db, shop_id):
        return []
    await remove_source_entries(db, shop_id, payment.id)
    if str(doc.type).upper() != "INVOICE":
        return []
    acc = await account_map(db, shop_id)
    entry = pair(
        shop_id,
        payment.payment_date,
        f"Payment received {doc.doc_number}" + (f" ({payment.payment_reference})" if payment.payment_reference else ""),
        acc["1200"],
        acc["1300"],
        Decimal(str(payment.amount)),
        "payment",
        payment.id,
        payment.payment_reference or doc.doc_number,
        payment.recorded_by_user_id,
    )
    return await add_entries(db, shop_id, [entry])


async def remove_payment_gl(db: AsyncSession, shop_id: uuid.UUID, payment_id: uuid.UUID) -> None:
    if await gl_enabled(db, shop_id):
        await remove_source_entries(db, shop_id, payment_id)


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------
async def integrity_report(db: AsyncSession, shop_id: uuid.UUID, limit: int = 100, full: bool = False) -> dict:
    """Checks that the ledger matches the records it is derived from."""
    from app.models.expense import Expense, Income

    issues: dict[str, list] = {}

    def add(kind: str, item: dict) -> None:
        issues.setdefault(kind, []).append(item)

    # 1. Entries pointing at another workspace's accounts, non-positive amounts, self-postings.
    own_accounts = select(ChartOfAccount.id).where(ChartOfAccount.shop_id == shop_id)
    bad = (
        await db.execute(
            select(JournalEntry.id, JournalEntry.description, JournalEntry.amount).where(
                JournalEntry.shop_id == shop_id,
                or_(
                    JournalEntry.debit_account_id.not_in(own_accounts),
                    JournalEntry.credit_account_id.not_in(own_accounts),
                    JournalEntry.amount <= 0,
                    JournalEntry.debit_account_id == JournalEntry.credit_account_id,
                ),
            ).limit(limit)
        )
    ).all()
    for jid, desc, amt in bad:
        add("invalid_entries", {"entryId": str(jid), "description": desc, "amount": str(amt)})

    gl_on = await gl_enabled(db, shop_id)
    if gl_on:
        posted_sources = select(JournalEntry.source_id).where(JournalEntry.shop_id == shop_id, JournalEntry.source_id.is_not(None))

        # 2. Issued sales documents with no entries.
        rows = (
            await db.execute(
                select(Document.id, Document.doc_number, Document.type, Document.status).where(
                    Document.shop_id == shop_id,
                    Document.type.in_(POSTED_TYPES),
                    Document.status.not_in(["DRAFT", "CANCELLED"]),
                    Document.grand_total > 0,
                    Document.id.not_in(posted_sources),
                ).limit(limit)
            )
        ).all()
        for did, num, typ, st in rows:
            add("documents_not_posted", {"documentId": str(did), "docNumber": num, "type": typ, "status": st})

        # 3. Draft/cancelled documents that still have entries.
        rows = (
            await db.execute(
                select(Document.id, Document.doc_number, Document.status).where(
                    Document.shop_id == shop_id,
                    Document.status.in_(["DRAFT", "CANCELLED"]),
                    Document.id.in_(posted_sources),
                ).limit(limit)
            )
        ).all()
        for did, num, st in rows:
            add("cancelled_or_draft_still_posted", {"documentId": str(did), "docNumber": num, "status": st})

        # 4. Invoice payments with no entry.
        rows = (
            await db.execute(
                select(DocumentPayment.id, Document.doc_number, DocumentPayment.amount)
                .join(Document, Document.id == DocumentPayment.document_id)
                .where(
                    DocumentPayment.shop_id == shop_id,
                    Document.type == "INVOICE",
                    DocumentPayment.id.not_in(posted_sources),
                ).limit(limit)
            )
        ).all()
        for pid, num, amt in rows:
            add("payments_not_posted", {"paymentId": str(pid), "docNumber": num, "amount": str(amt)})

        # 5. Expenses / other income with no entry.
        for model, label in ((Expense, "expenses_not_posted"), (Income, "incomes_not_posted")):
            rows = (
                await db.execute(
                    select(model.id, model.description, model.amount).where(
                        model.shop_id == shop_id, model.amount > 0, model.id.not_in(posted_sources)
                    ).limit(limit)
                )
            ).all()
            for rid, desc, amt in rows:
                add(label, {"id": str(rid), "description": desc, "amount": str(amt)})

    totals = (
        await db.execute(
            select(func.count(JournalEntry.id), func.coalesce(func.sum(JournalEntry.amount), 0)).where(JournalEntry.shop_id == shop_id)
        )
    ).one()
    return {
        "success": True,
        "glEnabled": gl_on,
        "healthy": not issues,
        "entryCount": int(totals[0]),
        "postedValue": str(Decimal(totals[1])),
        "issues": {k: {"count": len(v), "samples": v if full else v[:20]} for k, v in issues.items()},
    }


async def repair_ledger(db: AsyncSession, shop_id: uuid.UUID) -> dict:
    """Re-sync every record that an integrity check flags. Records in closed periods are skipped."""
    from app.models.expense import Expense, Income
    from app.modules.cashbook.service import EXPENSE, INCOME, _post_to_gl

    report = await integrity_report(db, shop_id, limit=20000, full=True)
    fixed, skipped = 0, []

    async def attempt(label: str, coro_factory):
        nonlocal fixed
        try:
            async with db.begin_nested():
                await coro_factory()
            fixed += 1
        except PeriodLockedError as exc:
            skipped.append({"item": label, "reason": str(exc)})

    for key in ("documents_not_posted", "cancelled_or_draft_still_posted"):
        for item in report["issues"].get(key, {}).get("samples", []):
            doc = await db.get(Document, uuid.UUID(item["documentId"]))
            if doc:
                await attempt(item["docNumber"], lambda d=doc: sync_document_gl(db, shop_id, d))
    for item in report["issues"].get("payments_not_posted", {}).get("samples", []):
        pay = await db.get(DocumentPayment, uuid.UUID(item["paymentId"]))
        if pay:
            doc = await db.get(Document, pay.document_id)
            await attempt(f"payment {item['docNumber']}", lambda p=pay, d=doc: sync_payment_gl(db, shop_id, p, d))
    for key, model, kind in (("expenses_not_posted", Expense, EXPENSE), ("incomes_not_posted", Income, INCOME)):
        for item in report["issues"].get(key, {}).get("samples", []):
            row = await db.get(model, uuid.UUID(item["id"]))
            if row:
                await attempt(item["description"], lambda r=row, k=kind: _post_to_gl(db, k, r, None))

    await db.commit()
    return {"success": True, "fixed": fixed, "skipped": skipped, "after": await integrity_report(db, shop_id)}
