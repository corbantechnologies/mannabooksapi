# app/modules/payroll/service.py
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import ChartOfAccount
from app.models.document import Document, DocumentItem
from app.models.hr import Employee
from app.models.identity import Shop
from app.models.journal import JournalEntry
from app.models.payroll import PayrollLine, PayrollRemittance
from app.modules.accounts.gl_service import ensure_default_accounts
from app.modules.payroll.rates import DEFAULT_KE_RATES, compute_pay, get_rate_set_for, period_start
from app.core.datetime_utils import utcnow

ZERO = Decimal("0.00")

# Authority -> (PayrollLine fields that accrue to it, liability account code)
AUTHORITIES: dict[str, tuple[tuple[str, ...], str]] = {
    "PAYE": (("paye",), "2310"),
    "NSSF": (("nssf_employee", "nssf_employer"), "2311"),
    "SHIF": (("shif",), "2312"),
    "HOUSING_LEVY": (("housing_levy_employee", "housing_levy_employer"), "2313"),
    "NITA": (("nita",), "2314"),
}

PAYROLL_ACCOUNTS = [
    {"code": "2300", "name": "Net Salaries Payable", "account_type": "LIABILITY"},
    {"code": "2310", "name": "PAYE Payable (KRA)", "account_type": "LIABILITY"},
    {"code": "2311", "name": "NSSF Payable", "account_type": "LIABILITY"},
    {"code": "2312", "name": "SHIF Payable (SHA)", "account_type": "LIABILITY"},
    {"code": "2313", "name": "Affordable Housing Levy Payable", "account_type": "LIABILITY"},
    {"code": "2314", "name": "NITA Levy Payable", "account_type": "LIABILITY"},
    {"code": "2315", "name": "Other Payroll Deductions Payable", "account_type": "LIABILITY"},
    {"code": "6100", "name": "Salaries and Wages", "account_type": "EXPENSE"},
    {"code": "6110", "name": "Employer Payroll Contributions", "account_type": "EXPENSE"},
]

LINE_MONEY_FIELDS = (
    "basic_salary", "allowances", "commissions", "gross_pay", "nssf_employee", "shif",
    "housing_levy_employee", "taxable_pay", "tax_charged", "personal_relief", "paye",
    "custom_deductions", "total_deductions", "net_pay", "nssf_employer", "housing_levy_employer", "nita",
)


def normalize_period(raw: Optional[str], fallback: Optional[date] = None) -> str:
    """Accept 'YYYY-MM', 'YYYY-MM-DD' or labels like 'OCTOBER-2026'; return 'YYYY-MM'."""
    months = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
              "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
    if raw:
        text = raw.strip().upper()
        for i, name in enumerate(months, start=1):
            if text.startswith(name) or text.startswith(name[:3] + "-") or text.startswith(name[:3] + " "):
                year = "".join(ch for ch in text if ch.isdigit())[-4:]
                if len(year) == 4:
                    return f"{year}-{i:02d}"
        parts = text.replace("/", "-").split("-")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit() and len(parts[0]) == 4:
            month = int(parts[1])
            if 1 <= month <= 12:
                return f"{parts[0]}-{month:02d}"
    d = fallback or utcnow().date()
    return f"{d.year}-{d.month:02d}"


async def rates_for_period(db: AsyncSession, period: str) -> tuple[Optional[uuid.UUID], dict, Optional[str], bool]:
    """(rate_set_id, rates, rate_set_name, is_verified) for a YYYY-MM period."""
    rate_set = await get_rate_set_for(db, period_start(period))
    if rate_set:
        return rate_set.id, rate_set.rates, rate_set.name, rate_set.is_verified
    return None, DEFAULT_KE_RATES, "Built-in defaults", False


