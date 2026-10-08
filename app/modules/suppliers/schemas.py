import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class SupplierCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    phone: Optional[str] = None
    supplier_type: str = "CORPORATE"
    tax_pin: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{11}$")
    requires_etims: bool = False
    payment_terms: str = "NET_30"


class SupplierUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    supplier_type: Optional[str] = None
    tax_pin: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{11}$")
    requires_etims: Optional[bool] = None
    payment_terms: Optional[str] = None


class SupplierResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    email: str
    phone: Optional[str] = None
    supplier_type: str
    tax_pin: Optional[str] = None
    requires_etims: bool
    payment_terms: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
