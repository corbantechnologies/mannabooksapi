import uuid
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.db.session import get_db
from app.models.identity import User, Shop
from app.models.client import Client
from app.models.item import Product
from app.models.inventory import StockLocation, StockLedger
from app.models.operations import (
    Budget, CostCenter, BillOfMaterials, BomItem, ProductionOrder,
    Stocktake, StocktakeItem, LoyaltyProgram, MembershipTier,
    ClientLoyaltyAccount, LoyaltyLedger
)
from app.modules.auth.service import verify_and_get_session

router = APIRouter(tags=["Operations, Manufacturing, Budgets & Loyalty"])


# -------------------------------------------------------------------------
# Request Models
# -------------------------------------------------------------------------
class CreateBudgetRequest(BaseModel):
    shop_id: str
    account_id: str
    fiscal_year_id: Optional[str] = None
    period: str = "ANNUAL"
    budget_amount: Decimal
    notes: Optional[str] = None


class CreateCostCenterRequest(BaseModel):
    shop_id: str
    code: str
    name: str
    description: Optional[str] = None
    manager_id: Optional[str] = None
    budget_amount: Decimal = Decimal("0.00")


class CreateBomItemRequest(BaseModel):
    raw_material_product_id: str
    quantity_required: Decimal
    wastage_percentage: Decimal = Decimal("0.00")


class CreateBomRequest(BaseModel):
    shop_id: str
    finished_product_id: str
    name: str
    labor_cost_estimate: Decimal = Decimal("0.00")
    overhead_cost_estimate: Decimal = Decimal("0.00")
    items: List[CreateBomItemRequest]


class CreateProductionOrderRequest(BaseModel):
    shop_id: str
    bom_id: str
    target_location_id: str
    units_to_produce: Decimal
    total_cost_kes: Decimal = Decimal("0.00")
    notes: Optional[str] = None


class LoyaltyProgramUpdateRequest(BaseModel):
    shop_id: str
    name: str = "Customer Rewards"
    points_per_kes: Decimal = Decimal("0.01")
    kes_per_point: Decimal = Decimal("1.00")
    min_redeem_points: int = 100
    points_expiry_days: int = 365
    is_active: bool = True


class AdjustLoyaltyPointsRequest(BaseModel):
    shop_id: str
    client_id: str
    points: int  # Positive for earn, negative for redeem
    movement_type: str  # EARN, REDEEM, MANUAL_ADJUST
    reason: Optional[str] = None
    source_document_id: Optional[str] = None


# -------------------------------------------------------------------------
# BUDGETS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/operations/budgets")
async def list_budgets(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Budget).where(Budget.shop_id == shop_id).order_by(desc(Budget.created_at))
    res = await db.execute(q)
    budgets = res.scalars().all()
    return {"success": True, "budgets": budgets}