def serialize_line(line: PayrollLine) -> dict[str, Any]:
    data = {f: getattr(line, f) for f in LINE_MONEY_FIELDS}
    data.update(
        id=str(line.id),
        employeeId=str(line.employee_id) if line.employee_id else None,
        employeeName=line.employee_name,
        kraPin=line.kra_pin,
        period=line.period,
        mode=line.mode,
    )
    return data


async def create_payroll_run(
    db: AsyncSession,
    shop_id: uuid.UUID,
    period: str,
    mode: str,
    lines: Iterable[dict],
    status: str,
    issue_date: datetime,
    created_by_id: Optional[uuid.UUID],
) -> tuple[Document, list[PayrollLine]]:
    """
    Create a payroll voucher (Document type PAYROLL_VOUCHER) with one PayrollLine per employee.
    `lines` items: {employee_id?, employee_name, basic_salary, allowances, commissions, custom_deductions}.
    Caller commits.
    """
    rate_set_id, rates, _, _ = await rates_for_period(db, period)

    employee_ids = [uuid.UUID(str(l["employee_id"])) for l in lines if l.get("employee_id")]
    employees: dict[uuid.UUID, Employee] = {}
    if employee_ids:
        rows = (
            await db.execute(select(Employee).where(Employee.shop_id == shop_id, Employee.id.in_(employee_ids)))
        ).scalars().all()
        employees = {e.id: e for e in rows}

    doc = Document(
        shop_id=shop_id,
        type="PAYROLL_VOUCHER",
        doc_number=f"PAY-{period.replace('-', '')}-{secrets.token_hex(2).upper()}",
        status="DRAFT",
        issue_date=issue_date,
        currency="KES",
        sub_total=ZERO,
        tax_amount=ZERO,
        grand_total=ZERO,
        notes=f"Payroll run for {period}",
    )
    db.add(doc)
    await db.flush()

    created: list[PayrollLine] = []
    gross_total = ZERO
    net_total = ZERO
    for raw in lines:
        emp_id = uuid.UUID(str(raw["employee_id"])) if raw.get("employee_id") else None
        if emp_id is not None and emp_id not in employees:
            raise ValueError("One of the employees does not belong to this workspace.")
        emp = employees.get(emp_id) if emp_id else None
        calc = compute_pay(
            rates,
            raw.get("basic_salary", 0),
            raw.get("allowances", 0),
            raw.get("commissions", 0),
            raw.get("custom_deductions", 0),
            mode,
        )
        name = (raw.get("employee_name") or (emp.full_name if emp else "") or "Staff").strip()
        line = PayrollLine(
            shop_id=shop_id,
            document_id=doc.id,
            employee_id=emp_id,
            rate_set_id=rate_set_id,
            period=period,
            mode=mode,
            employee_name=name,
            kra_pin=emp.kra_pin if emp else None,
            **{f: calc[f] for f in LINE_MONEY_FIELDS},
        )
        db.add(line)
        created.append(line)
        db.add(
            DocumentItem(
                document_id=doc.id,
                description=(
                    f"[Payroll {period}] {name} | Gross {calc['gross_pay']:.2f} | PAYE {calc['paye']:.2f} | "
                    f"NSSF {calc['nssf_employee']:.2f} | SHIF {calc['shif']:.2f} | "
                    f"AHL {calc['housing_levy_employee']:.2f} | Other {calc['custom_deductions']:.2f} | "
                    f"Net {calc['net_pay']:.2f}"
                ),
                quantity=Decimal("1.00"),
                unit_price=calc["net_pay"],
                tax_type="EXEMPT",
                tax_amount=ZERO,
                item_total=calc["net_pay"],
            )
        )
        gross_total += calc["gross_pay"]
        net_total += calc["net_pay"]

    doc.sub_total = gross_total
    doc.grand_total = net_total
    await db.flush()

    if status == "PAID":
        doc.status = "PAID"
        await post_payroll_to_gl(db, shop_id, doc, created, created_by_id)
    return doc, created


