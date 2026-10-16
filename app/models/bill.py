import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import String, DateTime, ForeignKey, Numeric, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.core.datetime_utils import utcnow


class VendorBill(Base):
    __tablename__ = "vendor_bills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False, index=True)
    bill_number: Mapped[str] = mapped_column("bill_number", String(50), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bill_date: Mapped[datetime] = mapped_column("bill_date", DateTime, default=utcnow, nullable=False)
    due_date: Mapped[Optional[datetime]] = mapped_column("due_date", DateTime, nullable=True)
    sub_total: Mapped[Decimal] = mapped_column("sub_total", Numeric(12, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column("tax_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    wht_rate: Mapped[Decimal] = mapped_column("wht_rate", Numeric(5, 2), default=Decimal("0.00"), nullable=False)
    wht_amount: Mapped[Decimal] = mapped_column("wht_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column("total_amount", Numeric(12, 2), nullable=False)
    net_payable: Mapped[Decimal] = mapped_column("net_payable", Numeric(12, 2), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column("amount_paid", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False, index=True)
    payment_channel: Mapped[Optional[str]] = mapped_column("payment_channel", String(50), nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column("payment_reference", String(100), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column("paid_at", DateTime, nullable=True)
    attachment_url: Mapped[Optional[str]] = mapped_column("attachment_url", Text, nullable=True)
    attachment_name: Mapped[Optional[str]] = mapped_column("attachment_name", String(255), nullable=True)
    attachment_size: Mapped[Optional[int]] = mapped_column("attachment_size", Integer, nullable=True)
    source_po_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    source_grn_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    matching_status: Mapped[str] = mapped_column("matching_status", String(30), default="UNMATCHED", nullable=False, index=True)
    price_variance_amount: Mapped[Decimal] = mapped_column("price_variance_amount", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    quantity_variance_count: Mapped[Decimal] = mapped_column("quantity_variance_count", Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    variance_notes: Mapped[Optional[str]] = mapped_column("variance_notes", Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column("updated_at", DateTime, default=utcnow, nullable=False)

    supplier = relationship("Supplier", lazy="selectin")
    items = relationship("VendorBillItem", back_populates="bill", cascade="all, delete-orphan", lazy="selectin")
    source_po = relationship("Document", foreign_keys=[source_po_id], lazy="selectin")
    source_grn = relationship("Document", foreign_keys=[source_grn_id], lazy="selectin")


class VendorBillItem(Base):
    __tablename__ = "vendor_bill_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vendor_bills.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chart_of_accounts.id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("1.00"), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column("unit_price", Numeric(12, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column("tax_rate", Numeric(5, 2), default=Decimal("0.00"), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column("total_amount", Numeric(12, 2), nullable=False)
    po_item_id: Mapped[Optional[uuid.UUID]] = mapped_column("po_item_id", UUID(as_uuid=True), nullable=True)
    po_unit_price: Mapped[Optional[Decimal]] = mapped_column("po_unit_price", Numeric(12, 2), nullable=True)
    grn_quantity: Mapped[Optional[Decimal]] = mapped_column("grn_quantity", Numeric(12, 2), nullable=True)

    bill = relationship("VendorBill", back_populates="items")
    account = relationship("ChartOfAccount", lazy="selectin")
