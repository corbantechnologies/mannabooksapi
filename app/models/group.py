import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class GroupEntity(Base):
    __tablename__ = "group_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    reporting_currency: Mapped[str] = mapped_column("reporting_currency", String(3), default="KES", nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=datetime.utcnow, nullable=False)

    memberships = relationship("GroupMembership", back_populates="group", cascade="all, delete-orphan", lazy="selectin")
    transactions = relationship("InterCompanyTransaction", back_populates="group", cascade="all, delete-orphan", lazy="selectin")


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "shop_id", name="unique_group_shop"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column("entity_type", String(30), default="SUBSIDIARY", nullable=False)
    ownership_percentage: Mapped[Decimal] = mapped_column("ownership_percentage", Numeric(5, 2), default=Decimal("100.00"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column("joined_at", DateTime, default=datetime.utcnow, nullable=False)

    group = relationship("GroupEntity", back_populates="memberships")
    shop = relationship("Shop", lazy="selectin")


class InterCompanyTransaction(Base):
    __tablename__ = "inter_company_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("group_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    source_shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    target_shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    target_bill_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("vendor_bills.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="KES", nullable=False)
    transaction_type: Mapped[str] = mapped_column("transaction_type", String(50), default="MANAGEMENT_SERVICES", nullable=False)
    is_eliminated: Mapped[bool] = mapped_column("is_eliminated", Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=datetime.utcnow, nullable=False)

    group = relationship("GroupEntity", back_populates="transactions")
    source_shop = relationship("Shop", foreign_keys=[source_shop_id], lazy="selectin")
    target_shop = relationship("Shop", foreign_keys=[target_shop_id], lazy="selectin")
    source_document = relationship("Document", foreign_keys=[source_document_id], lazy="selectin")
    target_bill = relationship("VendorBill", foreign_keys=[target_bill_id], lazy="selectin")
