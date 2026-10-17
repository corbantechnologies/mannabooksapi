import logging
import uuid
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, field_validator
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

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
from app.models.expense import Expense, Income
from app.modules.auth.service import verify_and_get_session

from app.core.config import get_settings
from app.api.deps import get_current_user, verify_internal_api_key, get_principal, Principal
from app.core.datetime_utils import to_naive_utc
from app.core.datetime_utils import utcnow
from app.core.schemas import ApiModel

settings = get_settings()


logger = logging.getLogger("mannabooks.operations")

router = APIRouter(tags=["Operations, Manufacturing, Budgets & Loyalty"])
inventory_router = APIRouter(prefix="/inventory", tags=["Inventory Management"], dependencies=[Depends(verify_internal_api_key)])


async def _verify_auth(principal: Principal = Depends(get_principal)):
    return principal


async def _resolve_shop_id(db: AsyncSession, shop_id: Optional[str] = None, shop_slug: Optional[str] = None) -> Optional[uuid.UUID]:
    if shop_id:
        try:
            return uuid.UUID(shop_id)
        except ValueError:
            pass
    if shop_slug:
        q = select(Shop.id).where(Shop.slug == shop_slug)
        res = await db.execute(q)
        sid = res.scalar_one_or_none()
        if sid:
            return sid
    return None



# -------------------------------------------------------------------------
# Request Models
# -------------------------------------------------------------------------
class CreateBudgetRequest(ApiModel):
    shop_id: str
    account_id: str
    month: Optional[int] = None
    year: Optional[int] = None
    monthly_limit: Optional[Decimal] = None
    # Backward compatibility
    fiscal_year_id: Optional[str] = None
    period: str = "ANNUAL"
    budget_amount: Optional[Decimal] = None
    notes: Optional[str] = None


class CreateCostCenterRequest(ApiModel):
    shop_id: str
    code: str
    name: str
    department: Optional[str] = None
    description: Optional[str] = None
    manager_id: Optional[str] = None
    budget_amount: Decimal = Decimal("0.00")


class CreateBomItemRequest(ApiModel):
    raw_material_product_id: str
    quantity_required: Decimal
    wastage_percentage: Decimal = Decimal("0.00")


class CreateBomRequest(ApiModel):
    shop_id: str
    finished_product_id: str
    name: str
    labor_cost_estimate: Decimal = Decimal("0.00")
    overhead_cost_estimate: Decimal = Decimal("0.00")
    items: List[CreateBomItemRequest]


class CreateProductionOrderRequest(ApiModel):
    shop_id: str
    bom_id: str
    target_location_id: str
    units_to_produce: Decimal
    total_cost_kes: Decimal = Decimal("0.00")
    notes: Optional[str] = None


class LoyaltyProgramUpdateRequest(ApiModel):
    shop_id: str
    name: str = "Customer Rewards"
    points_per_kes: Optional[Decimal] = Decimal("0.01")
    earn_rate_kes: Optional[Decimal] = None  # KES spent per point; preferred over points_per_kes
    kes_per_point: Decimal = Decimal("1.00")
    min_redeem_points: int = 100
    points_expiry_days: Optional[int] = 365  # empty / 0: points never expire
    is_active: bool = True


class AdjustLoyaltyPointsRequest(ApiModel):
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
    shop_id: Optional[str] = Query(None, description="Workspace ID"),
    shop_slug: Optional[str] = Query(None, description="Workspace slug"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "budgets": []}

    q = select(Budget).where(Budget.shop_id == sid)
    if year:
        q = q.where(Budget.year == year)
    if month:
        q = q.where(Budget.month == month)
    q = q.order_by(desc(Budget.created_at))
    res = await db.execute(q)
    budgets = res.scalars().all()

    # Budget vs actual per expense account for the selected month (actuals from the general ledger).
    import calendar
    from datetime import datetime as _dt
    from app.models.account import ChartOfAccount
    from app.models.journal import JournalEntry

    now = utcnow()
    y, m = year or now.year, month or now.month
    lo = _dt(y, m, 1)
    hi = _dt(y, m, calendar.monthrange(y, m)[1], 23, 59, 59)
    accounts = (
        await db.execute(
            select(ChartOfAccount)
            .where(ChartOfAccount.shop_id == sid, ChartOfAccount.account_type == "EXPENSE")
            .order_by(ChartOfAccount.code)
        )
    ).scalars().all()
    debits = dict(
        (await db.execute(
            select(JournalEntry.debit_account_id, func.coalesce(func.sum(JournalEntry.amount), 0))
            .where(JournalEntry.shop_id == sid, JournalEntry.entry_date >= lo, JournalEntry.entry_date <= hi)
            .group_by(JournalEntry.debit_account_id)
        )).all()
    )
    credits = dict(
        (await db.execute(
            select(JournalEntry.credit_account_id, func.coalesce(func.sum(JournalEntry.amount), 0))
            .where(JournalEntry.shop_id == sid, JournalEntry.entry_date >= lo, JournalEntry.entry_date <= hi)
            .group_by(JournalEntry.credit_account_id)
        )).all()
    )
    month_budgets = {b.account_id: b for b in budgets if b.year == y and b.month == m}
    lines = []
    for acc in accounts:
        actual = Decimal(debits.get(acc.id, 0)) - Decimal(credits.get(acc.id, 0))
        b = month_budgets.get(acc.id)
        limit = Decimal(b.monthly_limit) if b else None
        pct = float((actual / limit * 100).quantize(Decimal("0.1"))) if limit else None
        lines.append({
            "accountId": str(acc.id),
            "accountCode": acc.code,
            "accountName": acc.name,
            "budgetId": str(b.id) if b else None,
            "monthlyLimit": float(limit) if limit is not None else None,
            "actual": float(actual),
            "percentUsed": pct,
            "isOverBudget": bool(limit is not None and actual > limit),
            "isWarning": bool(pct is not None and 80 <= pct < 100),
        })
    return {"success": True, "budgets": budgets, "lines": lines, "month": m, "year": y}


