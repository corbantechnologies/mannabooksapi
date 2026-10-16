import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.core.datetime_utils import utcnow

MONEY = Numeric(14, 2)


class StatutoryRateSet(Base):
    """
    Effective-dated statutory payroll parameters (managed by super admins).
    A payroll period uses the latest set whose `effective_from` is on or before the period start.
    `rates` layout: see app/modules/payroll/rates.py (DEFAULT_KE_RATES).
    """
    __tablename__ = "statutory_rate_sets"
    __table_args__ = (UniqueConstraint("country", "effective_from", name="uq_statutory_rate_sets_country_from"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country: Mapped[str] = mapped_column(String(2), default="KE", nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rates: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # False = entered but not yet confirmed against the official KRA/NSSF/SHA publications.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class PayrollLine(Base):
    """One employee's computed pay for one payroll run (the voucher is a Document of type PAYROLL_VOUCHER)."""
    __tablename__ = "payroll_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True)
    rate_set_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("statutory_rate_sets.id", ondelete="SET NULL"), nullable=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM
    mode: Mapped[str] = mapped_column(String(30), default="KENYA_STATUTORY", nullable=False)

    employee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kra_pin: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    basic_salary: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    allowances: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    commissions: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    gross_pay: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)

    nssf_employee: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    shif: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    housing_levy_employee: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    taxable_pay: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    tax_charged: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    personal_relief: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    paye: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    custom_deductions: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    total_deductions: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    net_pay: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)

    nssf_employer: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    housing_levy_employer: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    nita: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class PayrollRemittance(Base):
    """A payment of withheld/employer statutory amounts to an authority for a period."""
    __tablename__ = "payroll_remittances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True)
    authority: Mapped[str] = mapped_column(String(20), nullable=False)  # PAYE | NSSF | SHIF | HOUSING_LEVY | NITA
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payment_channel: Mapped[str] = mapped_column(String(30), default="BANK", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
