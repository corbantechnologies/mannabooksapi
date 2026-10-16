import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from app.core.schemas import ApiModel


class ClientCreateRequest(ApiModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: Optional[str] = ""
    phone: Optional[str] = None
    client_type: str = "WALK_IN"  # WALK_IN, CORPORATE, INDIVIDUAL
    tax_pin: Optional[str] = None
    requires_etims: bool = False


class ClientUpdateRequest(ApiModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[str] = None
    phone: Optional[str] = None
    client_type: Optional[str] = None
    tax_pin: Optional[str] = None
    requires_etims: Optional[bool] = None


class ClientResponse(ApiModel):
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
