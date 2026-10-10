import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.documents.schemas import (
    DocumentCreateRequest,
    CreditNoteCreateRequest,
    DocumentResponse,
    DocumentStatusUpdateRequest,
)
from app.modules.documents.service import (
    list_documents,
    get_document_by_id,
    create_document,
    issue_credit_note,
    update_document_status,
)

router = APIRouter(prefix="/documents", tags=["Documents & Credit Notes"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[DocumentResponse])
async def list_documents_endpoint(
    shop_id: uuid.UUID = Query(...),
    doc_type: Optional[str] = Query(None),
    doc_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await list_documents(db, shop_id=shop_id, doc_type=doc_type, doc_status=doc_status, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document_endpoint(
    document_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await get_document_by_id(db, shop_id=shop_id, document_id=document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document_endpoint(
    req: DocumentCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_document(db, shop_id=shop_id, req=req)


@router.post("/{document_id}/credit-note", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def issue_credit_note_endpoint(
    document_id: uuid.UUID,
    req: CreditNoteCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await issue_credit_note(db, shop_id=shop_id, parent_doc_id=document_id, req=req)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.patch("/{document_id}/status", response_model=DocumentResponse)
async def update_status_endpoint(
    document_id: uuid.UUID,
    req: DocumentStatusUpdateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await update_document_status(db, shop_id=shop_id, document_id=document_id, new_status=req.status)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.get("/public/{token}")
async def get_public_document_endpoint(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    import uuid
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.document import Document, DocumentToken
    from app.models.identity import Shop
    from app.models.payment import PaymentMethod

    target_doc_id = None

    q_tok = select(DocumentToken).where(DocumentToken.token == token)
    tok_res = (await db.execute(q_tok)).scalar_one_or_none()
    if tok_res:
        target_doc_id = tok_res.document_id

    if not target_doc_id:
        try:
            parsed_uuid = uuid.UUID(token)
            q_doc = select(Document).where(Document.id == parsed_uuid)
            doc_direct = (await db.execute(q_doc)).scalar_one_or_none()
            if doc_direct:
                target_doc_id = doc_direct.id
        except (ValueError, TypeError):
            pass

    if not target_doc_id:
        raise HTTPException(status_code=404, detail="Document not found")

    q_full = (
        select(Document)
        .where(Document.id == target_doc_id)
        .options(
            selectinload(Document.items),
            selectinload(Document.client),
            selectinload(Document.supplier),
        )
    )
    doc = (await db.execute(q_full)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    q_shop = select(Shop).where(Shop.id == doc.shop_id)
    shop = (await db.execute(q_shop)).scalar_one_or_none()

    q_pm = select(PaymentMethod).where(PaymentMethod.shop_id == doc.shop_id)
    pms = (await db.execute(q_pm)).scalars().all()

    return {
        "success": True,
        "document": {
            "id": str(doc.id),
            "docNumber": doc.doc_number,
            "type": doc.type,
            "status": doc.status,
            "currency": doc.currency or "KES",
            "grandTotal": str(doc.grand_total or "0.00"),
            "subTotal": str(doc.sub_total or "0.00"),
            "taxAmount": str(doc.tax_amount or "0.00"),
            "notes": doc.notes,
            "termsAndConditions": doc.terms_and_conditions,
            "issueDate": doc.issue_date.isoformat() if doc.issue_date else None,
            "dueDate": doc.due_date.isoformat() if doc.due_date else None,
            "kraCuInvoiceNumber": doc.kra_cu_invoice_number,
            "requiresEtims": doc.requires_etims,
            "parentDocumentId": str(doc.parent_document_id) if doc.parent_document_id else None,
            "client": {
                "id": str(doc.client.id),
                "name": doc.client.name,
                "email": doc.client.email,
                "phone": getattr(doc.client, "phone", None),
                "taxPin": getattr(doc.client, "tax_pin", None),
            } if doc.client else None,
            "supplier": {
                "id": str(doc.supplier.id),
                "name": doc.supplier.name,
            } if doc.supplier else None,
            "items": [
                {
                    "id": str(item.id),
                    "description": item.description,
                    "notes": item.notes,
                    "quantity": str(item.quantity),
                    "unitPrice": str(item.unit_price),
                    "taxType": item.tax_type,
                    "taxAmount": str(item.tax_amount),
                    "itemTotal": str(item.item_total),
                }
                for item in (doc.items or [])
            ],
        },
        "shop": {
            "id": str(shop.id) if shop else str(doc.shop_id),
            "name": shop.name if shop else "Merchant",
            "slug": shop.slug if shop else "",
            "currency": shop.currency if shop else "KES",
            "taxPin": getattr(shop, "tax_pin", None) if shop else None,
            "primaryColor": getattr(shop, "primary_color", "#064e3b") if shop else "#064e3b",
        },
        "paymentMethods": [
            {
                "id": str(pm.id),
                "type": getattr(pm, "type", "MPESA"),
                "name": getattr(pm, "name", "M-Pesa"),
                "accountNumber": getattr(pm, "account_number", None),
                "paybillNumber": getattr(pm, "paybill_number", None),
            }
            for pm in pms
        ]
    }

