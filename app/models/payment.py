import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class DocumentPayment(Base):
    __tablename__ = "document_payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column("shop_id", UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column("amount", Numeric(12, 2), nullable=False)
    payment_date: Mapped[datetime] = mapped_column("payment_date", DateTime, default=datetime.utcnow, nullable=False)
    payment_channel: Mapped[str] = mapped_column("payment_channel", String(50), default="BANK", nullable=False)  # MPESA, BANK, CASH, CHEQUE, OTHER
    payment_reference: Mapped[Optional[str]] = mapped_column("payment_reference", String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column("recorded_by_user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=datetime.utcnow, nullable=False)
