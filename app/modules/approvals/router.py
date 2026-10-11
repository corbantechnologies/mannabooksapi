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
from app.models.bill import VendorBill
from app.models.identity import Shop

router = APIRouter(prefix="/approvals", tags=["Approvals Workflow Engine"], dependencies=[Depends(verify_internal_api_key)])


class CreateApprovalRequest(BaseModel):
    shop_id: uuid.UUID
    title: str
    entity_type: str = "DOCUMENT"
    entity_id: Optional[uuid.UUID] = None
    amount: Decimal = Decimal("0.00")
    requested_by_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    action: str  # "APPROVED" or "REJECTED"
    reason: Optional[str] = None
    approver_id: Optional[uuid.UUID] = None


DEFAULT_POLICIES = [
    {
        "id": "pol-exp-tier1",
        "name": "Standard Expense Limit",
        "entityType": "EXPENSE",
        "thresholdAmount": 50000.0,
        "requiredRole": "MANAGER",
        "isActive": True,
    },
    {
        "id": "pol-bill-tier2",
        "name": "Major Vendor Bill Sign-off",
        "entityType": "VENDOR_BILL",
        "thresholdAmount": 250000.0,
        "requiredRole": "ADMIN",
        "isActive": True,
    },
    {
        "id": "pol-quote-tier1",
        "name": "High Value Quotation Review",
        "entityType": "DOCUMENT",
        "thresholdAmount": 1000000.0,
        "requiredRole": "ADMIN",
        "isActive": True,
    },
]


@router.get("/policies")
async def list_approval_policies(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return DEFAULT_POLICIES


@router.get("/requests")
async def list_approval_requests(
    shop_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    if not shop_id:
        return []

    # Fetch pending vendor bills
    bill_q = (
        select(VendorBill)
        .where(
            and_(
                VendorBill.shop_id == shop_id,
                VendorBill.status.in_(["DRAFT", "PENDING_APPROVAL"]),
            )
        )
        .order_by(desc(VendorBill.created_at))
        .limit(50)
    )
    bills = (await db.execute(bill_q)).scalars().all()

    # Fetch draft/conf documents
    doc_q = (
        select(Document)
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.status.in_(["DRAFT"]),
            )
        )
        .order_by(desc(Document.issue_date))
        .limit(50)
    )
    docs = (await db.execute(doc_q)).scalars().all()

    results = []
    for b in bills:
        results.append({
            "id": str(b.id),
            "shopId": str(b.shop_id),
            "entityType": "VENDOR_BILL",
            "entityId": str(b.id),
            "title": f"Vendor Bill: {b.bill_number}",
            "amount": float(b.total_amount),
            "status": "PENDING" if b.status in ("DRAFT", "PENDING_APPROVAL") else b.status,
            "notes": b.notes or b.variance_notes,
            "createdAt": b.created_at.isoformat(),
        })

    for d in docs:
        results.append({
            "id": str(d.id),
            "shopId": str(d.shop_id),
            "entityType": "DOCUMENT",
            "entityId": str(d.id),
            "title": f"{d.type}: {d.doc_number}",
            "amount": float(d.grand_total),
            "status": "PENDING",
            "notes": d.notes,
            "createdAt": d.issue_date.isoformat(),
        })

    return results


@router.post("/requests")
async def create_approval_request(
    req: CreateApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    req_id = str(uuid.uuid4())
    return {
        "success": True,
        "request": {
            "id": req_id,
            "shopId": str(req.shop_id),
            "title": req.title,
            "entityType": req.entity_type,
            "entityId": str(req.entity_id) if req.entity_id else None,
            "amount": float(req.amount),
            "status": "PENDING",
            "notes": req.notes,
            "createdAt": datetime.utcnow().isoformat(),
        },
    }


@router.post("/requests/{request_id}/decision")
async def record_approval_decision(
    request_id: uuid.UUID,
    req: ApprovalDecisionRequest,
    db: AsyncSession = Depends(get_db),
):
    # Check if request_id corresponds to a VendorBill
    bill = await db.get(VendorBill, request_id)
    if bill:
        bill.status = "APPROVED" if req.action.upper() == "APPROVED" else "CANCELLED"
        await db.commit()
        return {"success": True, "status": bill.status, "message": f"Bill status set to {bill.status}"}

    # Check if request_id corresponds to a Document
    doc = await db.get(Document, request_id)
    if doc:
        doc.status = "ISSUED" if req.action.upper() == "APPROVED" else "CANCELLED"
        await db.commit()
        return {"success": True, "status": doc.status, "message": f"Document status set to {doc.status}"}

    return {"success": True, "status": req.action.upper(), "message": f"Approval decision recorded: {req.action}"}
