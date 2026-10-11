import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.db.session import get_db
from app.models.document import Document, DocumentToken
from app.models.crm import Proposal, ProposalToken
from app.models.identity import Shop

router = APIRouter(prefix="/portal", tags=["Public Customer & Partner Portal"])


class ProposalDecisionPayload(BaseModel):
    signer_name: Optional[str] = None
    signature_data: Optional[str] = None
    notes: Optional[str] = None


class QuotationDecisionPayload(BaseModel):
    client_response: Optional[str] = None
    amendment_notes: Optional[str] = None


class CatalogQuoteRequest(BaseModel):
    client_name: str
    client_email: str
    client_phone: Optional[str] = None
    notes: Optional[str] = None
    items: List[Dict[str, Any]] = []


@router.post("/proposals/{token}/accept")
async def portal_accept_proposal(
    token: str,
    payload: Optional[ProposalDecisionPayload] = None,
    db: AsyncSession = Depends(get_db),
):
    q_tok = select(ProposalToken).where(ProposalToken.token == token)
    res_tok = await db.execute(q_tok)
    tok = res_tok.scalar_one_or_none()
    if not tok:
        raise HTTPException(status_code=404, detail="Proposal not found or invalid link")

    prop = await db.get(Proposal, tok.proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    prop.status = "ACCEPTED"
    prop.responded_at = datetime.utcnow()
    if payload:
        if payload.signer_name:
            prop.signer_name = payload.signer_name
        if payload.signature_data:
            prop.signature_data_url = payload.signature_data
        if payload.notes:
            prop.amendment_notes = payload.notes

    await db.commit()
    return {"success": True, "message": "Proposal accepted successfully"}


@router.post("/proposals/{token}/amendment")
async def portal_amend_proposal(
    token: str,
    payload: ProposalDecisionPayload,
    db: AsyncSession = Depends(get_db),
):
    q_tok = select(ProposalToken).where(ProposalToken.token == token)
    tok = (await db.execute(q_tok)).scalar_one_or_none()
    if not tok:
        raise HTTPException(status_code=404, detail="Proposal not found or invalid link")

    prop = await db.get(Proposal, tok.proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    prop.status = "AMENDMENT_REQUESTED"
    prop.responded_at = datetime.utcnow()
    prop.amendment_notes = payload.notes
    await db.commit()
    return {"success": True, "message": "Amendment request recorded"}


@router.post("/proposals/{token}/decline")
async def portal_decline_proposal(
    token: str,
    payload: Optional[ProposalDecisionPayload] = None,
    db: AsyncSession = Depends(get_db),
):
    q_tok = select(ProposalToken).where(ProposalToken.token == token)
    tok = (await db.execute(q_tok)).scalar_one_or_none()
    if not tok:
        raise HTTPException(status_code=404, detail="Proposal not found or invalid link")

    prop = await db.get(Proposal, tok.proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    prop.status = "DECLINED"
    prop.responded_at = datetime.utcnow()
    if payload and payload.notes:
        prop.amendment_notes = payload.notes
    await db.commit()
    return {"success": True, "message": "Proposal declined"}


@router.post("/quotations/{token}/accept")
async def portal_accept_quotation(
    token: str,
    payload: Optional[QuotationDecisionPayload] = None,
    db: AsyncSession = Depends(get_db),
):
    q_tok = select(DocumentToken).where(DocumentToken.token == token)
    tok = (await db.execute(q_tok)).scalar_one_or_none()
    if not tok:
        raise HTTPException(status_code=404, detail="Quotation token not found")

    doc = await db.get(Document, tok.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.client_portal_response = "ACCEPTED"
    doc.status = "CONFIRMED"

    # Automatically generate child invoice from accepted quotation
    from app.modules.documents.service import convert_document
    q_existing = select(Document).where(
        and_(
            Document.parent_document_id == doc.id,
            Document.type == "INVOICE",
            Document.shop_id == doc.shop_id,
        )
    )
    existing_inv = (await db.execute(q_existing)).scalar_one_or_none()

    invoice_info = None
    if not existing_inv:
        invoice_info = await convert_document(db, shop_id=doc.shop_id, document_id=doc.id, target_type="INVOICE")
        doc.status = "CONFIRMED"
    else:
        invoice_info = {
            "new_document_id": str(existing_inv.id),
            "doc_number": existing_inv.doc_number,
            "serial": existing_inv.doc_number,
            "newDocumentId": str(existing_inv.id),
            "docNumber": existing_inv.doc_number,
        }

    await db.commit()
    return {
        "success": True,
        "message": "Quotation accepted and invoice generated successfully",
        "invoice": invoice_info,
        "newDocumentId": invoice_info.get("new_document_id"),
        "docNumber": invoice_info.get("doc_number"),
    }


@router.post("/quotations/{token}/amendment")
async def portal_amend_quotation(
    token: str,
    payload: QuotationDecisionPayload,
    db: AsyncSession = Depends(get_db),
):
    q_tok = select(DocumentToken).where(DocumentToken.token == token)
    tok = (await db.execute(q_tok)).scalar_one_or_none()
    if not tok:
        raise HTTPException(status_code=404, detail="Quotation token not found")

    doc = await db.get(Document, tok.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.client_portal_response = "AMENDMENT_REQUESTED"
    doc.client_amendment_notes = payload.amendment_notes or payload.client_response
    await db.commit()
    return {"success": True, "message": "Quotation amendment requested"}


@router.post("/catalog/{slug}/quote")
async def portal_catalog_quote_request(
    slug: str,
    req: CatalogQuoteRequest,
    db: AsyncSession = Depends(get_db),
):
    q_shop = select(Shop).where(Shop.slug == slug)
    shop = (await db.execute(q_shop)).scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Store catalog not found")

    return {
        "success": True,
        "message": f"Quotation request sent to {shop.name}. Our sales team will get back to you shortly.",
        "reference": f"QREQ-{datetime.utcnow().strftime('%y%m%d%H%M')}",
    }


catalog_router = APIRouter(prefix="/catalog", tags=["Store Catalog"])


@catalog_router.get("/{slug}")
async def get_public_catalog(
    slug: str,
    search: Optional[str] = Query(None),
    item_ids: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.identity import Shop, ShopTerm
    from app.models.item import Product
    from sqlalchemy import select, and_, or_, func

    q_shop = select(Shop).where(Shop.slug == slug)
    shop = (await db.execute(q_shop)).scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Workspace not found")

    q_prod = select(Product).where(and_(Product.shop_id == shop.id, Product.is_active == True))
    if search:
        s = f"%{search.strip().lower()}%"
        q_prod = q_prod.where(or_(func.lower(Product.name).like(s), func.lower(Product.sku).like(s)))

    if item_ids:
        try:
            ids = [uuid.UUID(i.strip()) for i in item_ids.split(",") if i.strip()]
            if ids:
                q_prod = q_prod.where(Product.id.in_(ids))
        except Exception:
            pass

    res_prod = await db.execute(q_prod)
    products = res_prod.scalars().all()

    q_terms = select(ShopTerm).where(ShopTerm.shop_id == shop.id)
    res_terms = await db.execute(q_terms)
    terms = res_terms.scalars().all()

    return {
        "success": True,
        "shop": {
            "id": str(shop.id),
            "name": shop.name,
            "shortName": shop.short_name or shop.name,
            "slug": shop.slug,
            "currency": shop.currency or "KES",
            "phone": shop.phone,
            "email": shop.email,
            "website": shop.website,
            "logoUrl": shop.logo_url,
            "primaryColor": shop.primary_color or "#000000",
            "taxPin": shop.tax_pin,
            "isVatRegistered": bool(shop.is_vat_registered),
        },
        "products": [
            {
                "id": str(p.id),
                "name": p.name,
                "sku": p.sku,
                "itemType": p.item_type,
                "unitPrice": float(p.unit_price),
                "defaultTaxType": p.default_tax_type,
                "trackStock": bool(p.track_stock),
                "stockQuantity": float(p.stock_quantity),
            }
            for p in products
        ],
        "terms": [
            {
                "id": str(t.id),
                "title": t.title,
                "content": t.content,
                "displayOrder": t.display_order,
                "isDefaultCatalog": bool(t.is_default_catalog),
            }
            for t in terms
        ],
    }


contact_router = APIRouter(prefix="/contact", tags=["Contact"])


@contact_router.post("")
async def submit_contact_form(req: dict):
    return {
        "success": True,
        "message": "Thank you for reaching out! Our team will respond to your inquiry shortly.",
    }
