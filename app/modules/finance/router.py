import uuid
from decimal import Decimal
from typing import Optional, List, Any, Dict
from datetime import date, datetime
from app.core.datetime_utils import to_naive_utc
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, asc, func

from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.identity import Shop
from app.modules.finance.schemas import (
    TaxSettingsResponse,
    TaxSettingsDTO,
    UpdateTaxSettingsRequest,
    CreateFixedAssetRequest,
    DisposeFixedAssetRequest,
    CapitalAllowancesRequest,
    GenerateInstalmentsRequest,
    PayInstalmentRequest,
)
from app.modules.finance.tax_service import (
    list_fixed_assets,
    create_fixed_asset,
    dispose_fixed_asset,
    run_capital_allowances,
    compute_tax_schedule,
    compute_turnover_tax,
    list_tax_instalments,
    generate_tax_instalments,
    pay_tax_instalment,
)
from app.core.datetime_utils import utcnow

router = APIRouter(prefix="/finance", tags=["Finance & Tax"], dependencies=[Depends(verify_internal_api_key)])
tax_router = APIRouter(prefix="/tax", tags=["Statutory Tax Engine"], dependencies=[Depends(verify_internal_api_key)])


def _build_tax_response(shop: Shop) -> dict:
    cit_rate = float(shop.cit_rate) if shop.cit_rate is not None else 30.00
    est_profit = float(shop.estimated_annual_profit) if shop.estimated_annual_profit is not None else 0.00
    
    dto = TaxSettingsDTO(
        isCitActive=bool(shop.is_cit_active),
        isTotActive=bool(shop.is_tot_active),
        citRate=cit_rate,
        estimatedAnnualProfit=est_profit,
        taxPin=shop.tax_pin,
        isVatRegistered=bool(shop.is_vat_registered),
        vatNumber=shop.vat_number,
        taxRegime=shop.tax_regime or "EXEMPT",
    )
    return {
        "success": True,
        "shop_id": str(shop.id),
        "isCitActive": dto.isCitActive,
        "isTotActive": dto.isTotActive,
        "citRate": dto.citRate,
        "estimatedAnnualProfit": dto.estimatedAnnualProfit,
        "taxPin": dto.taxPin,
        "isVatRegistered": dto.isVatRegistered,
        "vatNumber": dto.vatNumber,
        "taxRegime": dto.taxRegime,
        "settings": dto.model_dump(),
    }


def _serialize_asset(a) -> dict:
    return {
        "id": str(a.id),
        "name": a.name,
        "assetClass": a.asset_class,
        "asset_class": a.asset_class,
        "purchaseDate": a.purchase_date.isoformat() if a.purchase_date else "",
        "purchaseCost": str(a.purchase_cost),
        "taxWdv": str(a.tax_wdv),
        "scrapValue": str(a.scrap_value),
        "isDisposed": a.is_disposed,
        "disposalDate": a.disposal_date.isoformat() if a.disposal_date else None,
        "disposalProceeds": str(a.disposal_proceeds) if a.disposal_proceeds is not None else None,
    }


def _serialize_instalment(inst) -> dict:
    return {
        "id": str(inst.id),
        "instalmentNumber": inst.instalment_number,
        "instalment_number": inst.instalment_number,
        "year": inst.year,
        "dueDate": inst.due_date.isoformat() if inst.due_date else "",
        "due_date": inst.due_date.isoformat() if inst.due_date else "",
        "estimatedAmount": str(inst.estimated_amount),
        "paidAmount": str(inst.paid_amount),
        "paidAt": inst.paid_at.isoformat() if inst.paid_at else None,
        "paymentReference": inst.payment_reference,
        "status": inst.status,
    }


# ==========================================
# 1. TAX SETTINGS
# ==========================================

@router.get("/tax/settings")
async def get_tax_settings_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return _build_tax_response(shop)