@router.post("/operations/budgets")
async def create_budget(
    req: CreateBudgetRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    budget = Budget(
        shop_id=uuid.UUID(req.shop_id),
        account_id=uuid.UUID(req.account_id),
        fiscal_year_id=uuid.UUID(req.fiscal_year_id) if req.fiscal_year_id else None,
        period=req.period,
        budget_amount=req.budget_amount,
        actual_amount=Decimal("0.00"),
        notes=req.notes.strip() if req.notes else None,
    )
    db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return {"success": True, "budget": budget}


# -------------------------------------------------------------------------
# COST CENTERS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/operations/cost-centers")
async def list_cost_centers(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(CostCenter).where(CostCenter.shop_id == shop_id).order_by(desc(CostCenter.created_at))
    res = await db.execute(q)
    ccs = res.scalars().all()
    return {"success": True, "costCenters": ccs}


@router.post("/operations/cost-centers")
async def create_cost_center(
    req: CreateCostCenterRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    cc = CostCenter(
        shop_id=uuid.UUID(req.shop_id),
        code=req.code.strip().upper(),
        name=req.name.strip(),
        description=req.description.strip() if req.description else None,
        manager_id=uuid.UUID(req.manager_id) if req.manager_id else None,
        budget_amount=req.budget_amount,
        is_active=True,
    )
    db.add(cc)
    await db.commit()
    await db.refresh(cc)
    return {"success": True, "costCenter": cc}


# -------------------------------------------------------------------------
# BOM & PRODUCTION ORDERS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/operations/bom")
async def list_boms(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(BillOfMaterials).where(BillOfMaterials.shop_id == shop_id).order_by(desc(BillOfMaterials.created_at))
    res = await db.execute(q)
    boms = res.scalars().all()
    return {"success": True, "boms": boms}


@router.post("/operations/bom")
async def create_bom(
    req: CreateBomRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    bom = BillOfMaterials(
        shop_id=uuid.UUID(req.shop_id),
        finished_product_id=uuid.UUID(req.finished_product_id),
        name=req.name.strip(),
        labor_cost_estimate=req.labor_cost_estimate,
        overhead_cost_estimate=req.overhead_cost_estimate,
        is_active=True,
    )
    db.add(bom)
    await db.flush()

    for it in req.items:
        b_item = BomItem(
            bom_id=bom.id,
            raw_material_product_id=uuid.UUID(it.raw_material_product_id),
            quantity_required=it.quantity_required,
            wastage_percentage=it.wastage_percentage,
        )
        db.add(b_item)

    await db.commit()
    return {"success": True, "bomId": str(bom.id)}


@router.get("/operations/production-orders")
async def list_production_orders(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(ProductionOrder).where(ProductionOrder.shop_id == shop_id).order_by(desc(ProductionOrder.created_at))
    res = await db.execute(q)
    orders = res.scalars().all()
    return {"success": True, "orders": orders}


@router.post("/operations/production-orders")
async def create_production_order(
    req: CreateProductionOrderRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.utcnow()
    order_number = f"PRD-{now.strftime('%y%m')}-{secrets.randbelow(9000) + 1000}"

    po = ProductionOrder(
        shop_id=uuid.UUID(req.shop_id),
        bom_id=uuid.UUID(req.bom_id),
        order_number=order_number,
        target_location_id=uuid.UUID(req.target_location_id),
        units_to_produce=req.units_to_produce,
        total_cost_kes=req.total_cost_kes,
        status="DRAFT",
        notes=req.notes.strip() if req.notes else None,
    )
    db.add(po)
    await db.commit()
    await db.refresh(po)
    return {"success": True, "order": po}


# -------------------------------------------------------------------------
# CUSTOMER LOYALTY PROGRAM & WALLETS
# -------------------------------------------------------------------------
@router.get("/operations/loyalty/program")
async def get_loyalty_program(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q_prog = select(LoyaltyProgram).where(LoyaltyProgram.shop_id == shop_id)
    res_prog = await db.execute(q_prog)
    program = res_prog.scalar_one_or_none()

    q_tiers = select(MembershipTier).where(MembershipTier.shop_id == shop_id).order_by(MembershipTier.display_order)
    res_tiers = await db.execute(q_tiers)
    tiers = res_tiers.scalars().all()

    return {"success": True, "program": program, "tiers": tiers}


@router.post("/operations/loyalty/program")
async def update_loyalty_program(
    req: LoyaltyProgramUpdateRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(LoyaltyProgram).where(LoyaltyProgram.shop_id == req.shop_id)
    res = await db.execute(q)
    prog = res.scalar_one_or_none()

    if not prog:
        prog = LoyaltyProgram(
            shop_id=uuid.UUID(req.shop_id),
            name=req.name,
            points_per_kes=req.points_per_kes,
            kes_per_point=req.kes_per_point,
            min_redeem_points=req.min_redeem_points,
            points_expiry_days=req.points_expiry_days,
            is_active=req.is_active,
        )
        db.add(prog)
    else:
        prog.name = req.name
        prog.points_per_kes = req.points_per_kes
        prog.kes_per_point = req.kes_per_point
        prog.min_redeem_points = req.min_redeem_points
        prog.points_expiry_days = req.points_expiry_days
        prog.is_active = req.is_active

    await db.commit()
    return {"success": True, "program": prog}


@router.post("/operations/loyalty/adjust")
async def adjust_loyalty_points(
    req: AdjustLoyaltyPointsRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get or create client loyalty account
    q_acc = select(ClientLoyaltyAccount).where(
        ClientLoyaltyAccount.shop_id == req.shop_id,
        ClientLoyaltyAccount.client_id == req.client_id
    )
    res_acc = await db.execute(q_acc)
    acc = res_acc.scalar_one_or_none()

    if not acc:
        acc = ClientLoyaltyAccount(
            shop_id=uuid.UUID(req.shop_id),
            client_id=uuid.UUID(req.client_id),
            current_points=0,
            lifetime_points_earned=0,
            lifetime_points_redeemed=0,
        )
        db.add(acc)
        await db.flush()

    new_balance = acc.current_points + req.points
    if new_balance < 0:
        return {"success": False, "error": f"Insufficient loyalty points (current: {acc.current_points}, requested: {abs(req.points)})"}

    acc.current_points = new_balance
    if req.points > 0:
        acc.lifetime_points_earned += req.points
    else:
        acc.lifetime_points_redeemed += abs(req.points)

    ledger_entry = LoyaltyLedger(
        shop_id=uuid.UUID(req.shop_id),
        account_id=acc.id,
        source_document_id=uuid.UUID(req.source_document_id) if req.source_document_id else None,
        movement_type=req.movement_type,
        points=req.points,
        running_balance=new_balance,
        reason=req.reason,
    )
    db.add(ledger_entry)
    await db.commit()

    return {"success": True, "currentPoints": new_balance}
