import uuid
import secrets
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status, Request, Cookie
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func, delete

from app.db.session import get_db
from app.core.config import get_settings
from app.api.deps import verify_internal_api_key
from app.models.identity import User, Shop
from app.models.client import Client
from app.models.document import Document, DocumentItem
from app.models.crm import (
    Deal, DealActivity, Proposal, ProposalItem, ProposalToken,
    Contract, ContractHoursLog, Project, ProjectMember, Timesheet, ProjectMilestone
)
from app.modules.auth.service import verify_and_get_session

settings = get_settings()

router = APIRouter(tags=["CRM, Proposals, Contracts & Projects"])


async def _verify_auth(
    request: Request,
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    authorization: Optional[str] = Header(None),
    access_token: Optional[str] = Cookie(None, alias="access_token"),
    db: AsyncSession = Depends(get_db),
):
    return await verify_internal_api_key(
        request=request,
        x_internal_key=x_internal_key,
        authorization=authorization,
        access_token=access_token,
        db=db,
    )


async def _resolve_shop_id(db: AsyncSession, shop_id: Optional[str] = None, shop_slug: Optional[str] = None) -> Optional[uuid.UUID]:
    if shop_id:
        try:
            return uuid.UUID(shop_id)
        except ValueError:
            pass
    if shop_slug:
        res = await db.execute(select(Shop.id).where(Shop.slug == shop_slug))
        return res.scalar_one_or_none()
    return None


# -------------------------------------------------------------------------
# Request Models
# -------------------------------------------------------------------------
class CreateDealRequest(BaseModel):
    shop_id: str
    client_id: Optional[str] = None
    title: str
    value: Decimal = Decimal("0.00")
    currency: str = "KES"
    stage: str = "LEAD"
    probability: int = 10
    close_date: Optional[str] = None


class UpdateDealRequest(BaseModel):
    title: Optional[str] = None
    value: Optional[Decimal] = None
    stage: Optional[str] = None
    probability: Optional[int] = None
    close_date: Optional[str] = None
    lost_reason: Optional[str] = None


class CreateProposalItemRequest(BaseModel):
    id: Optional[str] = None
    product_id: Optional[str] = None
    package_label: Optional[str] = None
    description: str
    notes: Optional[str] = None
    quantity: Decimal = Decimal("1.0000")
    unit_price: Decimal = Decimal("0.00")
    discount_amount: Decimal = Decimal("0.00")
    tax_type: Optional[str] = "EXEMPT"
    tax_amount: Optional[Decimal] = Decimal("0.00")
    item_total: Optional[Decimal] = None


class CreateProposalRequest(BaseModel):
    shop_id: str
    deal_id: Optional[str] = None
    client_id: Optional[str] = None
    title: str
    currency: str = "KES"
    items: List[CreateProposalItemRequest] = []
    valid_until: Optional[str] = None
    notes: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    executive_summary: Optional[str] = None
    scope_of_work: Optional[str] = None


class UpdateProposalRequest(BaseModel):
    shop_id: Optional[str] = None
    deal_id: Optional[str] = None
    client_id: Optional[str] = None
    title: Optional[str] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    valid_until: Optional[str] = None
    notes: Optional[str] = None
    executive_summary: Optional[str] = None
    scope_of_work: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    items: Optional[List[CreateProposalItemRequest]] = None


class CreateContractRequest(BaseModel):
    shop_id: str
    client_id: Optional[str] = None
    deal_id: Optional[str] = None
    title: str
    currency: str = "KES"
    monthly_fee: Decimal = Decimal("0.00")
    is_retainer_hours: bool = False
    monthly_hours_allocated: Decimal = Decimal("0.00")
    hourly_rate: Decimal = Decimal("0.00")
    start_date: str
    end_date: Optional[str] = None
    terms_and_conditions: Optional[str] = None


class LogContractHoursRequest(BaseModel):
    billing_month: str  # YYYY-MM
    hours_used: Decimal
    description: Optional[str] = None
    log_date: Optional[str] = None


class CreateProjectRequest(BaseModel):
    shop_id: str
    client_id: Optional[str] = None
    contract_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    currency: str = "KES"
    budget_type: str = "FIXED"
    budget_amount: Decimal = Decimal("0.00")
    budget_hours: Decimal = Decimal("0.00")
    start_date: Optional[str] = None
    end_date: Optional[str] = None