@router.post("/tax/settings")
@router.put("/tax/settings")
@router.patch("/tax/settings")
async def update_tax_settings_endpoint(
    req: UpdateTaxSettingsRequest,
    db: AsyncSession = Depends(get_db),
):
    shop = await db.get(Shop, req.shop_id)
    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    cit_active = req.isCitActive if req.isCitActive is not None else req.is_cit_active
    if cit_active is not None:
        shop.is_cit_active = cit_active

    tot_active = req.isTotActive if req.isTotActive is not None else req.is_tot_active
    if tot_active is not None:
        shop.is_tot_active = tot_active

    cit_r = req.citRate if req.citRate is not None else req.cit_rate
    if cit_r is not None:
        shop.cit_rate = Decimal(str(cit_r))

    est_p = req.estimatedAnnualProfit if req.estimatedAnnualProfit is not None else req.estimated_annual_profit
    if est_p is not None:
        shop.estimated_annual_profit = Decimal(str(est_p))

    t_pin = req.taxPin if req.taxPin is not None else req.tax_pin
    if t_pin is not None:
        shop.tax_pin = t_pin.strip() or None

    vat_reg = req.isVatRegistered if req.isVatRegistered is not None else req.is_vat_registered
    if vat_reg is not None:
        shop.is_vat_registered = vat_reg

    vat_num = req.vatNumber if req.vatNumber is not None else req.vat_number
    if vat_num is not None:
        shop.vat_number = vat_num.strip() or None

    regime = req.taxRegime if req.taxRegime is not None else req.tax_regime
    if regime is not None:
        shop.tax_regime = regime.strip()

    await db.commit()
    await db.refresh(shop)

    resp = _build_tax_response(shop)
    resp["message"] = "Tax settings updated successfully."
    return resp


# ==========================================
# 2. FIXED ASSETS
# ==========================================

@router.get("/fixed-assets")
@tax_router.get("/fixed-assets")
async def get_fixed_assets_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    assets = await list_fixed_assets(db, shop_id)
    serialized = [_serialize_asset(a) for a in assets]
    return {"assets": serialized, "data": serialized}


@router.post("/fixed-assets")
@tax_router.post("/fixed-assets")
async def create_fixed_asset_endpoint(
    req: CreateFixedAssetRequest,
    db: AsyncSession = Depends(get_db),
):
    asset_cls = req.assetClass or req.asset_class or "CLASS_4"
    p_date = req.purchaseDate or req.purchase_date or date.today()
    p_cost = req.purchaseCost or req.purchase_cost or Decimal("0.00")
    s_val = req.scrapValue if req.scrapValue is not None else (req.scrap_value or Decimal("0.00"))

    asset = await create_fixed_asset(
        db=db,
        shop_id=req.shop_id,
        name=req.name,
        asset_class=asset_cls,
        purchase_date=p_date,
        purchase_cost=p_cost,
        scrap_value=s_val,
    )
    return {"success": True, "asset": _serialize_asset(asset)}


@router.post("/fixed-assets/{asset_id}/dispose")
@tax_router.post("/fixed-assets/{asset_id}/dispose")
async def dispose_fixed_asset_endpoint(
    asset_id: uuid.UUID,
    req: DisposeFixedAssetRequest,
    db: AsyncSession = Depends(get_db),
):
    d_date = req.disposalDate or req.disposal_date or date.today()
    d_proceeds = req.disposalProceeds if req.disposalProceeds is not None else (req.disposal_proceeds or Decimal("0.00"))

    asset = await dispose_fixed_asset(
        db=db,
        shop_id=req.shop_id,
        asset_id=asset_id,
        disposal_date=d_date,
        disposal_proceeds=d_proceeds,
    )
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    return {"success": True, "asset": _serialize_asset(asset)}


@router.post("/fixed-assets/capital-allowances")
@tax_router.post("/fixed-assets/capital-allowances")
async def run_capital_allowances_endpoint(
    req: CapitalAllowancesRequest,
    db: AsyncSession = Depends(get_db),
):
    return await run_capital_allowances(db, shop_id=req.shop_id, year=req.year)


# ==========================================
# 3. TAX COMPUTATION
# ==========================================

@router.get("/tax/computation")
@tax_router.get("/computation")
async def get_tax_computation_endpoint(
    shop_id: uuid.UUID = Query(...),
    year: int = Query(2026),
    db: AsyncSession = Depends(get_db),
):
    data = await compute_tax_schedule(db, shop_id=shop_id, year=year)
    return {"data": data, **data}


# ==========================================
# 4. TURNOVER TAX (TOT)
# ==========================================

@router.get("/tax/tot")
@router.get("/tax/turnover-tax")
@tax_router.get("/tot")
@tax_router.get("/turnover-tax")
async def get_turnover_tax_endpoint(
    shop_id: uuid.UUID = Query(...),
    year: int = Query(2026),
    db: AsyncSession = Depends(get_db),
):
    return await compute_turnover_tax(db, shop_id=shop_id, year=year)


