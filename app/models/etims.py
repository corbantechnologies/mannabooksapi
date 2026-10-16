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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.core.datetime_utils import utcnow


class EtimsConfig(Base):
    __tablename__ = "etims_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, unique=True)
    kra_pin: Mapped[str] = mapped_column(String(30), nullable=False)
    branch_id: Mapped[str] = mapped_column(String(10), default="00", nullable=False)
    device_serial: Mapped[str] = mapped_column(String(100), nullable=False)
    communication_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)  # AES-256-GCM encrypted
    sdc_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    mrc_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    environment: Mapped[str] = mapped_column(String(20), default="SANDBOX", nullable=False)  # SANDBOX | PRODUCTION
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_fiscalize_invoices: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_fiscalize_receipts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class EtimsSequence(Base):
    """Monotonic integer invoice sequences locked per tenant branch for KRA OSCU numbering."""
    __tablename__ = "etims_sequences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    branch_id: Mapped[str] = mapped_column(String(10), default="00", nullable=False)
    last_sequence_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("shop_id", "branch_id", name="uq_etims_sequences_shop_branch"),
    )


class EtimsTransmission(Base):
    """Transactional Outbox and transmission audit log for eTIMS payloads."""
    __tablename__ = "etims_transmissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(50), nullable=False)  # SALES_INVOICE, CREDIT_NOTE, etc.
    invoice_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    status_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # "000" = KRA Success
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", nullable=False, index=True)  # QUEUED, FISCALIZED, FAILED
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