async def _ensure_payroll_accounts(db: AsyncSession, shop_id: uuid.UUID) -> dict[str, ChartOfAccount]:
    accounts = await ensure_default_accounts(db, shop_id)
    for spec in PAYROLL_ACCOUNTS:
        if spec["code"] not in accounts:
            acc = ChartOfAccount(shop_id=shop_id, is_system=True, **spec)
            db.add(acc)
            accounts[spec["code"]] = acc
    await db.flush()
    return accounts


async def _gl_enabled(db: AsyncSession, shop_id: uuid.UUID) -> bool:
    shop = await db.get(Shop, shop_id)
    return bool(shop and shop.is_gl_enabled)


async def post_payroll_to_gl(
    db: AsyncSession,
    shop_id: uuid.UUID,
    doc: Document,
    lines: Optional[list[PayrollLine]] = None,
    created_by_id: Optional[uuid.UUID] = None,
) -> list[JournalEntry]:
    """
    Post a settled payroll voucher. Idempotent (skips if the voucher already has entries).
      Dr Salaries & Wages            / Cr Net Salaries Payable, PAYE, NSSF, SHIF, AHL, Other deductions
      Dr Employer Contributions      / Cr NSSF (employer), AHL (employer), NITA
      Dr Net Salaries Payable        / Cr Cash and Bank   (staff paid)
    """
    if not await _gl_enabled(db, shop_id) or str(doc.type).upper() != "PAYROLL_VOUCHER":
        return []
    already = await db.execute(
        select(JournalEntry.id).where(and_(JournalEntry.shop_id == shop_id, JournalEntry.source_id == doc.id))
    )
    if already.first():
        return []

    if lines is None:
        lines = list((await db.execute(select(PayrollLine).where(PayrollLine.document_id == doc.id))).scalars().all())

    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    if lines:
        for line in lines:
            for f in LINE_MONEY_FIELDS:
                totals[f] += getattr(line, f)
    else:
        # Vouchers created before payroll lines existed: only gross and net are known.
        totals["gross_pay"] = Decimal(str(doc.sub_total or 0))
        totals["net_pay"] = Decimal(str(doc.grand_total or 0))
        totals["custom_deductions"] = totals["gross_pay"] - totals["net_pay"]

    from app.modules.accounts.posting import assert_period_open

    entry_date = doc.issue_date or utcnow()
    await assert_period_open(db, shop_id, entry_date)
    accounts = await _ensure_payroll_accounts(db, shop_id)
    entries: list[JournalEntry] = []

    def post(desc: str, debit: str, credit: str, amount: Decimal) -> None:
        if amount > 0:
            entries.append(
                JournalEntry(
                    shop_id=shop_id,
                    entry_date=entry_date,
                    description=f"Payroll {doc.doc_number} - {desc}",
                    debit_account_id=accounts[debit].id,
                    credit_account_id=accounts[credit].id,
                    amount=amount,
                    reference_number=doc.doc_number,
                    source_type="payroll",
                    source_id=doc.id,
                    created_by_id=created_by_id,
                )
            )

    post("Net pay accrued", "6100", "2300", totals["net_pay"])
    post("PAYE withheld", "6100", "2310", totals["paye"])
    post("NSSF (employee)", "6100", "2311", totals["nssf_employee"])
    post("SHIF", "6100", "2312", totals["shif"])
    post("Housing Levy (employee)", "6100", "2313", totals["housing_levy_employee"])
    post("Other deductions", "6100", "2315", totals["custom_deductions"])
    post("NSSF (employer)", "6110", "2311", totals["nssf_employer"])
    post("Housing Levy (employer)", "6110", "2313", totals["housing_levy_employer"])
    post("NITA levy", "6110", "2314", totals["nita"])
    post("Net pay disbursed", "2300", "1200", totals["net_pay"])

    for e in entries:
        db.add(e)
    return entries


