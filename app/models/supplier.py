import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.core.datetime_utils import utcnow


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    supplier_type: Mapped[str] = mapped_column(
        "supplier_type",
        SQLEnum("WALK_IN", "INDIVIDUAL", "CORPORATE", name="client_type", create_type=False),
        default="CORPORATE",
        nullable=False,
    )
    tax_pin: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    requires_etims: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_terms: Mapped[Optional[str]] = mapped_column(String(50), default="NET_30", nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
