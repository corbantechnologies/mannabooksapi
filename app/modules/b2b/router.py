import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.document import Document
from app.models.expense import Expense
from app.core.datetime_utils import to_naive_utc

router = APIRouter(prefix="/b2b", tags=["B2B Network & e-Invoicing Inbox"], dependencies=[Depends(verify_internal_api_key)])


@router.get("/inbox")
async def get_b2b_inbox(
    shop_id: Optional[uuid.UUID] = Query(None),
    shop_slug: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if not shop_id and not shop_slug:
        return {"documents": [], "totalCount": 0}

    # Resolve shop_id if shop_slug passed
    sid = shop_id
    if not sid and shop_slug:
        from app.models.identity import Shop
        q = select(Shop.id).where(Shop.slug == shop_slug)
        sid = (await db.execute(q)).scalar_one_or_none()
    
    if not sid:
        return {"documents": [], "totalCount": 0}

    # Invoices/receipts directed to or associated with this workspace
    q = (
        select(Document)
        .where(Document.shop_id == sid)
        .order_by(desc(Document.issue_date))
        .limit(50)
    )
    docs = (await db.execute(q)).scalars().all()

    items = [
        {
            "id": str(d.id),
            "docNumber": d.doc_number,
            "type": d.type,
            "senderName": "MannaBooks Verified Network",
            "grandTotal": float(d.grand_total),
            "currency": d.currency or "KES",
            "issueDate": d.issue_date.isoformat(),
            "status": d.status,
            "isRead": d.is_read_by_recipient,
            "notes": d.notes,
        }
        for d in docs
    ]
    return {"documents": items, "totalCount": len(items)}


@router.patch("/inbox/{doc_id}/read")
async def mark_b2b_document_read(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="B2B Document not found")
    doc.is_read_by_recipient = True
    await db.commit()
    return {"success": True, "message": "Document marked as read"}


@router.post("/inbox/{doc_id}/import-expense")
async def import_b2b_doc_as_expense(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="B2B Document not found")

    exp = Expense(
        shop_id=doc.shop_id,
        description=f"Auto-imported from B2B Invoice {doc.doc_number}",
        amount=doc.grand_total,
        currency=doc.currency or "KES",
        category="OTHER",
        expense_date=to_naive_utc(doc.issue_date) or datetime.utcnow(),
        payment_channel=doc.payment_channel,
        payment_reference=doc.payment_reference or doc.doc_number,
        is_non_deductible=False,
    )
    db.add(exp)
    await db.commit()
    await db.refresh(exp)
    return {"success": True, "expenseId": str(exp.id), "message": "Imported as expense successfully"}
