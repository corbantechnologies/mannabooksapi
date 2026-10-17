import logging
import uuid
from decimal import Decimal
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from app.models.document import Document
from app.models.payment import DocumentPayment
from app.modules.payments.schemas import (
    RecordPaymentRequest,
    MpesaC2BCallbackRequest,
    PaymentResponse,
    PaymentReceiptSummary,
)
from datetime import datetime
from app.core.datetime_utils import to_naive_utc
from app.core.datetime_utils import utcnow
from app.modules.documents import workflow
from app.modules.documents.workflow import WorkflowError

logger = logging.getLogger("mannabooks.payments")


async def record_payment(
    db: AsyncSession,
    shop_id: uuid.UUID,
    req: RecordPaymentRequest,
    user_id: Optional[uuid.UUID] = None,
) -> PaymentReceiptSummary:
    """Record an installment against an invoice and transition status atomically."""
    # 1. Fetch target document with row lock
    q_doc = (
        select(Document)
        .where(and_(Document.id == req.document_id, Document.shop_id == shop_id))
        .with_for_update()
    )
    res_doc = await db.execute(q_doc)
    doc = res_doc.scalar_one_or_none()

    if not doc:
        raise ValueError("Target document not found in this workspace")

    if doc.type not in workflow.SETTLED_TYPES:
        raise WorkflowError("Payments are recorded against invoices and debit notes.")
    if doc.status == "CANCELLED":
        raise WorkflowError("Cannot record payment against a cancelled document")
    if doc.status == "DRAFT":
        raise WorkflowError("Issue the invoice before recording payments against it.")

    # Guard against overpayment: customer credit balances are not supported yet.
    outstanding = (await workflow.figures(db, doc)).balance
    if outstanding <= 0:
        raise ValueError("This document is already fully paid")
    if req.amount > outstanding:
        raise ValueError(f"Payment of {req.amount:,.2f} exceeds the outstanding balance of {outstanding:,.2f}")

    # 2. Record installment payment
    payment = DocumentPayment(
        document_id=doc.id,
        shop_id=shop_id,
        amount=req.amount,
        payment_date=to_naive_utc(req.payment_date) or utcnow(),
        payment_channel=req.payment_channel,
        payment_reference=req.payment_reference.strip() if req.payment_reference else None,
        notes=req.notes,
        recorded_by_user_id=user_id,
    )
    db.add(payment)
    await db.flush()

    from app.modules.accounts.posting import sync_payment_gl
    await sync_payment_gl(db, shop_id, payment, doc)

    # 3. Status follows payments, credit notes and the due date
    await workflow.refresh_settlement(db, doc)
    fig = await workflow.figures(db, doc)
    total_paid = fig.paid

    if not doc.payment_channel:
        doc.payment_channel = req.payment_channel
    if not doc.payment_reference and req.payment_reference:
        doc.payment_reference = req.payment_reference.strip()

    await db.commit()
    await db.refresh(payment)

    remaining_balance = fig.balance

    return PaymentReceiptSummary(
        payment=PaymentResponse.model_validate(payment),
        document_id=doc.id,
        doc_number=doc.doc_number,
        grand_total=doc.grand_total,
        total_paid=total_paid,
        remaining_balance=remaining_balance,
        status=doc.status,
    )


async def get_document_payments(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID
) -> List[DocumentPayment]:
    q = (
        select(DocumentPayment)
        .where(and_(DocumentPayment.document_id == document_id, DocumentPayment.shop_id == shop_id))
        .order_by(desc(DocumentPayment.payment_date))
    )
    res = await db.execute(q)
    return list(res.scalars().all())


