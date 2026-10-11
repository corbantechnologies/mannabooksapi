import uuid
import secrets
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.db.session import get_db
from app.models.identity import User, Shop
from app.models.client import Client
from app.models.crm import (
    Deal, DealActivity, Proposal, ProposalItem, ProposalToken,
    Contract, ContractHoursLog, Project, ProjectMember, Timesheet, ProjectMilestone
)
from app.modules.auth.service import verify_and_get_session

router = APIRouter(tags=["CRM, Proposals, Contracts & Projects"])


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
    product_id: Optional[str] = None
    description: str
    quantity: Decimal = Decimal("1.0000")
    unit_price: Decimal = Decimal("0.00")
    discount_amount: Decimal = Decimal("0.00")
    tax_type: str = "EXEMPT"
    tax_amount: Decimal = Decimal("0.00")
    item_total: Decimal = Decimal("0.00")


class CreateProposalRequest(BaseModel):
    shop_id: str
    deal_id: Optional[str] = None
    client_id: Optional[str] = None
    title: str
    currency: str = "KES"
    items: List[CreateProposalItemRequest]
    valid_until: Optional[str] = None
    notes: Optional[str] = None
    terms_and_conditions: Optional[str] = None


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
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Deal).where(Deal.shop_id == shop_id).order_by(desc(Deal.created_at))
    res = await db.execute(q)
    deals = res.scalars().all()
    return {"success": True, "deals": deals}