# ---------------------------------------------------------------------------
# Remittances
# ---------------------------------------------------------------------------
async def statutory_position(db: AsyncSession, shop_id: uuid.UUID, period: Optional[str] = None) -> list[dict]:
    """Accrued vs remitted per authority and period (finalized payroll runs only)."""
    cols = [func.coalesce(func.sum(getattr(PayrollLine, f)), 0) for f in
            ("paye", "nssf_employee", "nssf_employer", "shif", "housing_levy_employee", "housing_levy_employer", "nita")]
    q = (
        select(PayrollLine.period, *cols)
        .join(Document, Document.id == PayrollLine.document_id)
        .where(PayrollLine.shop_id == shop_id, Document.status == "PAID")
        .group_by(PayrollLine.period)
    )
    if period:
        q = q.where(PayrollLine.period == period)
    accrued: dict[tuple[str, str], Decimal] = {}
    for row in (await db.execute(q)).all():
        p, paye, nssf_e, nssf_r, shif, ahl_e, ahl_r, nita = row
        accrued[(p, "PAYE")] = Decimal(paye)
        accrued[(p, "NSSF")] = Decimal(nssf_e) + Decimal(nssf_r)
        accrued[(p, "SHIF")] = Decimal(shif)
        accrued[(p, "HOUSING_LEVY")] = Decimal(ahl_e) + Decimal(ahl_r)
        accrued[(p, "NITA")] = Decimal(nita)

    rq = (
        select(PayrollRemittance.period, PayrollRemittance.authority, func.coalesce(func.sum(PayrollRemittance.amount), 0))
        .where(PayrollRemittance.shop_id == shop_id)
        .group_by(PayrollRemittance.period, PayrollRemittance.authority)
    )
    if period:
        rq = rq.where(PayrollRemittance.period == period)
    remitted = {(p, a): Decimal(v) for p, a, v in (await db.execute(rq)).all()}

    keys = sorted(set(accrued) | set(remitted), key=lambda k: (k[0], k[1]), reverse=True)
    out = []
    for p, a in keys:
        acc = accrued.get((p, a), ZERO)
        rem = remitted.get((p, a), ZERO)
        if acc == 0 and rem == 0:
            continue
        out.append({"period": p, "authority": a, "accrued": acc, "remitted": rem, "outstanding": acc - rem})
    return out


async def record_remittance(
    db: AsyncSession,
    shop_id: uuid.UUID,
    authority: str,
    period: str,
    amount: Decimal,
    payment_date: date,
    reference: Optional[str],
    channel: str,
    notes: Optional[str],
    created_by_id: Optional[uuid.UUID],
) -> PayrollRemittance:
    if authority not in AUTHORITIES:
        raise ValueError(f"Unknown authority. Use one of: {', '.join(AUTHORITIES)}")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    position = {r["authority"]: r for r in await statutory_position(db, shop_id, period)}
    outstanding = position.get(authority, {}).get("outstanding", ZERO)
    if amount > outstanding:
        raise ValueError(
            f"Amount exceeds the outstanding {authority} balance for {period} ({outstanding:.2f})."
        )

    rem = PayrollRemittance(
        shop_id=shop_id,
        authority=authority,
        period=period,
        amount=amount,
        payment_date=payment_date,
        payment_reference=(reference or "").strip() or None,
        payment_channel=channel or "BANK",
        notes=notes,
        created_by_id=created_by_id,
    )
    db.add(rem)
    await db.flush()

    if await _gl_enabled(db, shop_id):
        from app.modules.accounts.posting import assert_period_open

        await assert_period_open(db, shop_id, payment_date)
        accounts = await _ensure_payroll_accounts(db, shop_id)
        db.add(
            JournalEntry(
                shop_id=shop_id,
                entry_date=datetime.combine(payment_date, datetime.min.time()),
                description=f"{authority} remittance for {period}" + (f" ({rem.payment_reference})" if rem.payment_reference else ""),
                debit_account_id=accounts[AUTHORITIES[authority][1]].id,
                credit_account_id=accounts["1200"].id,
                amount=amount,
                reference_number=rem.payment_reference,
                source_type="payroll",
                source_id=rem.id,
                created_by_id=created_by_id,
            )
        )
    return rem


