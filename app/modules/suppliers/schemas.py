import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from app.core.schemas import ApiModel


class SupplierCreateRequest(ApiModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: Optional[str] = None
    phone: Optional[str] = None
    supplier_type: str = "CORPORATE"
    tax_pin: Optional[str] = None
    requires_etims: bool = False
    payment_terms: str = "NET_30"


class SupplierUpdateRequest(ApiModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    email: Optional[str] = None
    phone: Optional[str] = None
    supplier_type: Optional[str] = None
    tax_pin: Optional[str] = None
    requires_etims: Optional[bool] = None
    payment_terms: Optional[str] = None


class SupplierResponse(ApiModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    supplier_type: str
    tax_pin: Optional[str] = None
    requires_etims: bool
    payment_terms: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
