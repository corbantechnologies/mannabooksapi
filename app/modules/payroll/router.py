# app/modules/payroll/router.py
import csv
import io
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import AliasChoices, BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_acting_user_id, get_current_user, require_super_admin
from app.core.datetime_utils import to_naive_utc
from app.db.session import get_db
from app.models.document import Document
from app.models.hr import Employee
from app.models.identity import User
from app.models.payroll import PayrollLine, PayrollRemittance, StatutoryRateSet
from app.modules.payroll.rates import compute_pay, validate_rates
from app.modules.payroll.service import (
    AUTHORITIES,
    LINE_MONEY_FIELDS,
    P10_COLUMNS,
    P9_COLUMNS,
    create_payroll_run,
    finalized_lines,
    normalize_period,
    p10_rows,
    p9_rows,
    post_payroll_to_gl,
    rates_for_period,
    record_remittance,
    serialize_line,
    statutory_position,
)
from app.core.datetime_utils import utcnow

router = APIRouter(tags=["Payroll & Statutory"])
admin_router = APIRouter(prefix="/admin/statutory-rates", tags=["Global Admin"], dependencies=[Depends(require_super_admin)])

MODES = ("KENYA_STATUTORY", "MANUAL_CUSTOM")


def _alias(*names: str):
    return Field(default=None, validation_alias=AliasChoices(*names))


class PayrollLineInput(BaseModel):
    employee_id: Optional[str] = _alias("employee_id", "employeeId")
    employee_name: Optional[str] = _alias("employee_name", "employeeName")
    basic_salary: Decimal = Field(Decimal("0"), validation_alias=AliasChoices("basic_salary", "base_salary", "baseSalary", "basicSalary"))
    allowances: Decimal = Decimal("0")
    commissions: Decimal = Decimal("0")
    custom_deductions: Decimal = Field(Decimal("0"), validation_alias=AliasChoices("custom_deductions", "customDeductions"))

    @field_validator("basic_salary", "allowances", "commissions", "custom_deductions")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v is None:
            return Decimal("0")
        if v < 0:
            raise ValueError("Amounts cannot be negative.")
        return v


class CalculateRequest(BaseModel):
    shop_id: Optional[str] = _alias("shop_id", "shopId")
    period: Optional[str] = _alias("period", "payroll_period_code", "payrollPeriodCode")
    mode: str = "KENYA_STATUTORY"
    lines: List[PayrollLineInput] = Field(default_factory=list, max_length=1000)


class GeneratePayrollRequest(CalculateRequest):
    status: str = "DRAFT"
    issue_date: Optional[datetime] = _alias("issue_date", "issueDate")


class RemittanceRequest(BaseModel):
    shop_id: uuid.UUID
    authority: str
    period: str
    amount: Decimal
    payment_date: date
    payment_reference: Optional[str] = None
    payment_channel: str = "BANK"
    notes: Optional[str] = None


def _check_mode(mode: str) -> str:
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"Mode must be one of {', '.join(MODES)}")
    return mode


def _rate_info(rate_set_id, name, verified, period) -> dict:
    return {"id": str(rate_set_id) if rate_set_id else None, "name": name, "isVerified": verified, "period": period}


def _csv_response(filename: str, columns: list[tuple[str, str]], rows: list[dict]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in columns])
    for row in rows:
        out = []
        for key, _ in columns:
            v = row.get(key, "")
            out.append(f"{v:.2f}" if isinstance(v, Decimal) else v)
        writer.writerow(out)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Payroll runs
# ---------------------------------------------------------------------------
@router.post("/payroll/calculate")
async def calculate_payroll(req: CalculateRequest, db: AsyncSession = Depends(get_db)):
    """Preview: compute pay with the statutory rates in force for the period (nothing is saved)."""
    mode = _check_mode(req.mode)
    period = normalize_period(req.period)
    rate_set_id, rates, name, verified = await rates_for_period(db, period)
    calculations = []
    totals = {f: Decimal("0") for f in LINE_MONEY_FIELDS}
    for line in req.lines:
        calc = compute_pay(rates, line.basic_salary, line.allowances, line.commissions, line.custom_deductions, mode)
        for f in LINE_MONEY_FIELDS:
            totals[f] += calc[f]
        calculations.append({"employeeId": line.employee_id, "employeeName": line.employee_name, **calc})
    return {
        "success": True,
        "rateSet": _rate_info(rate_set_id, name, verified, period),
        "calculations": calculations,
        "totals": totals,
    }


