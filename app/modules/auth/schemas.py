import uuid
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.core.schemas import ApiModel

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72  # bcrypt only uses the first 72 bytes


def validate_password_strength(value: str) -> str:
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(value.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError("Password is too long (maximum 72 bytes).")
    if value.isalpha() or value.isdigit():
        raise ValueError("Password must mix letters with numbers or symbols.")
    return value


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(..., max_length=256)


class RegisterOwnerRequest(ApiModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    password: str
    business_name: str = Field(..., min_length=1, max_length=150)
    invite_token: Optional[str] = None

    @field_validator("password")
    @classmethod
    def _password_policy(cls, v: str) -> str:
        return validate_password_strength(v)


class ForgotPasswordRequest(ApiModel):
    email: EmailStr


class ResetPasswordRequest(ApiModel):
    token: str = Field(..., min_length=32, max_length=128)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password_policy(cls, v: str) -> str:
        return validate_password_strength(v)


class UserProfileResponse(ApiModel):
    id: uuid.UUID
    name: str
    email: str
    is_super_admin: bool
    plan: str
    subscription_status: str


class SessionResponse(ApiModel):
    success: bool
    token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    refresh_expires_in: Optional[int] = None
    user: Optional[UserProfileResponse] = None
    shop_slug: Optional[str] = None
    error: Optional[str] = None