# ---------------------------------------------------------------------------
# Statutory returns
# ---------------------------------------------------------------------------
async def finalized_lines(
    db: AsyncSession,
    shop_id: uuid.UUID,
    period: Optional[str] = None,
    year: Optional[int] = None,
    employee_id: Optional[uuid.UUID] = None,
) -> list[PayrollLine]:
    q = (
        select(PayrollLine)
        .join(Document, Document.id == PayrollLine.document_id)
        .where(PayrollLine.shop_id == shop_id, Document.status == "PAID")
        .order_by(PayrollLine.period, PayrollLine.employee_name)
    )
    if period:
        q = q.where(PayrollLine.period == period)
    if year:
        q = q.where(PayrollLine.period.like(f"{year:04d}-%"))
    if employee_id:
        q = q.where(PayrollLine.employee_id == employee_id)
    return list((await db.execute(q)).scalars().all())


P10_COLUMNS = [
    ("kra_pin", "Employee KRA PIN"),
    ("employee_name", "Employee Name"),
    ("basic_salary", "Basic Salary"),
    ("allowances", "Allowances"),
    ("commissions", "Commissions / Other Cash Pay"),
    ("gross_pay", "Gross Pay"),
    ("nssf_employee", "NSSF (Employee)"),
    ("shif", "SHIF"),
    ("housing_levy_employee", "Housing Levy (Employee)"),
    ("taxable_pay", "Taxable Pay"),
    ("tax_charged", "Tax Charged"),
    ("personal_relief", "Personal Relief"),
    ("paye", "PAYE"),
]


def p10_rows(lines: list[PayrollLine]) -> list[dict]:
    """One row per employee for the month (multiple runs in a month are summed)."""
    grouped: dict[str, dict] = {}
    for line in lines:
        key = str(line.employee_id or line.employee_name)
        row = grouped.setdefault(
            key,
            {"kra_pin": line.kra_pin or "", "employee_name": line.employee_name,
             **{f: ZERO for f, _ in P10_COLUMNS if f not in ("kra_pin", "employee_name")}},
        )
        for f, _ in P10_COLUMNS:
            if f not in ("kra_pin", "employee_name"):
                row[f] += getattr(line, f)
    return sorted(grouped.values(), key=lambda r: r["employee_name"])


P9_COLUMNS = [
    ("period", "Month"),
    ("basic_salary", "Basic Salary"),
    ("allowances", "Allowances"),
    ("commissions", "Other Cash Pay"),
    ("gross_pay", "Gross Pay"),
    ("nssf_employee", "NSSF (E1/E2)"),
    ("shif", "SHIF"),
    ("housing_levy_employee", "Housing Levy"),
    ("taxable_pay", "Chargeable Pay"),
    ("tax_charged", "Tax Charged"),
    ("personal_relief", "Personal Relief"),
    ("paye", "PAYE Tax"),
]


def p9_rows(lines: list[PayrollLine], year: int) -> list[dict]:
    """Twelve monthly rows (zeros for months without pay) plus totals."""
    by_month: dict[str, dict] = {
        f"{year:04d}-{m:02d}": {"period": f"{year:04d}-{m:02d}", **{f: ZERO for f, _ in P9_COLUMNS if f != "period"}}
        for m in range(1, 13)
    }
    for line in lines:
        row = by_month.get(line.period)
        if row is None:
            continue
        for f, _ in P9_COLUMNS:
            if f != "period":
                row[f] += getattr(line, f)
    rows = list(by_month.values())
    total = {"period": "TOTAL", **{f: sum((r[f] for r in rows), ZERO) for f, _ in P9_COLUMNS if f != "period"}}
    return rows + [total]
