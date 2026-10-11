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
    Boolean,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
import app.models.identity  # noqa: F401 - ensure User model is in registry


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

    recorded_by = relationship("User", lazy="selectin", foreign_keys=[recorded_by_user_id])


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column("shop_id", UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column("is_default", Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column("created_at", DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return serialize_payment_method(self)


def serialize_payment_method(pm: Any) -> dict:
    if not pm:
        return {}
    name_str = (getattr(pm, "name", "") or "").strip()
    details_str = (getattr(pm, "details", "") or "").strip()
    is_def = bool(getattr(pm, "is_default", False))

    import re
    # Extract paybill / business number
    paybill_match = re.search(r"(?:paybill|business\s*(?:no|number))[^\d:]*[:\s]+([0-9]+)", details_str, re.I)
    paybill_no = paybill_match.group(1) if paybill_match else None
    if not paybill_no:
        name_num = re.search(r"\(([0-9]+)\)", name_str)
        if name_num:
            paybill_no = name_num.group(1)

    # Extract account reference or account number
    acc_ref_match = re.search(r"(?:account\s*ref|acc\s*ref|account\s*reference)[^:]*[:\s]+([^\n|]+)", details_str, re.I)
    acc_ref = acc_ref_match.group(1).strip() if acc_ref_match else None

    acc_no_match = re.search(r"(?:acc(?:ount)?\s*(?:no|number))[^\d:]*[:\s]+([0-9A-Za-z\-]+)", details_str, re.I)
    acc_no = acc_no_match.group(1).strip() if acc_no_match else (acc_ref if not paybill_no else None)

    bank_match = re.search(r"(?:bank)[^:]*[:\s]+([^\n|]+)", details_str, re.I)
    bank_name = bank_match.group(1).strip() if bank_match else None

    acc_name_match = re.search(r"(?:acc(?:ount)?\s*name)[^:]*[:\s]+([^\n|]+)", details_str, re.I)
    acc_name = acc_name_match.group(1).strip() if acc_name_match else None

    branch_match = re.search(r"(?:branch)[^:]*[:\s]+([^\n|]+)", details_str, re.I)
    branch = branch_match.group(1).strip() if branch_match else None

    lower_comb = (name_str + " " + details_str).lower()
    if "till" in lower_comb:
        pm_type = "TILL"
    elif "paybill" in lower_comb or "mpesa" in lower_comb or "m-pesa" in lower_comb:
        pm_type = "MPESA"
    elif "bank" in lower_comb:
        pm_type = "BANK"
    else:
        pm_type = getattr(pm, "type", "BANK")

    created_at_dt = getattr(pm, "created_at", None)

    return {
        "id": str(getattr(pm, "id", "")),
        "shopId": str(getattr(pm, "shop_id", "")) if getattr(pm, "shop_id", None) else None,
        "shop_id": str(getattr(pm, "shop_id", "")) if getattr(pm, "shop_id", None) else None,
        "type": pm_type,
        "name": name_str,
        "details": details_str,
        "instructions": details_str,
        "description": details_str,
        "isDefault": is_def,
        "is_default": is_def,
        "paybillNumber": paybill_no,
        "paybill_number": paybill_no,
        "businessNumber": paybill_no,
        "accountNumber": acc_no or acc_ref,
        "account_number": acc_no or acc_ref,
        "accountRef": acc_ref or ("Invoice number" if paybill_no else None),
        "account_ref": acc_ref or ("Invoice number" if paybill_no else None),
        "accountName": acc_name,
        "account_name": acc_name,
        "bankName": bank_name,
        "bank_name": bank_name,
        "branch": branch,
        "createdAt": created_at_dt.isoformat() if created_at_dt else None,
    }
