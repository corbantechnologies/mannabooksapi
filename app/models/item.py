import uuid
from datetime import datetime
from typing import Optional
from decimal import Decimal
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Numeric, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    item_type: Mapped[str] = mapped_column(String(20), default="PRODUCT", nullable=False)  # 'PRODUCT' | 'SERVICE'
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    default_tax_type: Mapped[str] = mapped_column(
        "default_tax_type",
        SQLEnum("V_16", "V_0", "EXEMPT", name="tax_type", create_type=False),
        default="V_16",
        nullable=False,
    )
    track_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stock_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    reorder_threshold: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("5.00"), nullable=False)
    default_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    # KRA eTIMS Statutory Classifications (Approved auto-defaults)
    item_cls_cd: Mapped[str] = mapped_column(String(20), default="24101601", nullable=False)  # WCO/UNSPSC
    pkg_unit_cd: Mapped[str] = mapped_column(String(10), default="PCE", nullable=False)  # Standard packaging unit

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
