import uuid
from typing import Optional
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterOwnerRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    business_name: str
    invite_token: Optional[str] = None


class UserProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    is_super_admin: bool
    plan: str
    subscription_status: str


class SessionResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    user: Optional[UserProfileResponse] = None
    shop_slug: Optional[str] = None
    error: Optional[str] = None
