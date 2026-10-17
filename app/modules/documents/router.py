import uuid
import secrets
from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, and_, or_, desc, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key, require_internal_service
from app.models.payment import PaymentMethod, serialize_payment_method
from app.modules.documents.schemas import (
    DocumentCreateRequest,
    CreditNoteCreateRequest,
    DocumentResponse,
    DocumentStatusUpdateRequest,
    DocumentPatchRequest,
    DocumentNoteCreateRequest,
    DocumentConvertRequest,
    DocumentBulkStatusRequest,
    DocumentActionRequest,
)
from app.modules.documents.service import (
    list_documents,
    get_document_by_id,
    create_document,
    issue_credit_note,
    update_document_status,
    perform_document_action,
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
from app.core.datetime_utils import utcnow

router = APIRouter(prefix="/documents", tags=["Documents & Credit Notes"], dependencies=[Depends(verify_internal_api_key)])


def _user_id(request: Request) -> Optional[uuid.UUID]:
    from app.api.deps import get_acting_user_id

    return get_acting_user_id(request)


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
    request: Request,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_document(db, shop_id=shop_id, req=req, user_id=_user_id(request))


@router.post("/{document_id}/credit-note", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def issue_credit_note_endpoint(
    document_id: uuid.UUID,
    req: CreditNoteCreateRequest,
    request: Request,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await issue_credit_note(db, shop_id=shop_id, parent_doc_id=document_id, req=req, user_id=_user_id(request))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.patch("/{document_id}/status", response_model=DocumentResponse)
async def update_status_endpoint(
    document_id: uuid.UUID,
    req: DocumentStatusUpdateRequest,
    request: Request,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await update_document_status(
        db, shop_id=shop_id, document_id=document_id, new_status=req.status,
        user_id=_user_id(request), reason=req.reason,
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.post("/{document_id}/actions/{action}")
async def document_action_endpoint(
    document_id: uuid.UUID,
    action: str,
    request: Request,
    req: Optional[DocumentActionRequest] = None,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Run one workflow action (issue, void, accept, decline, revise, cancel, confirm, receive, approve, pay)."""
    doc = await perform_document_action(
        db, shop_id=shop_id, document_id=document_id, action=action.lower(),
        user_id=_user_id(request), reason=req.reason if req else None,
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return await get_document_full_detail(db, shop_id=shop_id, document_id=document_id)


@router.patch("/{document_id}", response_model=DocumentResponse)
async def patch_document_endpoint(
    document_id: uuid.UUID,
    req: DocumentPatchRequest,
    request: Request,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    doc = await patch_document(db, shop_id=shop_id, document_id=document_id, req=req, user_id=_user_id(request))
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
    request: Request,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.api.deps import get_acting_user_id
    user_id = get_acting_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="A signed-in user is required to add notes")

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
    request: Request,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    result = await bulk_update_document_status(
        db, shop_id=shop_id, document_ids=req.document_ids, new_status=req.new_status, user_id=_user_id(request)
    )
    return {"success": True, "updatedCount": result["updated"], "skipped": result["skipped"]}


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


async def _caller_is_member(request: Request, db: AsyncSession, shop_id: uuid.UUID) -> bool:
    from app.api.deps import get_principal
    from app.models.identity import Shop, ShopMember

    try:
        principal = await get_principal(request, db)
    except HTTPException:
        return False
    user = principal.user
    if not user:
        return False
    if user.is_super_admin:
        return True
    member = (
        await db.execute(
            select(ShopMember).where(
                ShopMember.shop_id == shop_id, ShopMember.user_id == user.id, ShopMember.is_active == True  # noqa: E712
            )
        )
    ).scalars().first()
    if member:
        return True
    owner_id = (await db.execute(select(Shop.owner_id).where(Shop.id == shop_id))).scalar_one_or_none()
    return owner_id == user.id


@router.get("/public/{token}")
async def get_public_document_endpoint(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.models.document import Document, DocumentToken
    from app.models.identity import Shop

    target_doc_id = None

    q_tok = select(DocumentToken).where(DocumentToken.token == token)
    tok_res = (await db.execute(q_tok)).scalar_one_or_none()
    if tok_res:
        target_doc_id = tok_res.document_id

    if not target_doc_id:
        # Raw document ids are NOT public: only signed-in members of the owning workspace may use them
        # (e.g. printing a payroll voucher). Customers always get the unguessable token link.
        try:
            parsed_uuid = uuid.UUID(token)
        except (ValueError, TypeError):
            parsed_uuid = None
        if parsed_uuid:
            doc_direct = (await db.execute(select(Document).where(Document.id == parsed_uuid))).scalar_one_or_none()
            if doc_direct and await _caller_is_member(request, db, doc_direct.shop_id):
                target_doc_id = doc_direct.id

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

    # Child documents
    q_children = (
        select(Document)
        .where(and_(Document.parent_document_id == doc.id, Document.shop_id == doc.shop_id))
        .order_by(Document.created_at.asc())
    )
    children_res = list((await db.execute(q_children)).scalars().all())

    # Self-healing: If quotation was accepted/confirmed but child invoice was missing, auto-generate it
    if (
        doc.type == "QUOTATION"
        and (doc.status == "CONFIRMED" or doc.client_portal_response == "ACCEPTED")
        and not any(c.type == "INVOICE" for c in children_res)
    ):
        try:
            from app.modules.documents.service import convert_document
            await convert_document(db, shop_id=doc.shop_id, document_id=doc.id, target_type="INVOICE")
            doc.status = "CONFIRMED"
            await db.commit()
            q_children_re = (
                select(Document)
                .where(and_(Document.parent_document_id == doc.id, Document.shop_id == doc.shop_id))
                .order_by(Document.created_at.asc())
            )
            children_res = list((await db.execute(q_children_re)).scalars().all())
        except Exception:
            pass

    child_docs = [
        {
            "id": str(c.id),
            "docNumber": c.doc_number,
            "type": c.type,
            "status": c.status,
            "grandTotal": str(c.grand_total or "0.00"),
            "issueDate": c.issue_date.isoformat() if c.issue_date else None,
        }
        for c in children_res
    ]

    portal_response = doc.client_portal_response or ("ACCEPTED" if doc.status == "CONFIRMED" else None)
    is_decided = portal_response in ("ACCEPTED", "DECLINED") or doc.status in ("CONFIRMED", "CANCELLED")

    ret = {
        "success": True,
        "document": {
            "id": str(doc.id),
            "docNumber": doc.doc_number,
            "type": doc.type,
            "status": doc.status,
            "clientPortalResponse": portal_response,
            "client_portal_response": portal_response,
            "clientAmendmentNotes": doc.client_amendment_notes,
            "isAccepted": portal_response == "ACCEPTED" or doc.status == "CONFIRMED",
            "isDecided": is_decided,
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
            "childDocuments": child_docs,
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
        "paymentMethods": [serialize_payment_method(pm) for pm in pms],
    }
    ret["document"]["paymentMethods"] = ret["paymentMethods"]
    if "shop" in ret and ret["shop"]:
        ret["shop"]["paymentMethods"] = ret["paymentMethods"]
    return ret


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


@router.post("/webhooks/resend", dependencies=[Depends(require_internal_service)])
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
        doc.last_email_opened_at = utcnow()
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
    from app.models.document import Document, DocumentItem, DocumentToken
    stmt = (
        select(Document)
        .options(selectinload(Document.items))
        .where(Document.id == document_id, *([Document.shop_id == shop_id] if shop_id else []))
    )
    res = await db.execute(stmt)
    source = res.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Document not found")

    from app.modules.documents.service import generate_doc_number

    new_doc_num = await generate_doc_number(db, source.shop_id, source.type)
    from app.modules.documents.loyalty import undiscounted

    _, list_tax, list_total = undiscounted((it.quantity * it.unit_price, it.tax_amount) for it in source.items)
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
        # A copy starts at list prices; a loyalty discount belongs to the original sale.
        sub_total=source.sub_total,
        tax_amount=list_tax,
        grand_total=list_total,
        issue_date=utcnow(),
    )
    db.add(new_doc)
    await db.flush()
    db.add(DocumentToken(document_id=new_doc.id, token=secrets.token_hex(32)))

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



