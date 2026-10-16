import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Integer,
    JSON,
    Numeric,
    Index,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.core.datetime_utils import utcnow



class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    plan: Mapped[str] = mapped_column(String(30), default="FREE", nullable=False)
    subscription_status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)
    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    grace_period_ends_at: Mapped[Optional[datetime]] = mapped_column("grace_period_ends_at", DateTime, nullable=True)
    auto_renew_enabled: Mapped[bool] = mapped_column("auto_renew_enabled", Boolean, default=True, nullable=False)
    auto_renew_phone: Mapped[Optional[str]] = mapped_column("auto_renew_phone", String(30), nullable=True)
    last_renewal_prompt_at: Mapped[Optional[datetime]] = mapped_column("last_renewal_prompt_at", DateTime, nullable=True)
    is_lifetime_pro: Mapped[bool] = mapped_column("is_lifetime_pro", Boolean, default=False, nullable=False)
    # JWTs issued before this instant are rejected (set on password change / "log out everywhere").
    tokens_valid_after: Mapped[Optional[datetime]] = mapped_column("tokens_valid_after", DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RevokedToken(Base):
    """Denylist of individual JWTs (by jti) revoked at logout, kept until they expire."""
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class RefreshToken(Base):
    """
    Rotating refresh tokens. Each sign-in starts a `family`; every refresh replaces the token with a
    new one in the same family. Presenting an already-rotated token (outside a short grace window)
    means it was copied, so the whole family is revoked.
    """
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Absolute end of the whole family (sign-in time + REFRESH_TOKEN_DAYS).
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    replaced_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    user: Mapped["User"] = relationship("User")


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    code: Mapped[Optional[str]] = mapped_column(String(10), unique=True, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="KES", nullable=False)
    short_name: Mapped[Optional[str]] = mapped_column("short_name", String(50), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column("phone", String(50), nullable=True)
    website: Mapped[Optional[str]] = mapped_column("website", Text, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column("logo_url", Text, nullable=True)
    email: Mapped[Optional[str]] = mapped_column("email", String(255), nullable=True)
    tax_pin: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    is_vat_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vat_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    hide_onboarding: Mapped[bool] = mapped_column("hide_onboarding", Boolean, default=False, nullable=False)
    is_gl_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gl_onboarding_mode: Mapped[bool] = mapped_column("gl_onboarding_mode", Boolean, default=False, nullable=False)
    tax_regime: Mapped[str] = mapped_column("tax_regime", SQLEnum('CIT', 'TOT', 'EXEMPT', name="tax_regime", create_type=False), default="EXEMPT", nullable=False)
    is_cit_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_tot_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cit_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("30.00"), nullable=False)
    estimated_annual_profit: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0.00"), nullable=False)
    auto_stock_deduction_enabled: Mapped[bool] = mapped_column("auto_stock_deduction_enabled", Boolean, default=True, nullable=False)
    business_mode: Mapped[str] = mapped_column("business_mode", String(20), default="HYBRID", nullable=False)
    loyalty_engine_mode: Mapped[str] = mapped_column("loyalty_engine_mode", SQLEnum("OFF", "POINTS_ONLY", "TIERS_ONLY", "HYBRID", name="loyalty_engine_mode", create_type=False), default="OFF", nullable=False)
    primary_color: Mapped[str] = mapped_column(String(20), default="#000000", nullable=False)
    plan: Mapped[str] = mapped_column(String(30), default="FREE", nullable=False)
    subscription_status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)
    is_lifetime_pro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_suspended: Mapped[bool] = mapped_column("is_suspended", Boolean, default=False, nullable=False)
    # Public catalog: issue quotations immediately instead of creating drafts for review.
    catalog_auto_issue_quotes: Mapped[bool] = mapped_column("catalog_auto_issue_quotes", Boolean, default=False, nullable=False)
    suspended_reason: Mapped[Optional[str]] = mapped_column("suspended_reason", Text, nullable=True)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column("trial_ends_at", DateTime, nullable=True)
    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column("subscription_expires_at", DateTime, nullable=True)
    grace_period_ends_at: Mapped[Optional[datetime]] = mapped_column("grace_period_ends_at", DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ShopTerm(Base):
    __tablename__ = "shop_terms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column("display_order", Integer, default=0, nullable=False)
    is_default_invoice: Mapped[bool] = mapped_column("is_default_invoice", Boolean, default=False, nullable=False)
    is_default_catalog: Mapped[bool] = mapped_column("is_default_catalog", Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=utcnow, nullable=False)


class ShopCurrency(Base):
    __tablename__ = "shop_currencies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(3), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), default="$", nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column("exchange_rate", Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    is_enabled: Mapped[bool] = mapped_column("is_enabled", Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column("updated_at", DateTime, default=utcnow, nullable=False)


class BillingTransaction(Base):
    __tablename__ = "billing_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    checkout_request_id: Mapped[str] = mapped_column("checkout_request_id", String(100), unique=True, nullable=False)
    merchant_request_id: Mapped[Optional[str]] = mapped_column("merchant_request_id", String(100), nullable=True)
    phone_number: Mapped[str] = mapped_column("phone_number", String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column("amount", Numeric(12, 2), nullable=False)
    mpesa_receipt_number: Mapped[Optional[str]] = mapped_column("mpesa_receipt_number", String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    result_code: Mapped[Optional[int]] = mapped_column("result_code", Integer, nullable=True)
    result_desc: Mapped[Optional[str]] = mapped_column("result_desc", Text, nullable=True)
    target_plan: Mapped[str] = mapped_column("target_plan", String(30), nullable=False)
    billing_months: Mapped[int] = mapped_column("billing_months", Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column("completed_at", DateTime, nullable=True)


class ShopMember(Base):
    __tablename__ = "shop_members"
    __table_args__ = (Index("uq_shop_members_shop_user", "shop_id", "user_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(
        "role",
        SQLEnum(
            'OWNER', 'ADMIN', 'MANAGER', 'ACCOUNTANT', 'STOREKEEPER',
            'CASHIER', 'DISPATCHER', 'SALES_REP', 'EMPLOYEE', 'VIEWER',
            name="user_role", create_type=False
        ),
        default="EMPLOYEE",
        nullable=False,
    )
    custom_permissions: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    assigned_location_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    hide_cost_prices: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    direct_approval_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", lazy="selectin")
    shop: Mapped["Shop"] = relationship("Shop", lazy="selectin")


class ShopInvitation(Base):
    __tablename__ = "shop_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        "role",
        SQLEnum(
            'OWNER', 'ADMIN', 'MANAGER', 'ACCOUNTANT', 'STOREKEEPER',
            'CASHIER', 'DISPATCHER', 'SALES_REP', 'EMPLOYEE', 'VIEWER',
            name="user_role", create_type=False
        ),
        default="EMPLOYEE",
        nullable=False,
    )
    custom_permissions: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    assigned_location_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    hide_cost_prices: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    direct_approval_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        "status",
        SQLEnum('PENDING', 'ACCEPTED', 'REVOKED', name="invitation_status", create_type=False),
        default="PENDING",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    shop: Mapped["Shop"] = relationship("Shop", lazy="selectin")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", lazy="selectin")

