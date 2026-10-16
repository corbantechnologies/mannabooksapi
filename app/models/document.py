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
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.core.datetime_utils import utcnow


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    type: Mapped[str] = mapped_column(
        "type",
        SQLEnum(
            "QUOTATION", "INVOICE", "RECEIPT", "LPO", "PO", "LSO",
            "DELIVERY_NOTE", "CREDIT_NOTE", "DEBIT_NOTE",
            "GOODS_RECEIVED_NOTE", "SERVICE_COMPLETION_NOTE",
            "PAYMENT_VOUCHER", "PAYROLL_VOUCHER",
            name="doc_type",
            create_type=False,
        ),
        nullable=False,
    )
    doc_number: Mapped[str] = mapped_column("doc_number", String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        "status",
        SQLEnum(
            "DRAFT", "ISSUED", "OVERDUE", "PAID", "PARTIALLY_PAID",
            "RECEIVED", "CANCELLED", "CONFIRMED",
            name="doc_status",
            create_type=False,
        ),
        default="DRAFT",
        nullable=False,
    )

    # Statutory KRA eTIMS & Lineage Fields
    kra_cu_invoice_number: Mapped[Optional[str]] = mapped_column("kra_cu_invoice_number", String(100), nullable=True)
    parent_document_id: Mapped[Optional[uuid.UUID]] = mapped_column("parent_document_id", UUID(as_uuid=True), nullable=True)
    requires_etims: Mapped[bool] = mapped_column("requires_etims", Boolean, default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    terms_and_conditions: Mapped[Optional[str]] = mapped_column("terms_and_conditions", Text, nullable=True)

    payment_channel: Mapped[Optional[str]] = mapped_column("payment_channel", String(50), nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column("payment_reference", String(100), nullable=True)

    # Attachments Proof
    attachment_url: Mapped[Optional[str]] = mapped_column("attachment_url", Text, nullable=True)
    attachment_name: Mapped[Optional[str]] = mapped_column("attachment_name", String(255), nullable=True)
    attachment_size: Mapped[Optional[int]] = mapped_column("attachment_size", Integer, nullable=True)

    # Email Delivery Tracking
    resend_email_id: Mapped[Optional[str]] = mapped_column("resend_email_id", String(100), nullable=True)
    email_delivery_status: Mapped[str] = mapped_column("email_delivery_status", String(50), default="NOT_SENT", nullable=False)
    last_email_sent_at: Mapped[Optional[datetime]] = mapped_column("last_email_sent_at", DateTime, nullable=True)
    last_email_opened_at: Mapped[Optional[datetime]] = mapped_column("last_email_opened_at", DateTime, nullable=True)
    last_reminder_sent_at: Mapped[Optional[datetime]] = mapped_column("last_reminder_sent_at", DateTime, nullable=True)
    is_read_by_recipient: Mapped[bool] = mapped_column("is_read_by_recipient", Boolean, default=False, nullable=False)

    # Client Portal Interaction
    client_portal_response: Mapped[Optional[str]] = mapped_column("client_portal_response", String(50), nullable=True)
    client_amendment_notes: Mapped[Optional[str]] = mapped_column("client_amendment_notes", Text, nullable=True)

    # Loyalty Tracking
    loyalty_points_earned: Mapped[Optional[int]] = mapped_column("loyalty_points_earned", Integer, default=0, nullable=True)
    loyalty_points_redeemed: Mapped[Optional[int]] = mapped_column("loyalty_points_redeemed", Integer, default=0, nullable=True)
    loyalty_discount_amount: Mapped[Optional[Decimal]] = mapped_column("loyalty_discount_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=True)

    # Multi-currency and Financial Metrics
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    exchange_rate: Mapped[Decimal] = mapped_column("exchange_rate", Numeric(12, 4), default=Decimal("1.0000"), nullable=False)
    base_currency: Mapped[Optional[str]] = mapped_column("base_currency", String(3), nullable=True)
    base_grand_total: Mapped[Optional[Decimal]] = mapped_column("base_grand_total", Numeric(12, 2), nullable=True)
    sub_total: Mapped[Decimal] = mapped_column("sub_total", Numeric(12, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column("tax_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column("grand_total", Numeric(12, 2), nullable=False)

    issue_date: Mapped[datetime] = mapped_column("issue_date", DateTime, default=utcnow, nullable=False)
    due_date: Mapped[Optional[datetime]] = mapped_column("due_date", DateTime, nullable=True)

    # Recurring Invoicing
    is_recurring: Mapped[bool] = mapped_column("is_recurring", Boolean, default=False, nullable=False)
    recurring_interval: Mapped[Optional[str]] = mapped_column(
        "recurring_interval",
        SQLEnum('WEEKLY', 'MONTHLY', 'QUARTERLY', 'YEARLY', name="recurring_interval", create_type=False),
        nullable=True,
    )
    next_recurring_date: Mapped[Optional[datetime]] = mapped_column("next_recurring_date", DateTime, nullable=True)

    location_id: Mapped[Optional[uuid.UUID]] = mapped_column("location_id", UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=utcnow, nullable=False)

    # Relationships
    items: Mapped[List["DocumentItem"]] = relationship("DocumentItem", back_populates="document", cascade="all, delete-orphan", lazy="selectin")
    client = relationship("Client", lazy="selectin")
    supplier = relationship("Supplier", lazy="selectin")
    token_record = relationship("DocumentToken", lazy="selectin", uselist=False)
    notes_list: Mapped[List["DocumentNote"]] = relationship("DocumentNote", back_populates="document", cascade="all, delete-orphan", lazy="selectin", order_by="desc(DocumentNote.created_at)")

    __table_args__ = (
        UniqueConstraint("shop_id", "doc_number", "type", name="unique_shop_doc_number"),
    )

    @property
    def subtotal(self):
        return self.sub_total

    @subtotal.setter
    def subtotal(self, val):
        self.sub_total = val

    @property
    def tax_total(self):
        return self.tax_amount

    @tax_total.setter
    def tax_total(self, val):
        self.tax_amount = val


class DocumentItem(Base):
    __tablename__ = "document_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column("product_id", UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column("unit_price", Numeric(12, 2), nullable=False)
    tax_type: Mapped[str] = mapped_column(
        "tax_type",
        SQLEnum("V_16", "V_0", "EXEMPT", name="tax_type", create_type=False),
        default="V_16",
        nullable=False,
    )
    tax_amount: Mapped[Decimal] = mapped_column("tax_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    item_total: Mapped[Decimal] = mapped_column("item_total", Numeric(12, 2), nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="items")
    product = relationship("Product", lazy="selectin")


class DocumentToken(Base):
    __tablename__ = "document_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DocumentNote(Base):
    __tablename__ = "document_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    shop_id: Mapped[uuid.UUID] = mapped_column("shop_id", UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    document = relationship("Document", back_populates="notes_list")
    user = relationship("User", lazy="selectin")
