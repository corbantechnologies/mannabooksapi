import uuid
from datetime import datetime
from typing import Optional
from decimal import Decimal
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class StockLocation(Base):
    __tablename__ = "stock_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class StockLedger(Base):
    """Immutable audit trail of physical and fiscal inventory movements."""
    __tablename__ = "stock_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_locations.id", ondelete="SET NULL"), nullable=True)
    movement_type: Mapped[str] = mapped_column(String(50), nullable=False)  # PURCHASE_RECEIPT, SALE, ADJUSTMENT_IN, ADJUSTMENT_OUT, RETURN
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    running_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    transfer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    adjustment_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # DAMAGED, EXPIRED, COUNT_CORRECTION, OTHER
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("Product", lazy="selectin")
    location = relationship("StockLocation", lazy="selectin")

