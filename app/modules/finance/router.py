import uuid
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.identity import Shop
from app.modules.finance.schemas import (
    TaxSettingsResponse,
    TaxSettingsDTO,
    UpdateTaxSettingsRequest,
)

router = APIRouter(prefix="/finance", tags=["Finance & Tax Settings"], dependencies=[Depends(verify_internal_api_key)])


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


@router.get("/tax/settings")
async def get_tax_settings_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    # Resolve CIT active flag
    cit_active = req.isCitActive if req.isCitActive is not None else req.is_cit_active
    if cit_active is not None:
        shop.is_cit_active = cit_active

    # Resolve TOT active flag
    tot_active = req.isTotActive if req.isTotActive is not None else req.is_tot_active
    if tot_active is not None:
        shop.is_tot_active = tot_active

    # Resolve CIT rate
    cit_r = req.citRate if req.citRate is not None else req.cit_rate
    if cit_r is not None:
        shop.cit_rate = Decimal(str(cit_r))

    # Resolve Estimated Annual Profit
    est_p = req.estimatedAnnualProfit if req.estimatedAnnualProfit is not None else req.estimated_annual_profit
    if est_p is not None:
        shop.estimated_annual_profit = Decimal(str(est_p))

    # Optional Tax PIN
    t_pin = req.taxPin if req.taxPin is not None else req.tax_pin
    if t_pin is not None:
        shop.tax_pin = t_pin.strip() or None

    # Optional VAT registration
    vat_reg = req.isVatRegistered if req.isVatRegistered is not None else req.is_vat_registered
    if vat_reg is not None:
        shop.is_vat_registered = vat_reg

    # Optional VAT Number
    vat_num = req.vatNumber if req.vatNumber is not None else req.vat_number
    if vat_num is not None:
        shop.vat_number = vat_num.strip() or None

    # Optional Tax Regime
    regime = req.taxRegime if req.taxRegime is not None else req.tax_regime
    if regime is not None:
        shop.tax_regime = regime.strip()

    await db.commit()
    await db.refresh(shop)

    resp = _build_tax_response(shop)
    resp["message"] = "Tax settings updated successfully."
    return resp
