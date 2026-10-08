import uuid
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class AccountCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=10)
    name: str = Field(..., min_length=2, max_length=255)
    account_type: Literal["ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"]
    parent_code: Optional[str] = None


class AccountResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    code: str
    name: str
    account_type: str
    is_system: bool
    parent_code: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