@router.post("/payroll/generate")
@router.post("/payroll/commit")
async def generate_payroll_run(
    req: GeneratePayrollRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a payroll voucher. Without `lines`, every active employee is paid their basic salary."""
    mode = _check_mode(req.mode)
    if not req.shop_id:
        raise HTTPException(status_code=400, detail="shop_id is required")
    shop_id = uuid.UUID(str(req.shop_id))
    if req.status not in ("DRAFT", "PAID"):
        raise HTTPException(status_code=400, detail="status must be DRAFT or PAID")

    issue_date = to_naive_utc(req.issue_date) or utcnow()
    period = normalize_period(req.period, fallback=issue_date.date())

    if req.lines:
        lines = [l.model_dump() for l in req.lines]
    else:
        employees = (
            await db.execute(select(Employee).where(Employee.shop_id == shop_id, Employee.is_active == True))  # noqa: E712
        ).scalars().all()
        lines = [
            {"employee_id": str(e.id), "employee_name": e.full_name, "basic_salary": e.base_salary}
            for e in employees
        ]
    if not lines:
        raise HTTPException(status_code=400, detail="No employees to pay. Add employees or payroll lines first.")

    try:
        doc, created = await create_payroll_run(
            db, shop_id, period, mode, lines, req.status, issue_date, get_acting_user_id(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {
        "success": True,
        "voucherId": str(doc.id),
        "docNumber": doc.doc_number,
        "status": doc.status,
        "period": period,
        "employeesCount": len(created),
        "grossTotal": doc.sub_total,
        "grandTotal": doc.grand_total,
    }


@router.get("/payroll/{doc_id}/lines")
async def get_payroll_lines(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if not doc or doc.type != "PAYROLL_VOUCHER":
        raise HTTPException(status_code=404, detail="Payroll voucher not found")
    lines = (
        await db.execute(select(PayrollLine).where(PayrollLine.document_id == doc_id).order_by(PayrollLine.employee_name))
    ).scalars().all()
    totals = {f: sum((getattr(l, f) for l in lines), Decimal("0")) for f in LINE_MONEY_FIELDS}
    rate = None
    if lines and lines[0].rate_set_id:
        rs = await db.get(StatutoryRateSet, lines[0].rate_set_id)
        if rs:
            rate = _rate_info(rs.id, rs.name, rs.is_verified, lines[0].period)
    return {
        "success": True,
        "period": lines[0].period if lines else None,
        "rateSet": rate,
        "lines": [serialize_line(l) for l in lines],
        "totals": totals,
    }


@router.post("/payroll/{doc_id}/finalize")
async def finalize_payroll_run(
    doc_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doc = (await db.execute(select(Document).where(Document.id == doc_id).with_for_update())).scalar_one_or_none()
    if not doc or doc.type != "PAYROLL_VOUCHER":
        raise HTTPException(status_code=404, detail="Payroll voucher not found")
    if doc.status == "PAID":
        return {"success": True, "voucherId": str(doc.id), "status": "PAID", "message": "Already finalized"}
    if doc.status == "CANCELLED":
        raise HTTPException(status_code=400, detail="A cancelled payroll run cannot be finalized")

    doc.status = "PAID"
    entries = await post_payroll_to_gl(db, doc.shop_id, doc, None, current_user.id)
    await db.commit()
    return {"success": True, "voucherId": str(doc.id), "status": "PAID", "journalEntries": len(entries)}


@router.post("/payroll/{doc_id}/email")
async def email_payslips(doc_id: uuid.UUID):
    # Not implemented yet: fail loudly instead of reporting a fake success.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Emailing payslips is not available yet. Download the payroll voucher PDF instead.",
    )


# ---------------------------------------------------------------------------
# Statutory liabilities & remittances
# ---------------------------------------------------------------------------
@router.get("/payroll/statutory/position")
async def get_statutory_position(
    shop_id: uuid.UUID = Query(...),
    period: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    rows = await statutory_position(db, shop_id, normalize_period(period) if period else None)
    return {"success": True, "authorities": list(AUTHORITIES), "position": rows}


@router.get("/payroll/remittances")
async def list_remittances(
    shop_id: uuid.UUID = Query(...),
    period: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(PayrollRemittance).where(PayrollRemittance.shop_id == shop_id)
    if period:
        q = q.where(PayrollRemittance.period == normalize_period(period))
    rows = (await db.execute(q.order_by(PayrollRemittance.payment_date.desc()))).scalars().all()
    return {
        "success": True,
        "remittances": [
            {
                "id": str(r.id),
                "authority": r.authority,
                "period": r.period,
                "amount": r.amount,
                "paymentDate": r.payment_date.isoformat(),
                "paymentReference": r.payment_reference,
                "paymentChannel": r.payment_channel,
                "notes": r.notes,
            }
            for r in rows
        ],
    }


@router.post("/payroll/remittances")
async def create_remittance(req: RemittanceRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        rem = await record_remittance(
            db,
            req.shop_id,
            req.authority.upper(),
            normalize_period(req.period),
            req.amount,
            req.payment_date,
            req.payment_reference,
            req.payment_channel,
            req.notes,
            get_acting_user_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"success": True, "remittanceId": str(rem.id)}


# ---------------------------------------------------------------------------
# Statutory returns: P10 (monthly employer return) and P9 (employee annual tax card)
# ---------------------------------------------------------------------------
@router.get("/payroll/p10")
async def get_p10(
    shop_id: uuid.UUID = Query(...),
    period: str = Query(..., description="YYYY-MM"),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
):
    p = normalize_period(period)
    rows = p10_rows(await finalized_lines(db, shop_id, period=p))
    if format == "csv":
        return _csv_response(f"P10_{p}.csv", P10_COLUMNS, rows)
    return {"success": True, "period": p, "columns": P10_COLUMNS, "rows": rows}


@router.get("/payroll/p9")
async def get_p9(
    shop_id: uuid.UUID = Query(...),
    employee_id: uuid.UUID = Query(...),
    year: int = Query(..., ge=2000, le=2100),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
):
    emp = await db.get(Employee, employee_id)
    if not emp or emp.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Employee not found")
    rows = p9_rows(await finalized_lines(db, shop_id, year=year, employee_id=employee_id), year)
    if format == "csv":
        safe_name = "".join(ch for ch in emp.full_name if ch.isalnum() or ch in " -_").strip().replace(" ", "_")
        return _csv_response(f"P9_{year}_{safe_name or 'employee'}.csv", P9_COLUMNS, rows)
    return {
        "success": True,
        "year": year,
        "employee": {"id": str(emp.id), "name": emp.full_name, "kraPin": emp.kra_pin},
        "columns": P9_COLUMNS,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Super admin: statutory rate sets
# ---------------------------------------------------------------------------
class RateSetRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=120)
    effective_from: date
    rates: dict
    is_verified: bool = False
    notes: Optional[str] = None
    country: str = "KE"


class RateSetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=120)
    rates: Optional[dict] = None
    is_verified: Optional[bool] = None
    notes: Optional[str] = None


def _serialize_rate_set(rs: StatutoryRateSet, used: bool = False) -> dict[str, Any]:
    return {
        "id": str(rs.id),
        "country": rs.country,
        "name": rs.name,
        "effectiveFrom": rs.effective_from.isoformat(),
        "rates": rs.rates,
        "isVerified": rs.is_verified,
        "notes": rs.notes,
        "inUse": used,
        "updatedAt": rs.updated_at.isoformat() if rs.updated_at else None,
    }


async def _used_rate_set_ids(db: AsyncSession) -> set:
    return set((await db.execute(select(PayrollLine.rate_set_id).distinct())).scalars().all())


@admin_router.get("")
async def list_rate_sets(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(StatutoryRateSet).order_by(StatutoryRateSet.effective_from.desc()))).scalars().all()
    used = await _used_rate_set_ids(db)
    return {"success": True, "rateSets": [_serialize_rate_set(r, r.id in used) for r in rows]}


@admin_router.post("")
async def create_rate_set(req: RateSetRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        validate_rates(req.rates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    exists = (
        await db.execute(
            select(StatutoryRateSet.id).where(
                StatutoryRateSet.country == req.country, StatutoryRateSet.effective_from == req.effective_from
            )
        )
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="A rate set already starts on that date.")
    rs = StatutoryRateSet(
        country=req.country,
        name=req.name.strip(),
        effective_from=req.effective_from,
        rates=req.rates,
        is_verified=req.is_verified,
        notes=req.notes,
        created_by_id=user.id,
    )
    db.add(rs)
    await db.commit()
    return {"success": True, "rateSet": _serialize_rate_set(rs)}


@admin_router.patch("/{rate_set_id}")
async def update_rate_set(rate_set_id: uuid.UUID, req: RateSetUpdate, db: AsyncSession = Depends(get_db)):
    rs = await db.get(StatutoryRateSet, rate_set_id)
    if not rs:
        raise HTTPException(status_code=404, detail="Rate set not found")
    if req.rates is not None:
        if rate_set_id in await _used_rate_set_ids(db):
            raise HTTPException(
                status_code=409,
                detail="These rates were already used in payroll runs. Create a new rate set with a later effective date instead.",
            )
        try:
            rs.rates = validate_rates(req.rates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if req.name is not None:
        rs.name = req.name.strip()
    if req.is_verified is not None:
        rs.is_verified = req.is_verified
    if req.notes is not None:
        rs.notes = req.notes
    rs.updated_at = utcnow()
    await db.commit()
    return {"success": True, "rateSet": _serialize_rate_set(rs)}


@admin_router.delete("/{rate_set_id}")
async def delete_rate_set(rate_set_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rs = await db.get(StatutoryRateSet, rate_set_id)
    if not rs:
        raise HTTPException(status_code=404, detail="Rate set not found")
    if rate_set_id in await _used_rate_set_ids(db):
        raise HTTPException(status_code=409, detail="This rate set was used in payroll runs and cannot be deleted.")
    await db.delete(rs)
    await db.commit()
    return {"success": True}
