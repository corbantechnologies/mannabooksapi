import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload

from app.models.document import Document, DocumentItem
from app.modules.documents.service import generate_doc_number
from app.workers.etims_worker import process_queued_transmissions
from app.modules.crons.schemas import (
    OverdueSweepResponse,
    RecurringScheduleProcessedItem,
    ProcessRecurringResponse,
    QuoteExpiryResponse,
    EtimsRetryCronResponse,
)
from app.core.datetime_utils import to_naive_utc
from app.core.datetime_utils import utcnow


# ================================================================
# 1. OVERDUE INVOICE SWEEP
# ================================================================
async def run_overdue_sweep(
    db: AsyncSession, shop_id: Optional[uuid.UUID] = None
) -> OverdueSweepResponse:
    now = utcnow()
    conditions = [
        Document.type == "INVOICE",
        Document.status.in_(["ISSUED", "PARTIALLY_PAID"]),
        Document.due_date.isnot(None),
        Document.due_date < now,
    ]
    if shop_id:
        conditions.append(Document.shop_id == shop_id)

    q = select(Document).where(and_(*conditions))
    res = await db.execute(q)
    candidates = list(res.scalars().all())

    aging_buckets = {
        "1-30_days": 0,
        "31-60_days": 0,
        "61-90_days": 0,
        "90+_days": 0,
    }
    total_amount = Decimal("0.00")
    marked_count = 0

    for doc in candidates:
        d_due = to_naive_utc(doc.due_date)
        if not d_due:
            continue
        days = (now - d_due).days
        if days <= 30:
            aging_buckets["1-30_days"] += 1
        elif days <= 60:
            aging_buckets["31-60_days"] += 1
        elif days <= 90:
            aging_buckets["61-90_days"] += 1
        else:
            aging_buckets["90+_days"] += 1

        doc.status = "OVERDUE"
        total_amount += doc.grand_total
        marked_count += 1

    await db.commit()

    return OverdueSweepResponse(
        total_evaluated=len(candidates),
        marked_overdue=marked_count,
        aging_buckets=aging_buckets,
        total_overdue_amount=total_amount,
        errors=[],
    )


# ================================================================
# 2. RECURRING INVOICE ENGINE
# ================================================================
async def run_process_recurring(
    db: AsyncSession, shop_id: Optional[uuid.UUID] = None
) -> ProcessRecurringResponse:
    now = utcnow()
    conditions = [
        Document.is_recurring == True,
        Document.next_recurring_date.isnot(None),
        Document.next_recurring_date <= now,
    ]
    if shop_id:
        conditions.append(Document.shop_id == shop_id)

    q = (
        select(Document)
        .where(and_(*conditions))
        .options(selectinload(Document.items))
    )
    res = await db.execute(q)
    recurring_parents = list(res.scalars().all())

    processed_items: List[RecurringScheduleProcessedItem] = []
    errors: List[str] = []

    for parent in recurring_parents:
        try:
            # 1. Generate sequential document number
            new_doc_number = await generate_doc_number(db, parent.shop_id, parent.type)

            # 2. Create Clone with SAFE default status 'DRAFT' (avoids premature KRA tax liability)
            cloned_doc = Document(
                id=uuid.uuid4(),
                shop_id=parent.shop_id,
                client_id=parent.client_id,
                supplier_id=parent.supplier_id,
                type=parent.type,
                doc_number=new_doc_number,
                status="DRAFT",  # Safe pending review status
                requires_etims=parent.requires_etims,
                notes=parent.notes,
                terms_and_conditions=parent.terms_and_conditions,
                currency=parent.currency,
                exchange_rate=parent.exchange_rate,
                sub_total=parent.sub_total,
                tax_amount=parent.tax_amount,
                grand_total=parent.grand_total,
                issue_date=now,
                due_date=now + timedelta(days=30),
                is_recurring=False,  # Children are not recurring templates
            )
            db.add(cloned_doc)
            await db.flush()

            # 3. Clone line items
            for it in parent.items:
                cloned_item = DocumentItem(
                    id=uuid.uuid4(),
                    document_id=cloned_doc.id,
                    product_id=it.product_id,
                    description=it.description,
                    notes=it.notes,
                    quantity=it.quantity,
                    unit_price=it.unit_price,
                    tax_type=it.tax_type,
                    tax_amount=it.tax_amount,
                    item_total=it.item_total,
                )
                db.add(cloned_item)

            # 4. Advance next_recurring_date on parent
            interval = (parent.recurring_interval or "MONTHLY").upper()
            curr_target = to_naive_utc(parent.next_recurring_date) or now
            if interval == "WEEKLY":
                next_date = curr_target + timedelta(days=7)
            elif interval == "MONTHLY":
                next_date = curr_target + timedelta(days=30)
            elif interval == "QUARTERLY":
                next_date = curr_target + timedelta(days=90)
            elif interval == "YEARLY":
                next_date = curr_target + timedelta(days=365)
            else:
                next_date = curr_target + timedelta(days=30)

            parent.next_recurring_date = to_naive_utc(next_date)

            processed_items.append(
                RecurringScheduleProcessedItem(
                    original_document_id=parent.id,
                    new_document_id=cloned_doc.id,
                    new_doc_number=new_doc_number,
                    status=cloned_doc.status,
                    next_recurring_date=next_date,
                )
            )
        except Exception as e:
            errors.append(f"Failed recurring doc {parent.id}: {str(e)}")

    await db.commit()

    return ProcessRecurringResponse(
        total_evaluated=len(recurring_parents),
        created_invoices=len(processed_items),
        advanced_schedules=len(processed_items),
        items=processed_items,
        errors=errors,
    )


# ================================================================
# 3. QUOTATION EXPIRY SWEEP
# ================================================================
async def run_quote_expiry_sweep(
    db: AsyncSession, shop_id: Optional[uuid.UUID] = None
) -> QuoteExpiryResponse:
    now = utcnow()
    conditions = [
        Document.type == "QUOTATION",
        Document.status == "ISSUED",
        Document.due_date.isnot(None),
        Document.due_date < now,
    ]
    if shop_id:
        conditions.append(Document.shop_id == shop_id)

    q = select(Document).where(and_(*conditions))
    res = await db.execute(q)
    expired_quotes = list(res.scalars().all())

    for quote in expired_quotes:
        quote.status = "CANCELLED"

    await db.commit()

    return QuoteExpiryResponse(
        total_evaluated=len(expired_quotes),
        expired_quotes=len(expired_quotes),
        errors=[],
    )


# ================================================================
# 4. ETIMS RETRY DAEMON TRIGGER
# ================================================================
async def run_etims_retry_daemon(
    db: AsyncSession, limit: int = 20
) -> EtimsRetryCronResponse:
    summary = await process_queued_transmissions(db, batch_size=limit)
    return EtimsRetryCronResponse(
        batches_processed=1 if summary.get("processed", 0) > 0 else 0,
        total_transmissions_evaluated=summary.get("processed", 0),
        succeeded=summary.get("fiscalized", 0),
        failed=summary.get("failed", 0),
        errors=[],
    )
