import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from app.db.session import get_db
from app.models.document import Document, DocumentToken
from app.models.crm import Proposal, ProposalToken
from app.models.identity import Shop, User
from app.api.deps import require_internal_service
from app.core.datetime_utils import utcnow
from app.core.schemas import ApiModel

router = APIRouter(prefix="/portal", tags=["Public Customer & Partner Portal"])


class ProposalDecisionPayload(ApiModel):
    signer_name: Optional[str] = None
    signature_data: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=5000, validation_alias=AliasChoices("notes", "amendment_notes", "amendmentNotes"))
    client_name: Optional[str] = Field(None, max_length=200)
    contact_email: Optional[str] = Field(None, max_length=200)


class QuotationDecisionPayload(ApiModel):
    client_response: Optional[str] = None
    amendment_notes: Optional[str] = Field(None, max_length=5000)
    client_name: Optional[str] = Field(None, max_length=200)
    contact_email: Optional[str] = Field(None, max_length=200)


def _with_contact(notes: Optional[str], name: Optional[str], email: Optional[str]) -> Optional[str]:
    who = " ".join(x for x in [(name or "").strip(), f"<{email.strip()}>" if email and email.strip() else ""] if x)
    if not who:
        return notes
    return f"From {who}: {notes or ''}".strip()


class CatalogQuoteItem(ApiModel):
    product_id: uuid.UUID = Field(..., validation_alias=AliasChoices("product_id", "productId"))
    quantity: Decimal = Field(..., gt=0, le=Decimal("100000"))
    notes: Optional[str] = Field(None, max_length=500)


class CatalogQuoteRequest(ApiModel):
    customer_name: str = Field(..., min_length=2, max_length=150, validation_alias=AliasChoices("customer_name", "customerName", "client_name"))
    customer_email: Optional[str] = Field(None, max_length=200, validation_alias=AliasChoices("customer_email", "customerEmail", "client_email"))
    customer_phone: Optional[str] = Field(None, max_length=20, validation_alias=AliasChoices("customer_phone", "customerPhone", "client_phone"))
    customer_notes: Optional[str] = Field(None, max_length=2000, validation_alias=AliasChoices("customer_notes", "customerNotes", "notes"))
    items: List[CatalogQuoteItem] = Field(..., min_length=1, max_length=50)


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
    prop.responded_at = utcnow()
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
    prop.responded_at = utcnow()
    prop.amendment_notes = _with_contact(payload.notes, payload.client_name, payload.contact_email)
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
    prop.responded_at = utcnow()
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

    from app.modules.documents.service import convert_document

    # If quotation was already accepted/confirmed, return idempotent response
    if doc.status == "CONFIRMED" or doc.client_portal_response == "ACCEPTED":
        q_existing = select(Document).where(
            and_(
                Document.parent_document_id == doc.id,
                Document.type == "INVOICE",
                Document.shop_id == doc.shop_id,
            )
        )
        existing_inv = (await db.execute(q_existing)).scalar_one_or_none()
        if not existing_inv:
            invoice_info = await convert_document(db, shop_id=doc.shop_id, document_id=doc.id, target_type="INVOICE")
            doc.status = "CONFIRMED"
            await db.commit()
        else:
            invoice_info = {
                "new_document_id": str(existing_inv.id),
                "doc_number": existing_inv.doc_number,
                "serial": existing_inv.doc_number,
                "newDocumentId": str(existing_inv.id),
                "docNumber": existing_inv.doc_number,
            }
        return {
            "success": True,
            "already_accepted": True,
            "message": "Quotation has already been accepted and confirmed.",
            "invoice": invoice_info,
            "newDocumentId": invoice_info.get("new_document_id"),
            "docNumber": invoice_info.get("doc_number"),
        }

    if doc.status == "EXPIRED":
        raise HTTPException(status_code=409, detail="This quotation has expired. Ask the business for an updated quote.")
    if doc.status not in ("DRAFT", "ISSUED"):
        raise HTTPException(status_code=409, detail="This quotation is no longer open for acceptance.")

    doc.client_portal_response = "ACCEPTED"
    doc.status = "CONFIRMED"

    # Automatically generate child invoice from accepted quotation
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

    if doc.status == "CONFIRMED" or doc.client_portal_response == "ACCEPTED":
        raise HTTPException(
            status_code=400,
            detail="Cannot request changes on a quotation that has already been accepted and confirmed.",
        )

    doc.client_portal_response = "AMENDMENT_REQUESTED"
    doc.client_amendment_notes = _with_contact(
        payload.amendment_notes or payload.client_response, payload.client_name, payload.contact_email
    )
    await db.commit()
    return {"success": True, "message": "Quotation amendment requested"}