@router.post("/crm/deals")
async def create_deal(
    req: CreateDealRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    c_date = date.fromisoformat(req.close_date) if req.close_date else None
    deal = Deal(
        shop_id=uuid.UUID(req.shop_id),
        client_id=uuid.UUID(req.client_id) if req.client_id else None,
        assigned_user_id=user.id,
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
async def update_deal(
    deal_id: str,
    req: UpdateDealRequest,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Deal).where(Deal.id == deal_id, Deal.shop_id == shop_id)
    res = await db.execute(q)
    deal = res.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    if req.title is not None:
        deal.title = req.title.strip()
    if req.value is not None:
        deal.value = req.value
    if req.stage is not None:
        deal.stage = req.stage
    if req.probability is not None:
        deal.probability = req.probability
    if req.close_date is not None:
        deal.close_date = date.fromisoformat(req.close_date) if req.close_date else None
    if req.lost_reason is not None:
        deal.lost_reason = req.lost_reason.strip() if req.lost_reason else None

    await db.commit()
    return {"success": True}


# -------------------------------------------------------------------------
# PROPOSALS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/crm/proposals")
async def list_proposals(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Proposal).where(Proposal.shop_id == shop_id).order_by(desc(Proposal.created_at))
    res = await db.execute(q)
    proposals = res.scalars().all()
    return {"success": True, "proposals": proposals}


@router.post("/crm/proposals")
async def create_proposal(
    req: CreateProposalRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.utcnow()
    proposal_number = f"PROP-{now.strftime('%y%m')}-{secrets.randbelow(9000) + 1000}"

    subtotal = sum((it.quantity * it.unit_price) - it.discount_amount for it in req.items)
    tax_total = sum(it.tax_amount for it in req.items)
    grand_total = subtotal + tax_total

    v_until = date.fromisoformat(req.valid_until) if req.valid_until else None

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
        notes=req.notes.strip() if req.notes else None,
        terms_and_conditions=req.terms_and_conditions.strip() if req.terms_and_conditions else None,
    )
    db.add(prop)
    await db.flush()

    for idx, it in enumerate(req.items, start=1):
        p_item = ProposalItem(
            proposal_id=prop.id,
            description=it.description,
            quantity=it.quantity,
            unit_price=it.unit_price,
            item_total=it.item_total or (it.quantity * it.unit_price),
            display_order=idx,
        )
        db.add(p_item)

    # Generate public access token
    guest_token = secrets.token_hex(24)
    p_token = ProposalToken(proposal_id=prop.id, token=guest_token)
    db.add(p_token)

    await db.commit()
    return {"success": True, "proposalId": str(prop.id), "token": guest_token, "proposalNumber": prop.proposal_number}


# -------------------------------------------------------------------------
# PUBLIC GUEST PROPOSAL ACCESS (NO LOGIN REQUIRED)
# -------------------------------------------------------------------------
@router.get("/crm/proposals/public/{token}")
async def get_public_proposal(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    q_token = select(ProposalToken).where(ProposalToken.token == token)
    res_token = await db.execute(q_token)
    p_token = res_token.scalar_one_or_none()
    if not p_token:
        raise HTTPException(status_code=404, detail="Proposal not found or link expired")

    q_prop = select(Proposal, Shop).join(Shop, Proposal.shop_id == Shop.id).where(Proposal.id == p_token.proposal_id)
    res_prop = await db.execute(q_prop)
    row = res_prop.first()
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    prop, shop = row

    q_items = select(ProposalItem).where(ProposalItem.proposal_id == prop.id)
    res_items = await db.execute(q_items)
    items = res_items.scalars().all()

    return {
        "success": True,
        "proposal": {
            "id": str(prop.id),
            "proposalNumber": prop.proposal_number,
            "title": prop.title,
            "currency": prop.currency,
            "subtotal": float(prop.subtotal),
            "taxTotal": float(prop.tax_total),
            "grandTotal": float(prop.grand_total),
            "status": prop.status,
            "validUntil": prop.valid_until.isoformat() if prop.valid_until else None,
            "notes": prop.notes,
            "termsAndConditions": prop.terms_and_conditions,
        },
        "shop": {
            "name": shop.name,
            "currency": shop.currency,
            "phone": shop.phone,
            "email": shop.email,
        },
        "items": [
            {
                "description": it.description,
                "quantity": float(it.quantity),
                "unitPrice": float(it.unit_price),
                "itemTotal": float(it.item_total),
            }
            for it in items
        ],
    }


@router.post("/crm/proposals/public/{token}/accept")
async def accept_public_proposal(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    q_token = select(ProposalToken).where(ProposalToken.token == token)
    res_token = await db.execute(q_token)
    p_token = res_token.scalar_one_or_none()
    if not p_token:
        raise HTTPException(status_code=404, detail="Proposal link expired")

    q_prop = select(Proposal).where(Proposal.id == p_token.proposal_id)
    res_prop = await db.execute(q_prop)
    prop = res_prop.scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="Proposal not found")

    prop.status = "ACCEPTED"
    prop.accepted_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "message": "Proposal accepted successfully"}


# -------------------------------------------------------------------------
# CONTRACTS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/crm/contracts")
async def list_contracts(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Contract).where(Contract.shop_id == shop_id).order_by(desc(Contract.created_at))
    res = await db.execute(q)
    contracts = res.scalars().all()
    return {"success": True, "contracts": contracts}


@router.post("/crm/contracts")
async def create_contract(
    req: CreateContractRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.utcnow()
    contract_number = f"CNT-{now.strftime('%y%m')}-{secrets.randbelow(9000) + 1000}"

    s_date = date.fromisoformat(req.start_date)
    e_date = date.fromisoformat(req.end_date) if req.end_date else None

    contract = Contract(
        shop_id=uuid.UUID(req.shop_id),
        client_id=uuid.UUID(req.client_id) if req.client_id else None,
        deal_id=uuid.UUID(req.deal_id) if req.deal_id else None,
        contract_number=contract_number,
        title=req.title.strip(),
        currency=req.currency.upper(),
        status="ACTIVE",
        monthly_fee=req.monthly_fee,
        is_retainer_hours=req.is_retainer_hours,
        monthly_hours_allocated=req.monthly_hours_allocated,
        hourly_rate=req.hourly_rate,
        start_date=s_date,
        end_date=e_date,
        terms_and_conditions=req.terms_and_conditions.strip() if req.terms_and_conditions else None,
    )
    db.add(contract)
    await db.commit()
    await db.refresh(contract)
    return {"success": True, "contract": contract}


# -------------------------------------------------------------------------
# PROJECTS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/crm/projects")
async def list_projects(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Project).where(Project.shop_id == shop_id).order_by(desc(Project.created_at))
    res = await db.execute(q)
    projects = res.scalars().all()
    return {"success": True, "projects": projects}


@router.post("/crm/projects")
async def create_project(
    req: CreateProjectRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.utcnow()
    project_code = f"PRJ-{now.strftime('%y%m')}-{secrets.randbelow(9000) + 1000}"

    s_date = date.fromisoformat(req.start_date) if req.start_date else None
    e_date = date.fromisoformat(req.end_date) if req.end_date else None

    proj = Project(
        shop_id=uuid.UUID(req.shop_id),
        client_id=uuid.UUID(req.client_id) if req.client_id else None,
        contract_id=uuid.UUID(req.contract_id) if req.contract_id else None,
        project_code=project_code,
        name=req.name.strip(),
        description=req.description.strip() if req.description else None,
        currency=req.currency.upper(),
        status="ACTIVE",
        budget_type=req.budget_type,
        budget_amount=req.budget_amount,
        budget_hours=req.budget_hours,
        start_date=s_date,
        end_date=e_date,
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return {"success": True, "project": proj}
