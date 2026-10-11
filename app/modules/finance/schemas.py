import uuid
from typing import Optional, Dict, Any
from decimal import Decimal
from pydantic import BaseModel


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
