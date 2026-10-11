import uuid
from datetime import date, datetime
from typing import Optional, Dict, Any, List
from decimal import Decimal
from pydantic import BaseModel, Field


class TaxSettingsDTO(BaseModel):
    isCitActive: bool = True
    isTotActive: bool = False
    citRate: float = 30.00
    estimatedAnnualProfit: float = 0.00
    taxPin: Optional[str] = None
    isVatRegistered: bool = False
    vatNumber: Optional[str] = None
    taxRegime: str = "EXEMPT"


class TaxSettingsResponse(BaseModel):
    success: bool = True
    shop_id: str
    isCitActive: bool = True
    isTotActive: bool = False
    citRate: float = 30.00
    estimatedAnnualProfit: float = 0.00
    taxPin: Optional[str] = None
    isVatRegistered: bool = False
    vatNumber: Optional[str] = None
    taxRegime: str = "EXEMPT"
    settings: Optional[TaxSettingsDTO] = None


class UpdateTaxSettingsRequest(BaseModel):
    shop_id: uuid.UUID
    isCitActive: Optional[bool] = None
    is_cit_active: Optional[bool] = None
    isTotActive: Optional[bool] = None
    is_tot_active: Optional[bool] = None
    citRate: Optional[Decimal] = None
    cit_rate: Optional[Decimal] = None
    estimatedAnnualProfit: Optional[Decimal] = None
    estimated_annual_profit: Optional[Decimal] = None
    taxPin: Optional[str] = None
    tax_pin: Optional[str] = None
    isVatRegistered: Optional[bool] = None
    is_vat_registered: Optional[bool] = None
    vatNumber: Optional[str] = None
    vat_number: Optional[str] = None
    taxRegime: Optional[str] = None
    tax_regime: Optional[str] = None


class CreateFixedAssetRequest(BaseModel):
    shop_id: uuid.UUID
    name: str
    assetClass: Optional[str] = None
    asset_class: Optional[str] = None
    purchaseDate: Optional[date] = None
    purchase_date: Optional[date] = None
    purchaseCost: Optional[Decimal] = None
    purchase_cost: Optional[Decimal] = None
    scrapValue: Optional[Decimal] = Decimal("0.00")
    scrap_value: Optional[Decimal] = Decimal("0.00")


class DisposeFixedAssetRequest(BaseModel):
    shop_id: uuid.UUID
    disposalDate: Optional[date] = None
    disposal_date: Optional[date] = None
    disposalProceeds: Optional[Decimal] = None
    disposal_proceeds: Optional[Decimal] = None


class CapitalAllowancesRequest(BaseModel):
    shop_id: uuid.UUID
    year: int = 2026


class GenerateInstalmentsRequest(BaseModel):
    shop_id: uuid.UUID
    year: int = 2026


class PayInstalmentRequest(BaseModel):
    shop_id: uuid.UUID
    paidAmount: Optional[Decimal] = None
    paid_amount: Optional[Decimal] = None
    paymentReference: Optional[str] = None
    payment_reference: Optional[str] = None
    paidAt: Optional[datetime] = None
    paid_at: Optional[datetime] = None