@router.post("/catalog/{slug}/quote", dependencies=[Depends(require_internal_service)])
async def portal_catalog_quote_request(
    slug: str,
    req: CatalogQuoteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Public catalog "request a quote" (called by the Next.js server, which sends the emails).
    Creates a DRAFT quotation priced from the catalog (never from the browser) for the shop to review.
    Shops with `catalog_auto_issue_quotes` get an ISSUED quotation and the customer gets the portal link.
    """
    import re
    from app.core.rate_limit import client_ip, hit
    from app.models.client import Client
    from app.models.governance import Notification
    from app.models.identity import ShopMember
    from app.models.item import Product
    from app.modules.documents.schemas import DocumentCreateRequest, DocumentItemInput
    from app.modules.documents.service import create_document

    shop = (await db.execute(select(Shop).where(Shop.slug == slug))).scalar_one_or_none()
    if not shop or getattr(shop, "is_suspended", False):
        raise HTTPException(status_code=404, detail="Store catalog not found")

    ip = client_ip(request)
    await hit(f"catalog-quote:ip:{ip}", limit=5, window_seconds=60 * 60)
    await hit(f"catalog-quote:shop:{shop.id}", limit=200, window_seconds=24 * 60 * 60)

    email = (req.customer_email or "").strip().lower()
    phone = re.sub(r"[^\d+]", "", req.customer_phone or "")[:20]
    if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Please provide an email address or phone number.")

    product_ids = list({i.product_id for i in req.items})
    products = {
        p.id: p
        for p in (
            await db.execute(select(Product).where(Product.shop_id == shop.id, Product.id.in_(product_ids)))
        ).scalars().all()
    }
    missing = [str(pid) for pid in product_ids if pid not in products]
    if missing:
        raise HTTPException(status_code=400, detail="Some selected items are no longer available. Please refresh the catalog.")

    # Find the customer in this shop by email, then phone; otherwise create them.
    client = None
    if email:
        client = (
            await db.execute(select(Client).where(Client.shop_id == shop.id, func.lower(Client.email) == email))
        ).scalars().first()
    if client is None and phone:
        client = (await db.execute(select(Client).where(Client.shop_id == shop.id, Client.phone == phone))).scalars().first()
    if client is None:
        client = Client(
            shop_id=shop.id,
            name=req.customer_name.strip(),
            email=email,
            phone=phone or None,
            client_type="INDIVIDUAL",
        )
        db.add(client)
        await db.flush()

    auto_issue = bool(getattr(shop, "catalog_auto_issue_quotes", False))
    notes = "Requested via the online catalog."
    if req.customer_notes:
        notes += f"\nCustomer notes: {req.customer_notes.strip()}"

    doc_req = DocumentCreateRequest(
        client_id=client.id,
        type="QUOTATION",
        status="ISSUED" if auto_issue else "DRAFT",
        currency=shop.currency or "KES",
        notes=notes,
        items=[
            DocumentItemInput(
                product_id=products[item.product_id].id,
                description=products[item.product_id].name,
                notes=item.notes,
                quantity=item.quantity,
                unit_price=products[item.product_id].unit_price,
                tax_type=products[item.product_id].default_tax_type if shop.is_vat_registered else "EXEMPT",
            )
            for item in req.items
        ],
    )
    doc = await create_document(db, shop_id=shop.id, req=doc_req)
    doc_id = getattr(doc, "id", None) or (doc.get("id") if isinstance(doc, dict) else None)
    full = await db.get(Document, uuid.UUID(str(doc_id)))
    token_row = (await db.execute(select(DocumentToken).where(DocumentToken.document_id == full.id))).scalar_one_or_none()

    # In-app notification for the people who handle sales in this shop.
    recipients = set(
        (
            await db.execute(
                select(ShopMember.user_id).where(
                    ShopMember.shop_id == shop.id,
                    ShopMember.is_active == True,  # noqa: E712
                    ShopMember.role.in_(["OWNER", "ADMIN", "MANAGER", "SALES_REP"]),
                )
            )
        ).scalars().all()
    )
    recipients.add(shop.owner_id)
    link = f"/workspaces/{shop.slug}/documents/{full.id}"
    for user_id in recipients:
        db.add(
            Notification(
                user_id=user_id,
                shop_id=shop.id,
                title=f"New catalog quote request {full.doc_number}",
                message=f"{req.customer_name.strip()} requested {len(req.items)} item(s), total {shop.currency or 'KES'} {full.grand_total:,.2f}."
                + ("" if auto_issue else " Review and issue the draft quotation."),
                type="CATALOG_QUOTE",
                link=link,
            )
        )
    await db.commit()

    owner = await db.get(User, shop.owner_id)
    return {
        "success": True,
        "status": full.status,
        "serial": full.doc_number,
        "documentId": str(full.id),
        "grandTotal": float(full.grand_total),
        "currency": shop.currency or "KES",
        # The portal link is only shared once the quotation is issued.
        "token": token_row.token if (auto_issue and token_row) else None,
        "notify": {
            "shopName": shop.name,
            "shopEmail": shop.email or (owner.email if owner else None),
            "customerName": req.customer_name.strip(),
            "customerEmail": email or None,
            "customerPhone": phone or None,
            "itemCount": len(req.items),
            "workspaceLink": link,
        },
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
    if not shop or getattr(shop, "is_suspended", False):
        raise HTTPException(status_code=404, detail="Workspace not found")

    q_prod = select(Product).where(Product.shop_id == shop.id).order_by(Product.name).limit(500)
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
                "inStock": (not p.track_stock) or p.stock_quantity > 0,
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
async def submit_contact_form():
    # The contact form is handled by the Next.js /api/contact route (emails the team).
    # This endpoint used to discard messages while reporting success.
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="Use the website contact form.")