# -------------------------------------------------------------------------
# DEALS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/crm/deals")
async def list_deals(
    shop_id: Optional[str] = Query(None),
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "deals": []}

    q = select(Deal).where(Deal.shop_id == sid).order_by(desc(Deal.created_at))
    res = await db.execute(q)
    deals = res.scalars().all()
    return {"success": True, "deals": deals}


@router.get("/crm/deals/{deal_id}")
async def get_deal(
    deal_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    # fetch activities
    act_q = select(DealActivity).where(DealActivity.deal_id == deal_id).order_by(desc(DealActivity.created_at))
    activities = (await db.execute(act_q)).scalars().all()
    return {"success": True, "deal": deal, "activities": activities}


@router.post("/crm/deals")
async def create_deal(
    req: CreateDealRequest,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = auth.id if hasattr(auth, "id") else None
    c_date = date.fromisoformat(req.close_date) if req.close_date else None
    deal = Deal(
        shop_id=uuid.UUID(req.shop_id),
        client_id=uuid.UUID(req.client_id) if req.client_id else None,
        assigned_user_id=user_id,
        title=req.title.strip(),
        value=req.value,
        currency=req.currency.upper(),
        stage=req.stage,
        probability=req.probability,
        close_date=c_date,
    )
    db.add(deal)
    await db.commit()
    await db.refresh(deal)
    return {"success": True, "deal": deal}


@router.patch("/crm/deals/{deal_id}")
@router.patch("/crm/deals/{deal_id}/stage")
async def update_deal(
    deal_id: uuid.UUID,
    req: dict,
    shop_id: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    if "stage" in req and req["stage"]:
        deal.stage = req["stage"]
    if "title" in req and req["title"]:
        deal.title = req["title"].strip()
    if "value" in req:
        deal.value = Decimal(str(req["value"]))
    if "probability" in req:
        deal.probability = int(req["probability"])
    if "lost_reason" in req or "lostReason" in req:
        deal.lost_reason = req.get("lost_reason") or req.get("lostReason")

    await db.commit()
    await db.refresh(deal)
    return {"success": True, "deal": deal}


@router.post("/crm/deals/{deal_id}/activities")
async def add_deal_activity(
    deal_id: uuid.UUID,
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    user_id = auth.id if hasattr(auth, "id") else None
    activity_type = req.get("type") or req.get("activity_type") or "NOTE"
    desc = req.get("description") or req.get("notes") or ""

    act = DealActivity(
        deal_id=deal_id,
        shop_id=deal.shop_id,
        user_id=user_id,
        activity_type=activity_type,
        notes=desc,
    )
    db.add(act)
    await db.commit()
    await db.refresh(act)
    return {"success": True, "activity": act}


@router.get("/crm/forecast")
async def get_crm_forecast(
    shop_id: Optional[str] = Query(None),
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"totalPipeline": 0.0, "weightedForecast": 0.0, "stages": {}}

    q = select(Deal).where(and_(Deal.shop_id == sid, Deal.stage.notin_(["LOST"])))
    deals = (await db.execute(q)).scalars().all()

    pipeline_total = sum(float(d.value) for d in deals)
    weighted_total = sum(float(d.value) * (d.probability / 100.0) for d in deals)

    stages = {}
    for d in deals:
        stages[d.stage] = stages.get(d.stage, 0.0) + float(d.value)

    return {
        "success": True,
        "totalPipeline": pipeline_total,
        "weightedForecast": round(weighted_total, 2),
        "dealsCount": len(deals),
        "stages": stages,
    }


# -------------------------------------------------------------------------
# PROPOSALS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/crm/proposals")
async def list_proposals(
    shop_id: Optional[str] = Query(None),
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "proposals": []}

    q = (
        select(Proposal, Client)
        .outerjoin(Client, Proposal.client_id == Client.id)
        .where(Proposal.shop_id == sid)
        .order_by(desc(Proposal.created_at))
    )
    res = await db.execute(q)
    proposals_list = []
    for prop, client in res.all():
        proposals_list.append({
            "id": str(prop.id),
            "shop_id": str(prop.shop_id),
            "shopId": str(prop.shop_id),
            "deal_id": str(prop.deal_id) if prop.deal_id else None,
            "dealId": str(prop.deal_id) if prop.deal_id else None,
            "client_id": str(prop.client_id) if prop.client_id else None,
            "clientId": str(prop.client_id) if prop.client_id else None,
            "proposal_number": prop.proposal_number,
            "proposalNumber": prop.proposal_number,
            "title": prop.title,
            "status": prop.status,
            "currency": prop.currency,
            "subtotal": float(prop.subtotal),
            "grand_total": float(prop.subtotal),
            "grandTotal": float(prop.subtotal),
            "valid_until": prop.valid_until.isoformat() if prop.valid_until else None,
            "validUntil": prop.valid_until.isoformat() if prop.valid_until else None,
            "created_at": prop.created_at.isoformat() if prop.created_at else None,
            "createdAt": prop.created_at.isoformat() if prop.created_at else None,
            "view_count": prop.view_count,
            "viewCount": prop.view_count,
            "client_name": client.name if client else None,
            "clientName": client.name if client else None,
            "client": {
                "id": str(client.id),
                "name": client.name,
                "email": client.email,
            } if client else None,
        })
    return {"success": True, "proposals": proposals_list}


@router.get("/crm/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    prop = await db.get(Proposal, proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    items_q = select(ProposalItem).where(ProposalItem.proposal_id == proposal_id).order_by(ProposalItem.display_order)
    items = (await db.execute(items_q)).scalars().all()

    token_q = select(ProposalToken.token).where(ProposalToken.proposal_id == proposal_id)
    token = (await db.execute(token_q)).scalar_one_or_none()

    client = await db.get(Client, prop.client_id) if prop.client_id else None
    deal = await db.get(Deal, prop.deal_id) if prop.deal_id else None

    serialized_items = [
        {
            "id": str(it.id),
            "proposalId": str(it.proposal_id),
            "proposal_id": str(it.proposal_id),
            "packageLabel": it.package_label,
            "package_label": it.package_label,
            "description": it.description,
            "notes": it.notes,
            "quantity": float(it.quantity),
            "unitPrice": float(it.unit_price),
            "unit_price": float(it.unit_price),
            "itemTotal": float(it.item_total),
            "item_total": float(it.item_total),
            "displayOrder": it.display_order,
            "display_order": it.display_order,
        }
        for it in items
    ]

    proposal_data = {
        "id": str(prop.id),
        "shopId": str(prop.shop_id),
        "shop_id": str(prop.shop_id),
        "dealId": str(prop.deal_id) if prop.deal_id else None,
        "deal_id": str(prop.deal_id) if prop.deal_id else None,
        "clientId": str(prop.client_id) if prop.client_id else None,
        "client_id": str(prop.client_id) if prop.client_id else None,
        "proposalNumber": prop.proposal_number,
        "proposal_number": prop.proposal_number,
        "title": prop.title,
        "status": prop.status,
        "currency": prop.currency,
        "subtotal": float(prop.subtotal),
        "grandTotal": float(prop.subtotal),
        "grand_total": float(prop.subtotal),
        "validUntil": prop.valid_until.isoformat() if prop.valid_until else None,
        "valid_until": prop.valid_until.isoformat() if prop.valid_until else None,
        "executiveSummary": prop.executive_summary,
        "executive_summary": prop.executive_summary,
        "scopeOfWork": prop.scope_of_work,
        "scope_of_work": prop.scope_of_work,
        "termsAndConditions": prop.terms_and_conditions,
        "terms_and_conditions": prop.terms_and_conditions,
        "notes": prop.executive_summary or prop.notes,
        "isAmendmentRequested": bool(prop.amendment_notes),
        "amendmentNotes": prop.amendment_notes,
        "sentAt": prop.sent_at.isoformat() if prop.sent_at else None,
        "respondedAt": prop.responded_at.isoformat() if prop.responded_at else None,
        "signedByName": prop.signer_name,
        "viewCount": prop.view_count,
        "convertedDocumentId": str(prop.converted_document_id) if prop.converted_document_id else None,
        "client": {
            "id": str(client.id),
            "name": client.name,
            "email": client.email,
        } if client else None,
        "deal": {
            "id": str(deal.id),
            "title": deal.title,
        } if deal else None,
        "items": serialized_items,
        "publicToken": token,
    }

    return {
        "success": True,
        "proposal": proposal_data,
        "items": serialized_items,
        "publicToken": token,
    }


@router.post("/crm/proposals")
async def create_proposal(
    req: CreateProposalRequest,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    proposal_number = f"PROP-{now.strftime('%y%m')}-{secrets.randbelow(9000) + 1000}"

    subtotal = sum((it.quantity * it.unit_price) - it.discount_amount for it in req.items) if req.items else Decimal("0.00")
    v_until = None
    if req.valid_until:
        try:
            v_until = date.fromisoformat(req.valid_until.split("T")[0])
        except Exception:
            pass

    prop = Proposal(
        shop_id=uuid.UUID(req.shop_id),
        deal_id=uuid.UUID(req.deal_id) if req.deal_id else None,
        client_id=uuid.UUID(req.client_id) if req.client_id else None,
        proposal_number=proposal_number,
        title=req.title.strip(),
        currency=req.currency.upper(),
        subtotal=subtotal,
        status="DRAFT",
        valid_until=v_until,
        notes=req.notes.strip() if req.notes else (req.executive_summary.strip() if req.executive_summary else None),
        executive_summary=req.executive_summary.strip() if req.executive_summary else (req.notes.strip() if req.notes else None),
        scope_of_work=req.scope_of_work.strip() if req.scope_of_work else None,
        terms_and_conditions=req.terms_and_conditions.strip() if req.terms_and_conditions else None,
    )
    db.add(prop)
    await db.flush()

    for idx, it in enumerate(req.items, start=1):
        line_tot = it.item_total if it.item_total is not None else ((it.quantity * it.unit_price) - it.discount_amount)
        p_item = ProposalItem(
            proposal_id=prop.id,
            package_label=it.package_label,
            description=it.description,
            notes=it.notes,
            quantity=it.quantity,
            unit_price=it.unit_price,
            item_total=line_tot,
            display_order=idx,
        )
        db.add(p_item)

    guest_token = secrets.token_hex(24)
    p_token = ProposalToken(proposal_id=prop.id, token=guest_token)
    db.add(p_token)

    await db.commit()
    return {"success": True, "proposalId": str(prop.id), "token": guest_token, "proposalNumber": prop.proposal_number}


@router.put("/crm/proposals/{proposal_id}")
@router.patch("/crm/proposals/{proposal_id}")
async def update_proposal(
    proposal_id: uuid.UUID,
    req: UpdateProposalRequest,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    prop = await db.get(Proposal, proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if req.title is not None:
        prop.title = req.title.strip()
    if req.currency is not None:
        prop.currency = req.currency.strip().upper()
    if req.status is not None:
        prop.status = req.status.strip().upper()
    if req.deal_id is not None:
        try:
            prop.deal_id = uuid.UUID(req.deal_id) if req.deal_id.strip() else None
        except Exception:
            pass
    if req.client_id is not None:
        try:
            prop.client_id = uuid.UUID(req.client_id) if req.client_id.strip() else None
        except Exception:
            pass
    if req.valid_until is not None:
        try:
            val = req.valid_until.strip()
            if val:
                prop.valid_until = date.fromisoformat(val.split("T")[0])
            else:
                prop.valid_until = None
        except Exception:
            pass
    if req.notes is not None:
        prop.executive_summary = req.notes.strip() if req.notes else None
    if req.executive_summary is not None:
        prop.executive_summary = req.executive_summary.strip() if req.executive_summary else None
    if req.scope_of_work is not None:
        prop.scope_of_work = req.scope_of_work.strip() if req.scope_of_work else None
    if req.terms_and_conditions is not None:
        prop.terms_and_conditions = req.terms_and_conditions.strip() if req.terms_and_conditions else None

    # Update line items if provided
    if req.items is not None:
        await db.execute(delete(ProposalItem).where(ProposalItem.proposal_id == prop.id))
        subtotal = Decimal("0.00")
        for idx, it in enumerate(req.items, start=1):
            line_tot = it.item_total if it.item_total is not None else ((it.quantity * it.unit_price) - it.discount_amount)
            subtotal += line_tot
            p_item = ProposalItem(
                proposal_id=prop.id,
                package_label=it.package_label,
                description=it.description,
                notes=it.notes,
                quantity=it.quantity,
                unit_price=it.unit_price,
                item_total=line_tot,
                display_order=idx,
            )
            db.add(p_item)
        prop.subtotal = subtotal

    prop.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(prop)

    token_q = select(ProposalToken.token).where(ProposalToken.proposal_id == prop.id)
    token = (await db.execute(token_q)).scalar_one_or_none()
    if not token:
        token = secrets.token_hex(24)
        db.add(ProposalToken(proposal_id=prop.id, token=token))
        await db.commit()

    return {
        "success": True,
        "message": "Proposal updated successfully",
        "proposalId": str(prop.id),
        "publicToken": token,
    }


@router.delete("/crm/proposals/{proposal_id}")
async def delete_proposal(
    proposal_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    prop = await db.get(Proposal, proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")
    await db.delete(prop)
    await db.commit()
    return {"success": True, "message": "Proposal deleted successfully"}


@router.post("/crm/proposals/{proposal_id}/send")
async def send_proposal(
    proposal_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    prop = await db.get(Proposal, proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")
    prop.status = "SENT"
    prop.sent_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "message": "Proposal marked as sent"}


@router.post("/crm/proposals/{proposal_id}/convert")
async def convert_proposal_to_invoice(
    proposal_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    prop = await db.get(Proposal, proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    items_q = select(ProposalItem).where(ProposalItem.proposal_id == proposal_id)
    items = (await db.execute(items_q)).scalars().all()

    now = datetime.utcnow()
    doc_number = f"INV-{now.strftime('%y%m')}-{secrets.randbelow(9000)+1000}"

    doc = Document(
        shop_id=prop.shop_id,
        client_id=prop.client_id,
        type="INVOICE",
        doc_number=doc_number,
        status="DRAFT",
        currency=prop.currency,
        sub_total=prop.subtotal,
        tax_amount=Decimal("0.00"),
        grand_total=prop.subtotal,
        notes=f"Converted from Proposal #{prop.proposal_number}",
        issue_date=now,
    )
    db.add(doc)
    await db.flush()

    for it in items:
        di = DocumentItem(
            document_id=doc.id,
            description=it.description,
            quantity=it.quantity,
            unit_price=it.unit_price,
            tax_type="EXEMPT",
            tax_amount=Decimal("0.00"),
            item_total=it.item_total,
        )
        db.add(di)

    prop.converted_document_id = doc.id
    await db.commit()
    return {"success": True, "documentId": str(doc.id), "docNumber": doc.doc_number}


@router.post("/crm/proposals/public/{token}/view")
async def record_public_proposal_view(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    token_q = select(ProposalToken).where(ProposalToken.token == token)
    pt = (await db.execute(token_q)).scalar_one_or_none()
    if not pt:
        raise HTTPException(status_code=404, detail="Proposal token not found")

    prop = await db.get(Proposal, pt.proposal_id)
    if prop:
        prop.view_count = (prop.view_count or 0) + 1
        prop.viewed_at = datetime.utcnow()
        if prop.status == "SENT":
            prop.status = "VIEWED"
        await db.commit()

    return {"success": True, "message": "Proposal view recorded"}


@router.get("/crm/proposals/public/{token}")
async def get_public_proposal(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    token_q = select(ProposalToken).where(ProposalToken.token == token)
    pt = (await db.execute(token_q)).scalar_one_or_none()
    if not pt:
        raise HTTPException(status_code=404, detail="Proposal token not found")

    prop = await db.get(Proposal, pt.proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    items_q = select(ProposalItem).where(ProposalItem.proposal_id == prop.id)
    items = (await db.execute(items_q)).scalars().all()

    shop = await db.get(Shop, prop.shop_id)
    client = await db.get(Client, prop.client_id) if prop.client_id else None

    proposal_data = {
        "id": str(prop.id),
        "proposalNumber": prop.proposal_number,
        "title": prop.title,
        "status": prop.status,
        "subtotal": float(prop.subtotal),
        "currency": prop.currency,
        "validUntil": prop.valid_until.isoformat() if prop.valid_until else None,
        "executiveSummary": prop.executive_summary,
        "termsConditions": prop.terms_and_conditions,
        "isAmendmentRequested": bool(prop.amendment_notes),
        "amendmentNotes": prop.amendment_notes,
        "signedAt": prop.responded_at.isoformat() if prop.responded_at else None,
        "signedByName": prop.signer_name,
        "items": [
            {
                "id": str(it.id),
                "tier": it.package_label or "STANDARD",
                "packageLabel": it.package_label,
                "description": it.description,
                "notes": it.notes,
                "quantity": float(it.quantity),
                "unitPrice": float(it.unit_price),
                "itemTotal": float(it.item_total),
                "isOptional": False,
                "isSelected": True,
            }
            for it in items
        ],
        "shop": {
            "name": shop.name if shop else "",
            "currency": shop.currency if shop else "KES",
            "logoUrl": shop.logo_url if shop else None,
            "email": shop.email if shop else None,
            "phone": shop.phone if shop else None,
        } if shop else None,
        "client": {
            "name": client.name if client else "",
            "email": client.email if client else None,
        } if client else None,
    }
    return {"success": True, "proposal": proposal_data}


@router.post("/crm/proposals/public/{token}/accept")
async def accept_public_proposal(
    token: str,
    payload: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
):
    token_q = select(ProposalToken).where(ProposalToken.token == token)
    pt = (await db.execute(token_q)).scalar_one_or_none()
    if not pt:
        raise HTTPException(status_code=404, detail="Proposal token not found")

    prop = await db.get(Proposal, pt.proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    prop.status = "ACCEPTED"
    prop.responded_at = datetime.utcnow()
    if payload:
        if payload.get("signer_name") or payload.get("signerName"):
            prop.signer_name = payload.get("signer_name") or payload.get("signerName")
        if payload.get("signature_data") or payload.get("signatureData"):
            prop.signature_data_url = payload.get("signature_data") or payload.get("signatureData")
    await db.commit()
    return {"success": True, "message": "Proposal accepted successfully"}


# -------------------------------------------------------------------------
# CONTRACTS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/crm/contracts")
async def list_contracts(
    shop_id: Optional[str] = Query(None),
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "contracts": []}

    q = select(Contract).where(Contract.shop_id == sid).order_by(desc(Contract.created_at))
    res = await db.execute(q)
    contracts = res.scalars().all()
    return {"success": True, "contracts": contracts}


@router.post("/crm/contracts")
async def create_contract(
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, req.get("shop_id"))
    if not sid:
        raise HTTPException(status_code=400, detail="Shop ID required")

    cid = uuid.UUID(req["client_id"]) if req.get("client_id") else None
    deal_id = uuid.UUID(req["deal_id"]) if req.get("deal_id") else None
    now = datetime.utcnow()
    c_num = req.get("contract_number") or f"CTR-{now.strftime('%y%m')}-{secrets.randbelow(9000)+1000}"

    s_date = datetime.strptime(req["start_date"][:10], "%Y-%m-%d").date() if req.get("start_date") else now.date()
    e_date = datetime.strptime(req["end_date"][:10], "%Y-%m-%d").date() if req.get("end_date") else None

    contract = Contract(
        shop_id=sid,
        client_id=cid,
        deal_id=deal_id,
        contract_number=c_num,
        title=(req.get("title") or "Contract").strip(),
        description=req.get("description"),
        currency=(req.get("currency") or "KES").upper(),
        monthly_fee=Decimal(str(req.get("monthly_fee") or 0)),
        is_retainer_hours=bool(req.get("is_retainer_hours", False)),
        monthly_hours_allocated=Decimal(str(req.get("monthly_hours_allocated") or 0)),
        hourly_rate=Decimal(str(req.get("hourly_rate") or 0)),
        start_date=s_date,
        end_date=e_date,
        status="ACTIVE",
    )
    db.add(contract)
    await db.commit()
    await db.refresh(contract)
    return {"success": True, "contract": contract}


@router.get("/crm/contracts/{contract_id}")
async def get_contract(
    contract_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Contract, contract_id)
    if not c:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    # fetch logged hours
    h_q = select(ContractHoursLog).where(ContractHoursLog.contract_id == contract_id).order_by(desc(ContractHoursLog.log_date))
    hours = (await db.execute(h_q)).scalars().all()
    return {"success": True, "contract": c, "hours": hours}


@router.get("/crm/contracts/{contract_id}/burndown")
async def get_contract_burndown(
    contract_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Contract, contract_id)
    if not c:
        raise HTTPException(status_code=404, detail="Contract not found")

    h_q = (
        select(
            ContractHoursLog.billing_month,
            func.sum(ContractHoursLog.hours_used).label("total_hours"),
        )
        .where(ContractHoursLog.contract_id == contract_id)
        .group_by(ContractHoursLog.billing_month)
        .order_by(desc(ContractHoursLog.billing_month))
    )
    res = await db.execute(h_q)
    rows = res.all()

    allocated = float(c.monthly_hours_allocated)
    history = [
        {
            "month": r[0],
            "hoursUsed": float(r[1]),
            "hoursAllocated": allocated,
            "variance": allocated - float(r[1]),
        }
        for r in rows
    ]
    return {
        "success": True,
        "contractId": str(c.id),
        "isRetainerHours": c.is_retainer_hours,
        "allocatedPerMonth": allocated,
        "burndownHistory": history,
    }


@router.post("/crm/contracts/{contract_id}/hours")
async def log_contract_hours(
    contract_id: uuid.UUID,
    req: LogContractHoursRequest,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Contract, contract_id)
    if not c:
        raise HTTPException(status_code=404, detail="Contract not found")

    user_id = auth.id if hasattr(auth, "id") else None
    l_date = date.fromisoformat(req.log_date) if req.log_date else date.today()

    entry = ContractHoursLog(
        contract_id=contract_id,
        shop_id=c.shop_id,
        logged_by_user_id=user_id,
        billing_month=req.billing_month,
        hours_used=req.hours_used,
        description=req.description,
        log_date=l_date,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {"success": True, "log": entry}


# -------------------------------------------------------------------------
# PROJECTS, MILESTONES & TIMESHEETS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/projects")
@router.get("/crm/projects")
async def list_projects(
    shop_id: Optional[str] = Query(None),
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "projects": []}

    q = select(Project).where(Project.shop_id == sid).order_by(desc(Project.created_at))
    res = await db.execute(q)
    projects = res.scalars().all()
    return {"success": True, "projects": projects}


@router.post("/projects")
@router.post("/crm/projects")
async def create_project(
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, req.get("shop_id"))
    if not sid:
        raise HTTPException(status_code=400, detail="Shop ID required")

    cid = uuid.UUID(req["client_id"]) if req.get("client_id") else None
    deal_id = uuid.UUID(req["deal_id"]) if req.get("deal_id") else None
    contract_id = uuid.UUID(req["contract_id"]) if req.get("contract_id") else None
    now = datetime.utcnow()
    p_code = req.get("project_code") or req.get("code") or f"PRJ-{now.strftime('%y%m')}-{secrets.randbelow(9000)+1000}"

    s_date = datetime.strptime(req["start_date"][:10], "%Y-%m-%d").date() if req.get("start_date") else now.date()
    e_date = datetime.strptime(req["end_date"][:10], "%Y-%m-%d").date() if req.get("end_date") else None

    proj = Project(
        shop_id=sid,
        client_id=cid,
        deal_id=deal_id,
        contract_id=contract_id,
        project_code=p_code,
        name=(req.get("name") or "Project").strip(),
        description=req.get("description"),
        currency=(req.get("currency") or "KES").upper(),
        budget_type="FIXED",
        budget_hours=Decimal(str(req.get("budget_hours") or 0)),
        budget_amount=Decimal(str(req.get("budget_amount") or 0)),
        start_date=s_date,
        end_date=e_date,
        status="PLANNING",
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return {"success": True, "project": proj}


@router.get("/projects/{project_id}")
@router.get("/crm/projects/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # milestones
    m_q = select(ProjectMilestone).where(ProjectMilestone.project_id == project_id).order_by(ProjectMilestone.display_order)
    milestones = (await db.execute(m_q)).scalars().all()

    # timesheets
    t_q = select(Timesheet).where(Timesheet.project_id == project_id).order_by(desc(Timesheet.work_date))
    timesheets = (await db.execute(t_q)).scalars().all()

    return {"success": True, "project": proj, "milestones": milestones, "timesheets": timesheets}


@router.post("/crm/projects/{project_id}/milestones")
async def create_project_milestone(
    project_id: uuid.UUID,
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    amount = Decimal(str(req.get("amount", 0)))
    due_date = date.fromisoformat(req["due_date"]) if req.get("due_date") else None

    milestone = ProjectMilestone(
        project_id=project_id,
        shop_id=proj.shop_id,
        title=req["title"].strip(),
        description=req.get("description"),
        amount=amount,
        currency=proj.currency,
        due_date=due_date,
        status="PENDING",
    )
    db.add(milestone)
    await db.commit()
    await db.refresh(milestone)
    return {"success": True, "milestone": milestone}


@router.patch("/crm/projects/{project_id}/milestones/{milestone_id}/status")
async def update_milestone_status(
    project_id: uuid.UUID,
    milestone_id: uuid.UUID,
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    m = await db.get(ProjectMilestone, milestone_id)
    if not m:
        raise HTTPException(status_code=404, detail="Milestone not found")
    
    m.status = req.get("status", "COMPLETED")
    if m.status == "COMPLETED":
        m.completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(m)
    return {"success": True, "milestone": m}


@router.post("/crm/projects/{project_id}/milestones/{milestone_id}/convert")
async def convert_milestone_to_invoice(
    project_id: uuid.UUID,
    milestone_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    m = await db.get(ProjectMilestone, milestone_id)
    proj = await db.get(Project, project_id)
    if not m or not proj:
        raise HTTPException(status_code=404, detail="Milestone or Project not found")

    now = datetime.utcnow()
    doc_num = f"INV-MLS-{now.strftime('%y%m')}-{secrets.randbelow(9000)+1000}"

    doc = Document(
        shop_id=proj.shop_id,
        client_id=proj.client_id,
        type="INVOICE",
        doc_number=doc_num,
        status="DRAFT",
        currency=m.currency,
        sub_total=m.amount,
        tax_amount=Decimal("0.00"),
        grand_total=m.amount,
        notes=f"Milestone Billing: {m.title} [{proj.name}]",
        issue_date=now,
    )
    db.add(doc)
    await db.flush()

    di = DocumentItem(
        document_id=doc.id,
        description=f"{proj.name} - Milestone: {m.title}",
        quantity=Decimal("1.00"),
        unit_price=m.amount,
        tax_type="EXEMPT",
        tax_amount=Decimal("0.00"),
        item_total=m.amount,
    )
    db.add(di)

    m.status = "INVOICED"
    m.invoiced_document_id = doc.id
    await db.commit()
    return {"success": True, "documentId": str(doc.id), "docNumber": doc.doc_number}


@router.post("/crm/projects/{project_id}/timesheets")
async def create_timesheet_entry(
    project_id: uuid.UUID,
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    user_id = auth.id if hasattr(auth, "id") else None
    hours = Decimal(str(req.get("hours_logged", req.get("hours", 1))))
    rate = Decimal(str(req.get("billable_rate", 0)))
    w_date = date.fromisoformat(req["work_date"]) if req.get("work_date") else date.today()

    ts = Timesheet(
        project_id=project_id,
        shop_id=proj.shop_id,
        user_id=user_id,
        work_date=w_date,
        hours_logged=hours,
        task_description=req.get("task_description", req.get("description", "Work item")),
        is_billable=bool(req.get("is_billable", True)),
        billable_rate=rate,
        billable_amount=hours * rate,
        status="DRAFT",
    )
    db.add(ts)
    await db.commit()
    await db.refresh(ts)
    return {"success": True, "timesheet": ts}


@router.post("/crm/projects/{project_id}/timesheets/{ts_id}/submit")
async def submit_timesheet(
    project_id: uuid.UUID,
    ts_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    ts = await db.get(Timesheet, ts_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    ts.status = "SUBMITTED"
    await db.commit()
    return {"success": True, "message": "Timesheet submitted for approval"}


@router.post("/crm/projects/{project_id}/timesheets/{ts_id}/review")
async def review_timesheet(
    project_id: uuid.UUID,
    ts_id: uuid.UUID,
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    ts = await db.get(Timesheet, ts_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    
    decision = req.get("action", req.get("status", "APPROVED")).upper()
    ts.status = "APPROVED" if decision == "APPROVED" else "REJECTED"
    ts.review_notes = req.get("notes") or req.get("reason")
    await db.commit()
    return {"success": True, "status": ts.status}


@router.post("/crm/projects/{project_id}/timesheets/invoice")
async def invoice_approved_timesheets(
    project_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    q = select(Timesheet).where(
        and_(
            Timesheet.project_id == project_id,
            Timesheet.status == "APPROVED",
            Timesheet.invoiced_document_id.is_(None),
            Timesheet.is_billable == True,
        )
    )
    timesheets = (await db.execute(q)).scalars().all()
    if not timesheets:
        return {"success": False, "message": "No approved, unbilled timesheets found."}

    now = datetime.utcnow()
    total_amount = sum(t.billable_amount for t in timesheets)
    doc_num = f"INV-TS-{now.strftime('%y%m')}-{secrets.randbelow(9000)+1000}"

    doc = Document(
        shop_id=proj.shop_id,
        client_id=proj.client_id,
        type="INVOICE",
        doc_number=doc_num,
        status="DRAFT",
        currency=proj.currency,
        sub_total=total_amount,
        tax_amount=Decimal("0.00"),
        grand_total=total_amount,
        notes=f"Timesheet Billing for {proj.name}",
        issue_date=now,
    )
    db.add(doc)
    await db.flush()

    for ts in timesheets:
        di = DocumentItem(
            document_id=doc.id,
            description=f"[{ts.work_date}] {ts.task_description} ({ts.hours_logged} hrs @ {ts.billable_rate})",
            quantity=ts.hours_logged,
            unit_price=ts.billable_rate,
            tax_type="EXEMPT",
            tax_amount=Decimal("0.00"),
            item_total=ts.billable_amount,
        )
        db.add(di)
        ts.invoiced_document_id = doc.id

    await db.commit()
    return {"success": True, "documentId": str(doc.id), "docNumber": doc.doc_number, "billedCount": len(timesheets)}
