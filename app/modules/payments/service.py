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

    if doc.status == "CANCELLED":
        raise ValueError("Cannot record payment against a cancelled document")

    # 2. Record installment payment
    payment = DocumentPayment(
        document_id=doc.id,
        shop_id=shop_id,
        amount=req.amount,
        payment_date=req.payment_date or datetime.utcnow(),
        payment_channel=req.payment_channel,
        payment_reference=req.payment_reference.strip() if req.payment_reference else None,
        notes=req.notes,
        recorded_by_user_id=user_id,
    )
    db.add(payment)
    await db.flush()

    # 3. Calculate total historical payments recorded for this document
    q_sum = select(func.coalesce(func.sum(DocumentPayment.amount), Decimal("0.00"))).where(
        DocumentPayment.document_id == doc.id
    )
    res_sum = await db.execute(q_sum)
    total_paid: Decimal = res_sum.scalar() or Decimal("0.00")

    # 4. Transition document status
    if total_paid >= doc.grand_total:
        doc.status = "PAID"
    else:
        doc.status = "PARTIALLY_PAID"

    if not doc.payment_channel:
        doc.payment_channel = req.payment_channel
    if not doc.payment_reference and req.payment_reference:
        doc.payment_reference = req.payment_reference.strip()

    await db.commit()
    await db.refresh(payment)

    remaining_balance = max(Decimal("0.00"), doc.grand_total - total_paid)

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

    # 2. Match BillRefNumber against Document doc_number
    bill_ref = req.BillRefNumber.strip()
    q_doc = select(Document).where(Document.doc_number.ilike(bill_ref)).with_for_update()
    res_doc = await db.execute(q_doc)
    doc = res_doc.scalar_one_or_none()

    if not doc:
        # Acknowledge Safaricom to prevent callback retries even if reference is unmatched
        return {"ResultCode": 0, "ResultDesc": "Accepted (Unmatched BillRef)"}

    amount = Decimal(req.TransAmount)
    payer_name = f"{req.FirstName or ''} {req.LastName or ''}".strip()
    notes = f"M-Pesa C2B from {req.MSISDN} ({payer_name})"

    payment = DocumentPayment(
        document_id=doc.id,
        shop_id=doc.shop_id,
        amount=amount,
        payment_date=datetime.utcnow(),
        payment_channel="MPESA",
        payment_reference=trans_id,
        notes=notes,
    )
    db.add(payment)
    await db.flush()

    # Check cumulative totals
    q_sum = select(func.coalesce(func.sum(DocumentPayment.amount), Decimal("0.00"))).where(
        DocumentPayment.document_id == doc.id
    )
    res_sum = await db.execute(q_sum)
    total_paid: Decimal = res_sum.scalar() or Decimal("0.00")

    if total_paid >= doc.grand_total:
        doc.status = "PAID"
    else:
        doc.status = "PARTIALLY_PAID"

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
    await db.delete(payment)
    await db.flush()

    q_doc = select(Document).where(Document.id == doc_id).with_for_update()
    res_doc = await db.execute(q_doc)
    doc = res_doc.scalar_one_or_none()

    new_status = "ISSUED"
    remaining_balance = Decimal("0.00")
    if doc:
        q_sum = select(func.coalesce(func.sum(DocumentPayment.amount), Decimal("0.00"))).where(
            DocumentPayment.document_id == doc.id
        )
        res_sum = await db.execute(q_sum)
        total_paid: Decimal = res_sum.scalar() or Decimal("0.00")

        if total_paid >= doc.grand_total:
            doc.status = "PAID"
        elif total_paid > Decimal("0.00"):
            doc.status = "PARTIALLY_PAID"
        elif doc.due_date and doc.due_date < datetime.utcnow():
            doc.status = "OVERDUE"
        else:
            doc.status = "ISSUED"

        new_status = doc.status
        remaining_balance = max(Decimal("0.00"), doc.grand_total - total_paid)

    await db.commit()
    return {
        "success": True,
        "document_id": doc_id,
        "new_status": new_status,
        "remaining_balance": remaining_balance,
    }