@router.post("/operations/budgets")
async def create_budget(
    req: CreateBudgetRequest,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    now = utcnow()
    m_val = req.month if req.month is not None else now.month
    y_val = req.year if req.year is not None else now.year
    limit_val = req.monthly_limit if req.monthly_limit is not None else (req.budget_amount or Decimal("0.00"))
    if limit_val < 0:
        raise HTTPException(status_code=400, detail="Budget amount cannot be negative")
    if not (1 <= m_val <= 12) or not (2000 <= y_val <= 2100):
        raise HTTPException(status_code=400, detail="Invalid budget month or year")

    from app.models.account import ChartOfAccount
    shop_uuid = uuid.UUID(req.shop_id)
    account = await db.get(ChartOfAccount, uuid.UUID(req.account_id))
    if not account or account.shop_id != shop_uuid:
        raise HTTPException(status_code=400, detail="Account not found in this workspace")

    # One budget per account per month: update the existing one instead of duplicating it.
    budget = (
        await db.execute(
            select(Budget).where(
                Budget.shop_id == shop_uuid, Budget.account_id == account.id, Budget.month == m_val, Budget.year == y_val
            )
        )
    ).scalars().first()
    if budget:
        budget.monthly_limit = limit_val
    else:
        budget = Budget(
            shop_id=shop_uuid,
            account_id=account.id,
            month=m_val,
            year=y_val,
            monthly_limit=limit_val,
        )
        db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return {"success": True, "budget": budget}


@router.post("/operations/budgets/clone-previous")
async def clone_previous_budget(
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    shop_id = uuid.UUID(req["shop_id"]) if isinstance(req.get("shop_id"), str) else req.get("shop_id")
    target_month = int(req.get("month", 1))
    target_year = int(req.get("year", 2026))

    if target_month == 1:
        prev_month = 12
        prev_year = target_year - 1
    else:
        prev_month = target_month - 1
        prev_year = target_year

    q = select(Budget).where(
        and_(
            Budget.shop_id == shop_id,
            Budget.month == prev_month,
            Budget.year == prev_year,
        )
    )
    res = await db.execute(q)
    prev_budgets = res.scalars().all()

    cloned = 0
    for pb in prev_budgets:
        check_q = select(Budget).where(
            and_(
                Budget.shop_id == shop_id,
                Budget.account_id == pb.account_id,
                Budget.month == target_month,
                Budget.year == target_year,
            )
        )
        if not (await db.execute(check_q)).scalar_one_or_none():
            nb = Budget(
                shop_id=shop_id,
                account_id=pb.account_id,
                month=target_month,
                year=target_year,
                monthly_limit=pb.monthly_limit,
            )
            db.add(nb)
            cloned += 1

    await db.commit()
    return {"success": True, "clonedCount": cloned, "message": f"Successfully cloned {cloned} budget line items."}


# -------------------------------------------------------------------------
# COST CENTERS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/operations/cost-centers")
async def list_cost_centers(
    shop_id: Optional[str] = Query(None, description="Workspace ID"),
    shop_slug: Optional[str] = Query(None, description="Workspace slug"),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "costCenters": []}

    q = select(CostCenter).where(CostCenter.shop_id == sid).order_by(desc(CostCenter.created_at))
    res = await db.execute(q)
    ccs = res.scalars().all()
    return {"success": True, "costCenters": ccs}


@router.post("/operations/cost-centers")
async def create_cost_center(
    req: CreateCostCenterRequest,
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, req.shop_id, shop_slug)
    if not sid:
        raise HTTPException(status_code=400, detail="Invalid shop ID or slug")

    dept = req.department or req.description
    cc = CostCenter(
        shop_id=sid,
        code=req.code.strip().upper(),
        name=req.name.strip(),
        department=dept.strip() if dept else None,
        is_active=True,
    )
    db.add(cc)
    await db.commit()
    await db.refresh(cc)
    return {"success": True, "costCenter": cc}


@router.patch("/operations/cost-centers/{cost_center_id}")
async def update_cost_center(
    cost_center_id: uuid.UUID,
    req: dict,
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    cc = await db.get(CostCenter, cost_center_id)
    if not cc:
        raise HTTPException(status_code=404, detail="Cost center not found")

    if "code" in req and req["code"]:
        cc.code = req["code"].strip().upper()
    if "name" in req and req["name"]:
        cc.name = req["name"].strip()
    if "department" in req:
        cc.department = req["department"]
    if "isActive" in req or "is_active" in req:
        val = req.get("isActive") if "isActive" in req else req.get("is_active")
        cc.is_active = bool(val)

    await db.commit()
    await db.refresh(cc)
    return {"success": True, "costCenter": cc}


@router.delete("/operations/cost-centers/{cost_center_id}")
async def delete_cost_center(
    cost_center_id: uuid.UUID,
    shop_slug: Optional[str] = Query(None),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    cc = await db.get(CostCenter, cost_center_id)
    if not cc:
        raise HTTPException(status_code=404, detail="Cost center not found")
    cc.is_active = False
    await db.commit()
    return {"success": True, "message": "Cost center archived successfully"}


# -------------------------------------------------------------------------
# BOM & PRODUCTION ORDERS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/operations/bom")
@router.get("/operations/boms")
async def list_boms(
    shop_id: Optional[str] = Query(None, description="Workspace ID"),
    shop_slug: Optional[str] = Query(None, description="Workspace slug"),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "boms": []}

    q = select(BillOfMaterials).where(BillOfMaterials.shop_id == sid).order_by(desc(BillOfMaterials.created_at))
    res = await db.execute(q)
    boms = res.scalars().all()
    return {"success": True, "boms": boms}


@router.post("/operations/bom")
@router.post("/operations/boms")
async def create_bom(
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    shop_id = uuid.UUID(req["shop_id"]) if isinstance(req.get("shop_id"), str) else req.get("shop_id")
    finished_prod_id = uuid.UUID(req["finished_product_id"]) if isinstance(req.get("finished_product_id"), str) else req.get("finished_product_id")
    name = req.get("name", "Manufacturing Recipe").strip()
    labor = Decimal(str(req.get("labor_cost_estimate", 0)))
    overhead = Decimal(str(req.get("overhead_cost_estimate", 0)))

    bom = BillOfMaterials(
        shop_id=shop_id,
        finished_product_id=finished_prod_id,
        name=name,
        labor_cost_estimate=labor,
        overhead_cost_estimate=overhead,
        is_active=True,
    )
    db.add(bom)
    await db.flush()

    for it in req.get("items", []):
        raw_id = uuid.UUID(it["raw_material_product_id"]) if isinstance(it.get("raw_material_product_id"), str) else it.get("raw_material_product_id")
        qty = Decimal(str(it.get("quantity_required", 1)))
        wastage = Decimal(str(it.get("wastage_percentage", 0)))
        b_item = BomItem(
            bom_id=bom.id,
            raw_material_product_id=raw_id,
            quantity_required=qty,
            wastage_percentage=wastage,
        )
        db.add(b_item)

    await db.commit()
    await db.refresh(bom)
    return {"success": True, "bomId": str(bom.id), "bom": bom}


@router.post("/operations/boms/run")
async def run_bom_production(
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    shop_id = uuid.UUID(req["shop_id"]) if isinstance(req.get("shop_id"), str) else req.get("shop_id")
    bom_id = uuid.UUID(req["bom_id"]) if isinstance(req.get("bom_id"), str) else req.get("bom_id")
    units = Decimal(str(req.get("units_to_produce", 1)))
    target_loc_id = uuid.UUID(req["target_location_id"]) if req.get("target_location_id") else None
    notes = req.get("notes")

    bom = await db.get(BillOfMaterials, bom_id)
    if not bom or bom.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="BOM not found")

    q_items = select(BomItem).where(BomItem.bom_id == bom_id)
    b_items = (await db.execute(q_items)).scalars().all()

    total_materials_cost = Decimal("0.00")
    for bi in b_items:
        prod = await db.get(Product, bi.raw_material_product_id)
        cost = prod.cost_price if prod else Decimal("0.00")
        qty_needed = bi.quantity_required * units * (Decimal("1.00") + (bi.wastage_percentage / Decimal("100.00")))
        total_materials_cost += cost * qty_needed

        if target_loc_id:
            ledger_entry = StockLedger(
                shop_id=shop_id,
                product_id=bi.raw_material_product_id,
                location_id=target_loc_id,
                movement_type="ADJUSTMENT_OUT",
                quantity=-abs(qty_needed),
                unit_cost=cost,
                notes=f"Consumed in Production Run for {bom.name}",
            )
            db.add(ledger_entry)

    total_cost = total_materials_cost + (bom.labor_cost_estimate * units) + (bom.overhead_cost_estimate * units)
    unit_cost = (total_cost / units).quantize(Decimal("0.01")) if units > 0 else Decimal("0.00")

    fin_prod = await db.get(Product, bom.finished_product_id)
    if fin_prod:
        fin_prod.stock_quantity += units
        if target_loc_id:
            fin_entry = StockLedger(
                shop_id=shop_id,
                product_id=bom.finished_product_id,
                location_id=target_loc_id,
                movement_type="PURCHASE_RECEIPT",
                quantity=units,
                unit_cost=unit_cost,
                notes=f"Finished goods produced from BOM {bom.name}",
            )
            db.add(fin_entry)

    order_num = f"PRD-{utcnow().strftime('%y%m%d')}-{secrets.randbelow(9000)+1000}"
    po = ProductionOrder(
        shop_id=shop_id,
        bom_id=bom_id,
        order_number=order_num,
        target_location_id=target_loc_id or uuid.uuid4(),
        units_to_produce=units,
        total_cost_kes=total_cost,
        status="COMPLETED",
        completed_at=utcnow(),
        notes=notes,
    )
    db.add(po)
    await db.commit()
    await db.refresh(po)

    return {
        "success": True,
        "productionOrderId": str(po.id),
        "orderNumber": po.order_number,
        "unitProductionCost": float(unit_cost),
        "totalCost": float(total_cost),
    }


# -------------------------------------------------------------------------
# STOCKTAKES ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/operations/stocktakes")
async def list_stocktakes(
    shop_id: Optional[str] = Query(None, description="Workspace ID"),
    shop_slug: Optional[str] = Query(None, description="Workspace slug"),
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    sid = await _resolve_shop_id(db, shop_id, shop_slug)
    if not sid:
        return {"success": True, "stocktakes": []}
    q = select(Stocktake).where(Stocktake.shop_id == sid).order_by(desc(Stocktake.created_at))
    res = await db.execute(q)
    return {"success": True, "stocktakes": res.scalars().all()}


@router.post("/operations/stocktakes")
async def create_stocktake(
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    shop_id = uuid.UUID(req["shop_id"]) if isinstance(req.get("shop_id"), str) else req.get("shop_id")
    loc_id = uuid.UUID(req["location_id"]) if req.get("location_id") else None

    if not loc_id:
        loc_q = select(StockLocation.id).where(StockLocation.shop_id == shop_id).limit(1)
        loc_id = (await db.execute(loc_q)).scalar_one_or_none() or uuid.uuid4()

    shop_obj = await db.get(Shop, shop_id)
    user_id = shop_obj.owner_id if shop_obj else ((await db.execute(select(User.id).limit(1))).scalar_one_or_none() or uuid.uuid4())

    num = f"STK-{utcnow().strftime('%y%m%d')}-{secrets.randbelow(9000)+1000}"
    stk = Stocktake(
        shop_id=shop_id,
        location_id=loc_id,
        stocktake_number=num,
        status="COUNTING",
        conducted_by_user_id=user_id,
    )
    db.add(stk)
    await db.commit()
    await db.refresh(stk)
    return {"success": True, "stocktake": stk, "id": str(stk.id)}


@router.patch("/operations/stocktakes/{stocktake_id}/items/{item_id}")
async def update_stocktake_item(
    stocktake_id: uuid.UUID,
    item_id: uuid.UUID,
    req: dict,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    stk = await db.get(Stocktake, stocktake_id)
    if not stk:
        raise HTTPException(status_code=404, detail="Stocktake not found")

    counted_qty = Decimal(str(req.get("countedQuantity") or req.get("counted_quantity") or 0))
    notes = req.get("notes")

    q = select(StocktakeItem).where(
        and_(
            StocktakeItem.stocktake_id == stocktake_id,
            StocktakeItem.product_id == item_id,
        )
    )
    si = (await db.execute(q)).scalar_one_or_none()

    prod = await db.get(Product, item_id)
    book_qty = prod.stock_quantity if prod else Decimal("0.00")
    cost = prod.cost_price if prod else Decimal("0.00")
    variance = counted_qty - book_qty
    var_cost = variance * cost

    if not si:
        si = StocktakeItem(
            stocktake_id=stocktake_id,
            product_id=item_id,
            book_quantity=book_qty,
            counted_quantity=counted_qty,
            variance_quantity=variance,
            variance_cost_kes=var_cost,
            notes=notes,
        )
        db.add(si)
    else:
        si.counted_quantity = counted_qty
        si.variance_quantity = variance
        si.variance_cost_kes = var_cost
        if notes:
            si.notes = notes

    await db.commit()
    await db.refresh(si)
    return {"success": True, "stocktakeItem": si}


@router.post("/operations/stocktakes/{stocktake_id}/reconcile")
async def reconcile_stocktake(
    stocktake_id: uuid.UUID,
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):
    stk = await db.get(Stocktake, stocktake_id)
    if not stk:
        raise HTTPException(status_code=404, detail="Stocktake not found")

    q = select(StocktakeItem).where(StocktakeItem.stocktake_id == stocktake_id)
    items = (await db.execute(q)).scalars().all()

    for item in items:
        if item.variance_quantity != 0:
            prod = await db.get(Product, item.product_id)
            if prod:
                prod.stock_quantity = item.counted_quantity
            entry = StockLedger(
                shop_id=stk.shop_id,
                product_id=item.product_id,
                location_id=stk.location_id,
                movement_type="COUNT_CORRECTION" if item.variance_quantity > 0 else "ADJUSTMENT_OUT",
                quantity=item.variance_quantity,
                unit_cost=item.variance_cost_kes / item.variance_quantity if item.variance_quantity else Decimal("0.00"),
                notes=f"Stocktake reconciliation #{stk.stocktake_number}",
            )
            db.add(entry)

    stk.status = "COMPLETED"
    stk.completed_at = utcnow()
    await db.commit()
    await db.refresh(stk)
    return {"success": True, "stocktake": stk, "message": "Stocktake reconciled and ledger adjusted."}



@router.get("/operations/production-orders")
async def list_production_orders(
    shop_id: str = Query(..., description="Workspace ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = current_user
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(ProductionOrder).where(ProductionOrder.shop_id == shop_id).order_by(desc(ProductionOrder.created_at))
    res = await db.execute(q)
    orders = res.scalars().all()
    return {"success": True, "orders": orders}


@router.post("/operations/production-orders")
async def create_production_order(
    req: CreateProductionOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = current_user
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = utcnow()
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
    auth: Any = Depends(_verify_auth),
    db: AsyncSession = Depends(get_db),
):

    sid = uuid.UUID(shop_id) if isinstance(shop_id, str) and len(shop_id) == 36 else shop_id
    program = None
    tiers = []

    try:
        q_prog = select(LoyaltyProgram).where(LoyaltyProgram.shop_id == sid)
        res_prog = await db.execute(q_prog)
        program = res_prog.scalar_one_or_none()
    except Exception as e:
        logger.warning("Loyalty Query Warning: %s", e)

    try:
        q_tiers = select(MembershipTier).where(MembershipTier.shop_id == sid).order_by(MembershipTier.display_order)
        res_tiers = await db.execute(q_tiers)
        tiers = res_tiers.scalars().all()
    except Exception as e:
        logger.warning("Membership Tier Query Warning: %s", e)

    return {"success": True, "program": program, "tiers": tiers}


@router.post("/operations/loyalty/program")
async def update_loyalty_program(
    req: LoyaltyProgramUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = current_user
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    shop_uuid = uuid.UUID(str(req.shop_id))
    q = select(LoyaltyProgram).where(LoyaltyProgram.shop_id == shop_uuid)
    res = await db.execute(q)
    prog = res.scalar_one_or_none()

    # The screen sends points earned per KES (e.g. 0.01); the program stores KES spent per point (e.g. 100).
    if req.earn_rate_kes is not None and req.earn_rate_kes > 0:
        earn_rate = req.earn_rate_kes
    elif req.points_per_kes and req.points_per_kes > 0:
        earn_rate = (Decimal("1") / req.points_per_kes).quantize(Decimal("0.01"))
    else:
        earn_rate = Decimal("100.00")

    if not prog:
        prog = LoyaltyProgram(
            shop_id=shop_uuid,
            program_name=req.name,
            earn_rate_kes=earn_rate,
            point_value_kes=req.kes_per_point,
            min_redeem_points=req.min_redeem_points,
            points_expiry_days=req.points_expiry_days or 0,
            is_enabled=req.is_active,
            engine_mode="POINTS_ONLY" if req.is_active else "OFF",
        )
        db.add(prog)
    else:
        prog.program_name = req.name
        prog.earn_rate_kes = earn_rate
        prog.point_value_kes = req.kes_per_point
        prog.min_redeem_points = req.min_redeem_points
        prog.points_expiry_days = req.points_expiry_days or 0
        prog.is_enabled = req.is_active
        if not req.is_active:
            prog.engine_mode = "OFF"
        elif prog.engine_mode == "OFF":
            prog.engine_mode = "POINTS_ONLY"  # keep a mode chosen in settings (tiers / hybrid)

    await db.commit()
    return {"success": True, "program": prog}


class CreateTierRequest(ApiModel):
    shop_id: str
    name: str
    min_points: int = 0
    discount_percentage: Decimal = Decimal("0.00")
    perks_description: Optional[str] = None
    display_order: int = 0


@router.post("/operations/loyalty/tiers")
async def create_loyalty_tier(req: CreateTierRequest, db: AsyncSession = Depends(get_db)):
    tier = MembershipTier(
        shop_id=uuid.UUID(req.shop_id),
        name=req.name,
        min_spend_kes=Decimal(req.min_points),
        discount_percent=req.discount_percentage,
        points_multiplier=Decimal("1.00"),
        badge_color="#000000",
        display_order=req.display_order,
    )
    db.add(tier)
    await db.commit()
    await db.refresh(tier)
    return {
        "success": True,
        "data": {
            "id": str(tier.id),
            "name": tier.name,
            "minSpendKes": str(tier.min_spend_kes),
            "discountPercent": str(tier.discount_percent),
            "pointsMultiplier": str(tier.points_multiplier),
            "badgeColor": tier.badge_color or "#000000",
            "displayOrder": tier.display_order,
        }
    }


@router.delete("/operations/loyalty/tiers/{tier_id}")
async def delete_loyalty_tier(tier_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    q = select(MembershipTier).where(MembershipTier.id == tier_id)
    res = await db.execute(q)
    tier = res.scalar_one_or_none()
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")
    await db.delete(tier)
    await db.commit()
    return {"success": True}


@router.post("/operations/loyalty/adjust")
async def adjust_loyalty_points(
    req: AdjustLoyaltyPointsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = current_user
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get or create client loyalty account
    q_acc = select(ClientLoyaltyAccount).where(
        ClientLoyaltyAccount.shop_id == uuid.UUID(str(req.shop_id)),
        ClientLoyaltyAccount.client_id == uuid.UUID(str(req.client_id)),
    )
    res_acc = await db.execute(q_acc)
    acc = res_acc.scalar_one_or_none()

    if not acc:
        mem_num = f"MEM-{secrets.token_hex(4).upper()}"
        acc = ClientLoyaltyAccount(
            shop_id=uuid.UUID(req.shop_id),
            client_id=uuid.UUID(req.client_id),
            member_number=mem_num,
            current_points=0,
            lifetime_points=0,
        )
        db.add(acc)
        await db.flush()

    new_balance = acc.current_points + req.points
    if new_balance < 0:
        return {"success": False, "error": f"Insufficient loyalty points (current: {acc.current_points}, requested: {abs(req.points)})"}

    acc.current_points = new_balance
    if req.points > 0:
        acc.lifetime_points += req.points

    m_type = req.movement_type.upper() if req.movement_type else "MANUAL_ADJUST"
    ledger_entry = LoyaltyLedger(
        shop_id=uuid.UUID(req.shop_id),
        account_id=acc.id,
        source_document_id=uuid.UUID(req.source_document_id) if req.source_document_id else None,
        movement_type=m_type,
        points_delta=req.points,
        running_points_balance=new_balance,
        notes=req.reason,
        created_by_id=user.id,
    )
    db.add(ledger_entry)
    await db.commit()

    return {
        "success": True,
        "currentPoints": new_balance,
        "account": {
            "id": str(acc.id),
            "memberNumber": acc.member_number,
            "currentPoints": new_balance,
            "lifetimePoints": acc.lifetime_points,
        }
    }


async def _tier_summary(db: AsyncSession, acc: ClientLoyaltyAccount) -> Optional[dict]:
    """The member's tier as the sales screens use it (discount and points multiplier)."""
    tier = await db.get(MembershipTier, acc.tier_id) if acc.tier_id else None
    if not tier:
        return None
    return {
        "id": str(tier.id),
        "name": tier.name,
        "discountPercent": str(tier.discount_percent),
        "pointsMultiplier": str(tier.points_multiplier),
        "badgeColor": tier.badge_color,
    }


class PhoneLoyaltyRequest(ApiModel):
    shop_id: str
    phone: str
    customer_name: Optional[str] = None


@router.get("/operations/loyalty/account")
async def get_client_loyalty_account_endpoint(
    shop_id: uuid.UUID = Query(...),
    client_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    q_acc = select(ClientLoyaltyAccount).where(
        ClientLoyaltyAccount.shop_id == shop_id,
        ClientLoyaltyAccount.client_id == client_id,
    )
    res_acc = await db.execute(q_acc)
    acc = res_acc.scalar_one_or_none()
    if not acc:
        acc = ClientLoyaltyAccount(
            shop_id=shop_id,
            client_id=client_id,
            member_number=f"MEM-{secrets.token_hex(3).upper()}",
            current_points=0,
            lifetime_points=0,
            wallet_balance_kes=Decimal("0.00"),
        )
        db.add(acc)
        await db.commit()
        await db.refresh(acc)

    return {
        "success": True,
        "data": {
            "id": str(acc.id),
            "memberNumber": acc.member_number,
            "currentPoints": acc.current_points,
            "walletBalanceKes": str(acc.wallet_balance_kes or "0.00"),
            "tier": await _tier_summary(db, acc),
        }
    }


@router.post("/operations/loyalty/account/by-phone")
async def find_or_create_loyalty_account_by_phone_endpoint(
    req: PhoneLoyaltyRequest,
    db: AsyncSession = Depends(get_db),
):
    import re
    clean = re.sub(r"\s+", "", req.phone.strip())
    try:
        s_uuid = uuid.UUID(req.shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    from app.models.client import Client
    q_c = select(Client).where(Client.shop_id == s_uuid, Client.phone == clean)
    client = (await db.execute(q_c)).scalar_one_or_none()

    if not client:
        name = req.customer_name or f"Customer {clean[-4:]}"
        client = Client(
            shop_id=s_uuid,
            name=name,
            phone=clean,
            email="",
            client_type="WALK_IN",
            requires_etims=False,
        )
        db.add(client)
        await db.flush()

    q_acc = select(ClientLoyaltyAccount).where(
        ClientLoyaltyAccount.shop_id == s_uuid,
        ClientLoyaltyAccount.client_id == client.id,
    )
    acc = (await db.execute(q_acc)).scalar_one_or_none()
    if not acc:
        acc = ClientLoyaltyAccount(
            shop_id=s_uuid,
            client_id=client.id,
            member_number=f"MEM-{secrets.token_hex(3).upper()}",
            current_points=0,
            lifetime_points=0,
            wallet_balance_kes=Decimal("0.00"),
        )
        db.add(acc)
        await db.commit()
        await db.refresh(acc)
    else:
        await db.commit()

    return {
        "success": True,
        "data": {
            "client": {
                "id": str(client.id),
                "name": client.name,
                "phone": client.phone,
                "email": client.email,
                "clientType": client.client_type,
            },
            "loyaltyAccount": {
                "id": str(acc.id),
                "memberNumber": acc.member_number,
                "currentPoints": acc.current_points,
                "walletBalanceKes": str(acc.wallet_balance_kes or "0.00"),
                "tier": await _tier_summary(db, acc),
            }
        }
    }


# Expenses & other income live in app/modules/cashbook.


# ==========================================
# INVENTORY ROUTER (SSR & DUAL MOUNT)
# ==========================================
@inventory_router.get("/boms")
async def get_inventory_boms(shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    q = select(BillOfMaterials).where(BillOfMaterials.shop_id == shop_id)
    res = await db.execute(q)
    boms = res.scalars().all()
    return {"boms": boms, "data": boms}


@inventory_router.get("/locations")
async def get_inventory_locations(shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    q = select(StockLocation).where(StockLocation.shop_id == shop_id, StockLocation.is_active == True)
    res = await db.execute(q)
    locs = res.scalars().all()
    return [
        {
            "id": str(l.id),
            "shopId": str(l.shop_id),
            "name": l.name,
            "code": l.code,
            "isDefault": l.is_default,
            "isActive": l.is_active,
        }
        for l in locs
    ]


@inventory_router.get("/locations/{location_id}")
async def get_inventory_location_by_id(location_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    loc = await db.get(StockLocation, location_id)
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    return {
        "id": str(loc.id),
        "shopId": str(loc.shop_id),
        "name": loc.name,
        "code": loc.code,
        "isDefault": loc.is_default,
        "isActive": loc.is_active,
    }


@inventory_router.get("/stocktakes/{stocktake_id}")
async def get_inventory_stocktake_by_id(stocktake_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    stk = await db.get(Stocktake, stocktake_id)
    if not stk:
        raise HTTPException(status_code=404, detail="Stocktake not found")
    return stk


@inventory_router.get("/production-orders")
async def get_inventory_production_orders(shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    q = select(ProductionOrder).where(ProductionOrder.shop_id == shop_id)
    res = await db.execute(q)
    orders = res.scalars().all()
    return {"orders": orders, "data": orders}


@inventory_router.get("/reports/expiry")
async def get_inventory_reports_expiry(shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    return {"batches": [], "riskItems": [], "summary": {"expired": 0, "expiringSoon": 0}}


@inventory_router.get("/bins")
async def get_inventory_bins(
    shop_id: uuid.UUID = Query(...),
    location_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.operations import StorageBin
    q = select(StorageBin).where(StorageBin.shop_id == shop_id)
    if location_id:
        q = q.where(StorageBin.location_id == location_id)
    res = await db.execute(q)
    bins = res.scalars().all()
    return [
        {
            "id": str(b.id),
            "shopId": str(b.shop_id),
            "locationId": str(b.location_id),
            "zone": b.zone,
            "rack": b.rack,
            "shelf": b.shelf,
            "binCode": b.bin_code,
            "capacityVolume": float(b.capacity_volume) if b.capacity_volume else None,
            "maxWeight": float(b.max_weight) if b.max_weight else None,
            "isPickable": b.is_pickable,
            "createdAt": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bins
    ]


@inventory_router.post("/bins")
async def create_inventory_bin(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.operations import StorageBin
    sid = uuid.UUID(req["shop_id"]) if isinstance(req["shop_id"], str) else req["shop_id"]
    loc_id = uuid.UUID(req["location_id"]) if isinstance(req["location_id"], str) else req["location_id"]
    bin_obj = StorageBin(
        shop_id=sid,
        location_id=loc_id,
        zone=(req.get("zone") or "A").strip(),
        rack=req.get("rack"),
        shelf=req.get("shelf"),
        bin_code=(req.get("bin_code") or req.get("binCode") or "BIN-01").strip(),
        capacity_volume=Decimal(str(req.get("capacity_volume") or 0)) if req.get("capacity_volume") else None,
        max_weight=Decimal(str(req.get("max_weight") or 0)) if req.get("max_weight") else None,
        is_pickable=bool(req.get("is_pickable", True)),
    )
    db.add(bin_obj)
    await db.commit()
    await db.refresh(bin_obj)
    return {"success": True, "bin": bin_obj}


@inventory_router.delete("/bins/{bin_id}")
async def delete_inventory_bin(
    bin_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.operations import StorageBin
    bin_obj = await db.get(StorageBin, bin_id)
    if not bin_obj:
        raise HTTPException(status_code=404, detail="Bin not found")
    await db.delete(bin_obj)
    await db.commit()
    return {"success": True, "message": "Storage bin deleted successfully"}


@inventory_router.post("/batches")
async def create_inventory_batch(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.operations import ProductBatch
    sid = uuid.UUID(req["shop_id"]) if isinstance(req["shop_id"], str) else req["shop_id"]
    pid = uuid.UUID(req["product_id"]) if isinstance(req["product_id"], str) else req["product_id"]
    loc_id = uuid.UUID(req["location_id"]) if isinstance(req["location_id"], str) else req["location_id"]
    bin_id = uuid.UUID(req["bin_id"]) if req.get("bin_id") else None

    exp_date = datetime.strptime(req["expiry_date"][:10], "%Y-%m-%d").date()
    mfg_date = datetime.strptime(req["manufacture_date"][:10], "%Y-%m-%d").date() if req.get("manufacture_date") else None
    qty = Decimal(str(req.get("quantity") or 0))
    cost = Decimal(str(req.get("cost_price") or 0))

    batch = ProductBatch(
        shop_id=sid,
        productId=pid,
        location_id=loc_id,
        bin_id=bin_id,
        batch_number=(req.get("batch_number") or req.get("batchNumber") or "BATCH-01").strip(),
        manufacture_date=mfg_date,
        expiry_date=exp_date,
        initial_quantity=qty,
        current_quantity=qty,
        cost_price=cost,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return {"success": True, "batch": batch}


@inventory_router.get("/transfers")
async def get_inventory_transfers(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if not shop_id:
        return []
    from app.models.inventory import StockLedger
    q = (
        select(StockLedger)
        .where(
            and_(
                StockLedger.shop_id == shop_id,
                StockLedger.movement_type.in_(["TRANSFER_OUT", "TRANSFER_IN"]),
            )
        )
        .order_by(desc(StockLedger.created_at))
        .limit(200)
    )
    res = await db.execute(q)
    return res.scalars().all()


@inventory_router.get("/transfers/{transfer_id}")
async def get_inventory_transfer_by_id(
    transfer_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.inventory import StockLedger
    ledger = await db.get(StockLedger, transfer_id)
    if not ledger:
        q = select(StockLedger).where(StockLedger.transfer_id == transfer_id)
        res = await db.execute(q)
        ledger = res.scalar_one_or_none()

    if not ledger:
        transfer_dict = {
            "id": str(transfer_id),
            "shopId": str(shop_id) if shop_id else "",
            "transferNumber": f"TRF-{str(transfer_id)[:8].upper()}",
            "status": "COMPLETED",
            "sourceLocation": {"id": "", "name": "Main Store"},
            "destinationLocation": {"id": "", "name": "Branch Store"},
            "items": [],
            "notes": "Stock transfer record",
            "createdAt": utcnow().isoformat(),
        }
        return {"success": True, "transfer": transfer_dict}

    transfer_dict = {
        "id": str(ledger.id),
        "shopId": str(ledger.shop_id),
        "transferNumber": f"TRF-{str(ledger.id)[:8].upper()}",
        "status": "COMPLETED",
        "movementType": ledger.movement_type,
        "quantity": float(ledger.quantity),
        "notes": ledger.notes or "",
        "items": [
            {
                "id": str(ledger.id),
                "productId": str(ledger.product_id),
                "productName": ledger.product.name if ledger.product else "Transferred Item",
                "quantity": float(ledger.quantity),
                "unitCost": float(ledger.unit_cost),
            }
        ],
        "createdAt": ledger.created_at.isoformat() if ledger.created_at else None,
    }
    return {"success": True, "transfer": transfer_dict}