# ==========================================
# 5. TAX INSTALMENTS
# ==========================================

@router.get("/tax/instalments")
@tax_router.get("/instalments")
async def get_tax_instalments_endpoint(
    shop_id: uuid.UUID = Query(...),
    year: int = Query(2026),
    db: AsyncSession = Depends(get_db),
):
    instalments = await list_tax_instalments(db, shop_id=shop_id, year=year)
    serialized = [_serialize_instalment(i) for i in instalments]
    return {"instalments": serialized, "data": serialized}


@router.post("/tax/instalments/generate")
@tax_router.post("/instalments/generate")
async def generate_tax_instalments_endpoint(
    req: GenerateInstalmentsRequest,
    db: AsyncSession = Depends(get_db),
):
    instalments = await generate_tax_instalments(db, shop_id=req.shop_id, year=req.year)
    serialized = [_serialize_instalment(i) for i in instalments]
    return {"success": True, "instalments": serialized}


@router.post("/tax/instalments/{instalment_id}/pay")
@tax_router.post("/instalments/{instalment_id}/pay")
async def pay_tax_instalment_endpoint(
    instalment_id: uuid.UUID,
    req: PayInstalmentRequest,
    db: AsyncSession = Depends(get_db),
):
    p_amount = req.paidAmount if req.paidAmount is not None else (req.paid_amount or Decimal("0.00"))
    p_ref = req.paymentReference or req.payment_reference or "MANUAL-TAX-PAYMENT"
    p_at = to_naive_utc(req.paidAt or req.paid_at) or utcnow()

    inst = await pay_tax_instalment(
        db=db,
        shop_id=req.shop_id,
        instalment_id=instalment_id,
        paid_amount=p_amount,
        payment_reference=p_ref,
        paid_at=p_at,
    )
    if not inst:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instalment not found")

    return {"success": True, "instalment": _serialize_instalment(inst)}


reconciliation_router = APIRouter(prefix="/reconciliation", tags=["Bank Reconciliation"], dependencies=[Depends(verify_internal_api_key)])


@reconciliation_router.get("")
async def get_bank_reconciliation(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, and_, or_, desc
    from app.models.identity import Shop
    from app.models.account import ChartOfAccount
    from app.models.journal import JournalEntry
    from app.models.payment import PaymentMethod

    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Workspace not found")

    q_acc = select(ChartOfAccount).where(
        and_(ChartOfAccount.shop_id == shop_id, ChartOfAccount.code == "1200")
    )
    res_acc = await db.execute(q_acc)
    cash_acc = res_acc.scalar_one_or_none()

    gl_entries = []
    book_balance = Decimal("0.00")

    if cash_acc:
        q_je = select(JournalEntry).where(
            and_(
                JournalEntry.shop_id == shop_id,
                or_(JournalEntry.debit_account_id == cash_acc.id, JournalEntry.credit_account_id == cash_acc.id),
            )
        ).order_by(desc(JournalEntry.entry_date)).limit(100)
        res_je = await db.execute(q_je)
        entries = res_je.scalars().all()

        for je in entries:
            is_debit = (je.debit_account_id == cash_acc.id)
            amt = je.amount
            if is_debit:
                book_balance += amt
            else:
                book_balance -= amt

            gl_entries.append({
                "id": str(je.id),
                "entryDate": je.entry_date.isoformat() if je.entry_date else "",
                "description": je.description,
                "amount": float(amt),
                "direction": "DEBIT" if is_debit else "CREDIT",
                "sourceType": je.source_type,
                "reference": je.reference_number,
                "isReconciled": False,
            })

    from app.models.payment import serialize_payment_method
    q_pm = select(PaymentMethod).where(PaymentMethod.shop_id == shop_id).order_by(desc(PaymentMethod.is_default), asc(PaymentMethod.created_at))
    res_pm = await db.execute(q_pm)
    methods = [
        serialize_payment_method(m)
        for m in res_pm.scalars().all()
    ]

    payload = {
        "shopId": str(shop_id),
        "currency": shop.currency or "KES",
        "cashAccountId": str(cash_acc.id) if cash_acc else "",
        "cashAccountCode": cash_acc.code if cash_acc else "1200",
        "cashAccountName": cash_acc.name if cash_acc else "Cash and Bank",
        "bookBalance": float(book_balance),
        "glEntries": gl_entries,
        "availablePaymentMethods": methods,
    }
    return {"success": True, "data": payload}

