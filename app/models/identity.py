import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Integer,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


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
    tax_pin: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    is_vat_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vat_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_gl_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    primary_color: Mapped[str] = mapped_column(String(20), default="#000000", nullable=False)
    plan: Mapped[str] = mapped_column(String(30), default="FREE", nullable=False)
    subscription_status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)
    is_lifetime_pro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ShopMember(Base):
    __tablename__ = "shop_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="OPERATOR", nullable=False)
    custom_permissions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_location_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    hide_cost_prices: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    direct_approval_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", lazy="selectin")
    shop: Mapped["Shop"] = relationship("Shop", lazy="selectin")


class ShopInvitation(Base):
    __tablename__ = "shop_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="EMPLOYEE", nullable=False)
    custom_permissions: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    assigned_location_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    hide_cost_prices: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    direct_approval_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    shop: Mapped["Shop"] = relationship("Shop", lazy="selectin")
