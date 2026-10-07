import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import (
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # INVOICE, CREDIT_NOTE, RECEIPT, QUOTE, LPO, DELIVERY_NOTE, PAYMENT_VOUCHER
    doc_number: Mapped[str] = mapped_column("doc_number", String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False)  # DRAFT, ISSUED, PAID, PARTIAL, CANCELLED

    # Statutory KRA eTIMS & Lineage Fields
    kra_cu_invoice_number: Mapped[Optional[str]] = mapped_column("kra_cu_invoice_number", String(100), nullable=True)
    parent_document_id: Mapped[Optional[uuid.UUID]] = mapped_column("parent_document_id", UUID(as_uuid=True), nullable=True)
    requires_etims: Mapped[bool] = mapped_column("requires_etims", Boolean, default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    terms_and_conditions: Mapped[Optional[str]] = mapped_column("terms_and_conditions", Text, nullable=True)

    payment_channel: Mapped[Optional[str]] = mapped_column("payment_channel", String(50), nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column("payment_reference", String(100), nullable=True)

    # Multi-currency and Financial Metrics
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    exchange_rate: Mapped[Decimal] = mapped_column("exchange_rate", Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    base_currency: Mapped[Optional[str]] = mapped_column("base_currency", String(3), nullable=True)
    base_grand_total: Mapped[Optional[Decimal]] = mapped_column("base_grand_total", Numeric(12, 2), nullable=True)
    sub_total: Mapped[Decimal] = mapped_column("sub_total", Numeric(12, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column("tax_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column("grand_total", Numeric(12, 2), nullable=False)

    issue_date: Mapped[datetime] = mapped_column("issue_date", DateTime, default=datetime.utcnow, nullable=False)
    due_date: Mapped[Optional[datetime]] = mapped_column("due_date", DateTime, nullable=True)

    location_id: Mapped[Optional[uuid.UUID]] = mapped_column("location_id", UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    items: Mapped[List["DocumentItem"]] = relationship("DocumentItem", back_populates="document", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("shop_id", "doc_number", "type", name="unique_shop_doc_number"),
    )


class DocumentItem(Base):
    __tablename__ = "document_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column("product_id", UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column("unit_price", Numeric(12, 2), nullable=False)
    tax_type: Mapped[str] = mapped_column("tax_type", String(20), default="V_16", nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column("tax_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    item_total: Mapped[Decimal] = mapped_column("item_total", Numeric(12, 2), nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="items")
