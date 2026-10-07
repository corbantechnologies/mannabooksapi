import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class ClientCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    phone: Optional[str] = None
    client_type: str = "WALK_IN"  # WALK_IN, CORPORATE, INDIVIDUAL
    tax_pin: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{11}$")
    requires_etims: bool = False


class ClientUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    client_type: Optional[str] = None
    tax_pin: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{11}$")
    requires_etims: Optional[bool] = None


class ClientResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    email: str
    phone: Optional[str] = None
    client_type: str
    tax_pin: Optional[str] = None
    requires_etims: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
