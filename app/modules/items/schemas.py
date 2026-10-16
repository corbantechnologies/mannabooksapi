import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from app.core.schemas import ApiModel


class ItemCreateRequest(ApiModel):
    name: str = Field(..., min_length=1, max_length=255)
    sku: Optional[str] = None
    item_type: Literal["PRODUCT", "SERVICE"] = "PRODUCT"
    unit_price: Decimal = Field(..., ge=0)
    cost_price: Decimal = Field(Decimal("0.00"), ge=0)
    default_tax_type: Literal["V_16", "V_0", "EXEMPT"] = "V_16"
    track_stock: bool = False
    stock_quantity: Decimal = Field(Decimal("0.00"), ge=0)
    reorder_threshold: Decimal = Field(Decimal("5.00"), ge=0)
    default_location_id: Optional[uuid.UUID] = None

    # KRA eTIMS Classifications
    item_cls_cd: Optional[str] = None  # None -> auto-resolves to 24101601 (Product) or 80141600 (Service)
    pkg_unit_cd: str = "PCE"


class ItemUpdateRequest(ApiModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    sku: Optional[str] = None
    item_type: Optional[Literal["PRODUCT", "SERVICE"]] = None
    unit_price: Optional[Decimal] = Field(None, ge=0)
    cost_price: Optional[Decimal] = Field(None, ge=0)
    default_tax_type: Optional[Literal["V_16", "V_0", "EXEMPT"]] = None
    track_stock: Optional[bool] = None
    reorder_threshold: Optional[Decimal] = Field(None, ge=0)
    default_location_id: Optional[uuid.UUID] = None
    item_cls_cd: Optional[str] = None
    pkg_unit_cd: Optional[str] = None


class StockAdjustmentRequest(ApiModel):
    product_id: uuid.UUID
    location_id: Optional[uuid.UUID] = None
    quantity: Decimal = Field(..., gt=0, description="Positive quantity adjustment")
    movement_type: Literal["ADJUSTMENT_IN", "ADJUSTMENT_OUT", "RETURN", "VOID", "PURCHASE_RECEIPT"]
    adjustment_reason: Optional[Literal["DAMAGED", "EXPIRED", "THEFT", "COUNT_CORRECTION", "PROMOTION", "OTHER"]] = "COUNT_CORRECTION"
    unit_cost: Decimal = Field(Decimal("0.00"), ge=0)
    notes: Optional[str] = None


class ItemResponse(ApiModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    sku: Optional[str] = None
    item_type: str
    unit_price: Decimal
    cost_price: Decimal
    default_tax_type: str
    track_stock: bool
    stock_quantity: Decimal
    reorder_threshold: Decimal
    default_location_id: Optional[uuid.UUID] = None
    item_cls_cd: str
    pkg_unit_cd: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StockLedgerResponse(ApiModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    product_id: uuid.UUID
    location_id: Optional[uuid.UUID] = None
    movement_type: str
    quantity: Decimal
    unit_cost: Decimal
    running_balance: Optional[Decimal] = None
    adjustment_reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
