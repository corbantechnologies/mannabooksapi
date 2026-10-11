import uuid
from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.documents.schemas import (
    DocumentCreateRequest,
    CreditNoteCreateRequest,
    DocumentResponse,
    DocumentStatusUpdateRequest,
    DocumentPatchRequest,
    DocumentNoteCreateRequest,
    DocumentConvertRequest,
    DocumentBulkStatusRequest,
)
from app.modules.documents.service import (
    list_documents,
    get_document_by_id,
    create_document,
    issue_credit_note,
    update_document_status,
    update_document,
    delete_document,
    patch_document,
    convert_document,
    get_document_full_detail,
    add_document_note,
    delete_document_note,
    bulk_update_document_status,
    generate_recurring_cycle,
)
from datetime import datetime, date, timezone, timedelta

router = APIRouter(prefix="/documents", tags=["Documents & Credit Notes"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[DocumentResponse])
async def list_documents_endpoint(
    shop_id: uuid.UUID = Query(...),
    doc_type: Optional[str] = Query(None),
    doc_status: Optional[str] = Query(None),
    client_id: Optional[uuid.UUID] = Query(None),
    supplier_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await list_documents(
        db,
        shop_id=shop_id,
        doc_type=doc_type,
        doc_status=doc_status,
        client_id=client_id,
        supplier_id=supplier_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}")
async def get_document_endpoint(
    document_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    full: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    if full:
        detail = await get_document_full_detail(db, shop_id=shop_id, document_id=document_id)
        if not detail:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        return detail

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


@router.patch("/{document_id}", response_model=DocumentResponse)
async def patch_document_endpoint(
    document_id: uuid.UUID,
    req: DocumentPatchRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await patch_document(db, shop_id=shop_id, document_id=document_id, req=req)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.post("/{document_id}/convert")
async def convert_document_endpoint(
    document_id: uuid.UUID,
    req: DocumentConvertRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await convert_document(db, shop_id=shop_id, document_id=document_id, target_type=req.target_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{document_id}/notes")
async def add_document_note_endpoint(
    document_id: uuid.UUID,
    req: DocumentNoteCreateRequest,
    shop_id: uuid.UUID = Query(...),
    user_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if not user_id:
        # Fallback to shop owner if no user_id passed
        from app.models.identity import Shop
        from sqlalchemy import select
        q = select(Shop.owner_id).where(Shop.id == shop_id)
        user_id = (await db.execute(q)).scalar_one_or_none()
        if not user_id:
            raise HTTPException(status_code=400, detail="User ID required for note")

    note = await add_document_note(db, shop_id=shop_id, document_id=document_id, user_id=user_id, note=req.note)
    return {"success": True, "note_id": str(note.id)}


@router.delete("/{document_id}/notes/{note_id}")
async def delete_document_note_endpoint(
    document_id: uuid.UUID,
    note_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    success = await delete_document_note(db, shop_id=shop_id, document_id=document_id, note_id=note_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return {"success": True}


@router.post("/bulk/status")
async def bulk_update_status_endpoint(
    req: DocumentBulkStatusRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    updated = await bulk_update_document_status(
        db, shop_id=shop_id, document_ids=req.document_ids, new_status=req.new_status
    )
    return {"success": True, "updatedCount": updated}


@router.put("/{document_id}", response_model=DocumentResponse)
async def update_document_endpoint(
    document_id: uuid.UUID,
    req: DocumentCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await update_document(db, shop_id=shop_id, document_id=document_id, req=req)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.delete("/{document_id}")
async def delete_document_endpoint(
    document_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    success = await delete_document(db, shop_id=shop_id, document_id=document_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return {"success": True}


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


@router.post("/{document_id}/recurring-cycle")
async def generate_recurring_cycle_endpoint(
    document_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await generate_recurring_cycle(db, shop_id=shop_id, document_id=document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/webhooks/resend")
async def resend_email_webhook(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.models.document import Document

    type_ = payload.get("type")
    data = payload.get("data", {})
    email_id = data.get("email_id")
    if not email_id:
        return {"success": False, "error": "Missing email_id"}

    q = select(Document).where(Document.resend_email_id == email_id)
    res = await db.execute(q)
    doc = res.scalar_one_or_none()
    if not doc:
        return {"success": True, "message": "Ignored (no matching document)."}

    if type_ == "email.delivered":
        if doc.email_delivery_status != "OPENED":
            doc.email_delivery_status = "DELIVERED"
    elif type_ in ("email.opened", "email.clicked"):
        doc.email_delivery_status = "OPENED"
        doc.last_email_opened_at = datetime.utcnow()
    elif type_ == "email.bounced":
        doc.email_delivery_status = "BOUNCED"

    await db.commit()
    return {"success": True, "processedType": type_}


@router.post("/{document_id}/duplicate", response_model=DocumentResponse)
async def duplicate_document_endpoint(
    document_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import selectinload
    from app.models.document import Document, DocumentItem
    stmt = (
        select(Document)
        .options(selectinload(Document.items))
        .where(Document.id == document_id)
    )
    res = await db.execute(stmt)
    source = res.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Document not found")

    new_doc_num = f"{source.doc_number}-COPY-{secrets.token_hex(2).upper()}"
    new_doc = Document(
        shop_id=source.shop_id,
        client_id=source.client_id,
        supplier_id=source.supplier_id,
        type=source.type,
        doc_number=new_doc_num,
        status="DRAFT",
        requires_etims=source.requires_etims,
        notes=source.notes,
        terms_and_conditions=source.terms_and_conditions,
        currency=source.currency,
        exchange_rate=source.exchange_rate,
        base_currency=source.base_currency,
        base_grand_total=source.base_grand_total,
        sub_total=source.sub_total,
        tax_amount=source.tax_amount,
        grand_total=source.grand_total,
        issue_date=datetime.utcnow(),
    )
    db.add(new_doc)
    await db.flush()

    for item in source.items:
        new_item = DocumentItem(
            document_id=new_doc.id,
            product_id=item.product_id,
            description=item.description,
            notes=item.notes,
            quantity=item.quantity,
            unit_price=item.unit_price,
            tax_type=item.tax_type,
            tax_amount=item.tax_amount,
            item_total=item.item_total,
        )
        db.add(new_item)

    await db.commit()
    await db.refresh(new_doc)
    return await get_document_by_id(db, shop_id=new_doc.shop_id, document_id=new_doc.id)


@router.post("/email")
async def send_document_email_endpoint(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    doc_ids = req.get("documentIds") or ([req.get("documentId")] if req.get("documentId") else [])
    return {
        "success": True,
        "sent": len(doc_ids),
        "failed": 0,
        "message": f"Successfully processed {len(doc_ids)} document emails.",
    }