async def handle_mpesa_c2b_callback(
    db: AsyncSession,
    req: MpesaC2BCallbackRequest,
) -> Dict[str, Any]:
    """Idempotent handler for Safaricom Daraja C2B confirmation callbacks."""
    trans_id = req.TransID.strip()

    # 1. Idempotency Check: Don't process duplicate callbacks
    q_dup = select(DocumentPayment).where(DocumentPayment.payment_reference == trans_id)
    res_dup = await db.execute(q_dup)
    existing = res_dup.scalar_one_or_none()
    if existing:
        return {"ResultCode": 0, "ResultDesc": "Accepted Duplicate"}

    # 2. Resolve the receiving workspace from the paybill/till number, then match the
    #    BillRefNumber against that workspace's documents only. Document numbers are only
    #    unique per workspace, so a global match could credit the wrong tenant.
    from app.models.payment import PaymentMethod

    shortcode = (req.BusinessShortCode or "").strip()
    bill_ref = req.BillRefNumber.strip()
    if not shortcode or not bill_ref:
        return {"ResultCode": 0, "ResultDesc": "Accepted (Missing reference)"}

    shop_ids = (
        await db.execute(
            select(PaymentMethod.shop_id).where(PaymentMethod.details.ilike(f"%{shortcode}%")).distinct()
        )
    ).scalars().all()
    if not shop_ids:
        logger.warning("C2B %s: no workspace configured for shortcode %s", trans_id, shortcode)
        return {"ResultCode": 0, "ResultDesc": "Accepted (Unknown shortcode)"}

    docs = (
        await db.execute(
            select(Document)
            .where(Document.shop_id.in_(shop_ids), func.lower(Document.doc_number) == bill_ref.lower())
            .with_for_update()
        )
    ).scalars().all()

    if len(docs) != 1:
        # Unmatched or ambiguous: acknowledge so Safaricom stops retrying; reconcile manually.
        logger.warning("C2B %s: %d documents match BillRef %s for shortcode %s", trans_id, len(docs), bill_ref, shortcode)
        return {"ResultCode": 0, "ResultDesc": "Accepted (Unmatched BillRef)"}
    doc = docs[0]

    try:
        amount = Decimal(req.TransAmount)
    except Exception:
        return {"ResultCode": 0, "ResultDesc": "Accepted (Invalid amount)"}
    if amount <= 0:
        return {"ResultCode": 0, "ResultDesc": "Accepted (Invalid amount)"}
    payer_name = f"{req.FirstName or ''} {req.LastName or ''}".strip()
    notes = f"M-Pesa C2B from {req.MSISDN} ({payer_name})"

    payment = DocumentPayment(
        document_id=doc.id,
        shop_id=doc.shop_id,
        amount=amount,
        payment_date=utcnow(),
        payment_channel="MPESA",
        payment_reference=trans_id,
        notes=notes,
    )
    db.add(payment)
    await db.flush()

    from app.modules.accounts.posting import sync_payment_gl

    if doc.type in workflow.SETTLED_TYPES:
        if doc.status == "DRAFT":
            # Money arrived against a draft: the invoice is effectively issued.
            doc.status = "ISSUED"
            await db.flush()
            await workflow.sync_document_stock(db, doc)
            from app.modules.documents.service import _sync_gl

            await _sync_gl(db, doc.shop_id, doc)
        await sync_payment_gl(db, doc.shop_id, payment, doc)
        await workflow.refresh_settlement(db, doc)
    else:
        logger.warning("C2B %s matched %s %s, which does not take payments", trans_id, doc.type, doc.doc_number)

    doc.payment_channel = "MPESA"
    doc.payment_reference = trans_id

    await db.commit()
    return {"ResultCode": 0, "ResultDesc": "Success"}


async def delete_payment(
    db: AsyncSession, shop_id: uuid.UUID, payment_id: uuid.UUID
) -> Dict[str, Any]:
    """Delete an installment and recalibrate parent document status."""
    q_pay = select(DocumentPayment).where(
        and_(DocumentPayment.id == payment_id, DocumentPayment.shop_id == shop_id)
    )
    res_pay = await db.execute(q_pay)
    payment = res_pay.scalar_one_or_none()
    if not payment:
        raise ValueError("Payment installment record not found")

    doc_id = payment.document_id
    from app.modules.accounts.posting import remove_payment_gl
    await remove_payment_gl(db, shop_id, payment.id)
    await db.delete(payment)
    await db.flush()

    q_doc = select(Document).where(Document.id == doc_id).with_for_update()
    res_doc = await db.execute(q_doc)
    doc = res_doc.scalar_one_or_none()

    new_status = "ISSUED"
    remaining_balance = Decimal("0.00")
    if doc:
        if doc.status == "PAID" and doc.type in workflow.SETTLED_TYPES:
            doc.status = "ISSUED"  # recompute from the remaining payments
            await db.flush()
        await workflow.refresh_settlement(db, doc)
        new_status = doc.status
        remaining_balance = (await workflow.figures(db, doc)).balance

    await db.commit()
    return {
        "success": True,
        "document_id": doc_id,
        "new_status": new_status,
        "remaining_balance": remaining_balance,
    }
