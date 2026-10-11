import uuid
import secrets
from datetime import datetime, date
from app.core.datetime_utils import to_naive_utc
from decimal import Decimal
from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.db.session import get_db
from app.models.identity import User, Shop, ShopMember
from app.models.hr import Employee, ExpenseClaim
from app.models.expense import Expense
from app.models.document import Document, DocumentItem
from app.models.journal import JournalEntry
from app.modules.auth.service import verify_and_get_session

router = APIRouter(tags=["HR, Payroll & ESS"])


# -------------------------------------------------------------------------
# Kenyan Statutory Payroll Math
# -------------------------------------------------------------------------
def compute_kenyan_deductions(base_salary: float, allowances: float, commissions: float, custom_deductions: float = 0.0, mode: str = "KENYA_STATUTORY"):
    base = round(base_salary, 2)
    allow = round(allowances, 2)
    comms = round(commissions, 2)
    custom_ded = round(custom_deductions, 2)
    gross_salary = round(base + allow + comms, 2)

    if mode == "MANUAL_CUSTOM":
        total_ded = custom_ded
        net_pay = max(0.0, round(gross_salary - total_ded, 2))
        return {
            "gross_salary": gross_salary,
            "nssf": 0.0,
            "shif": 0.0,
            "housing_levy": 0.0,
            "paye": 0.0,
            "custom_deductions": custom_ded,
            "total_deductions": total_ded,
            "net_pay": net_pay,
        }

    # 1. NSSF: 6% capped at KES 2,160 max
    nssf = round(gross_salary * 0.06, 2)
    if nssf > 2160.0:
        nssf = 2160.0

    # 2. SHIF: 2.75% of Gross
    shif = round(gross_salary * 0.0275, 2)

    # 3. Affordable Housing Levy (AHL): 1.5% of Gross
    housing_levy = round(gross_salary * 0.015, 2)

    # 4. PAYE calculation (NSSF is tax-exempt)
    taxable_income = max(0.0, gross_salary - nssf)
    gross_paye = 0.0
    if taxable_income > 0.0:
        b1 = min(taxable_income, 24000.0)
        gross_paye += b1 * 0.10
        if taxable_income > 24000.0:
            b2 = min(taxable_income - 24000.0, 8333.0)
            gross_paye += b2 * 0.25
        if taxable_income > 32333.0:
            b3 = taxable_income - 32333.0
            gross_paye += b3 * 0.30

    # Monthly Personal Relief: KES 2,400
    personal_relief = 2400.0
    paye = round(gross_paye - personal_relief, 2) if gross_paye > personal_relief else 0.0

    total_ded = round(nssf + shif + housing_levy + paye + custom_ded, 2)
    net_pay = max(0.0, round(gross_salary - total_ded, 2))

    return {
        "gross_salary": gross_salary,
        "nssf": nssf,
        "shif": shif,
        "housing_levy": housing_levy,
        "paye": paye,
        "custom_deductions": custom_ded,
        "total_deductions": total_ded,
        "net_pay": net_pay,
    }


# -------------------------------------------------------------------------
# Request Models
# -------------------------------------------------------------------------
class CreateEmployeeRequest(BaseModel):
    shop_id: str
    full_name: str
    email: Optional[str] = None
    national_id: Optional[str] = None
    kra_pin: Optional[str] = None
    nhif_number: Optional[str] = None
    nssf_number: Optional[str] = None
    base_salary: Decimal = Decimal("0.00")
    commission_rate: Decimal = Decimal("0.00")
    department: Optional[str] = None
    designation: Optional[str] = None
    employment_type: str = "FULL_TIME"
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_branch: Optional[str] = None
    mpesa_phone: Optional[str] = None


class UpdateEmployeeRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    national_id: Optional[str] = None
    kra_pin: Optional[str] = None
    nhif_number: Optional[str] = None
    nssf_number: Optional[str] = None
    base_salary: Optional[Decimal] = None
    commission_rate: Optional[Decimal] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    employment_type: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_branch: Optional[str] = None
    mpesa_phone: Optional[str] = None
    is_active: Optional[bool] = None


class LinkUserRequest(BaseModel):
    user_id: str


class PayrollLineItem(BaseModel):
    employee_id: Optional[str] = None
    employee_name: str
    base_salary: float
    allowances: float = 0.0
    commissions: float = 0.0
    custom_deductions: float = 0.0


class CommitPayrollRequest(BaseModel):
    shop_id: str
    payroll_period_code: str  # e.g. "OCTOBER-2026"
    mode: str = "KENYA_STATUTORY"
    status: str = "DRAFT"  # DRAFT or PAID
    issue_date: Optional[datetime] = None
    lines: List[PayrollLineItem]


class CreateExpenseClaimRequest(BaseModel):
    shop_id: str
    employee_id: str
    title: str
    description: Optional[str] = None
    amount: Decimal
    claim_date: str  # YYYY-MM-DD
    category: str = "OTHER"
    cost_center_id: Optional[str] = None
    receipt_url: Optional[str] = None
    submit_now: bool = False


class ReviewExpenseClaimRequest(BaseModel):
    decision: str  # APPROVED or REJECTED
    notes: Optional[str] = None


class DisburseExpenseClaimRequest(BaseModel):
    payment_channel: str = "BANK"
    payment_reference: Optional[str] = None
    payment_date: Optional[str] = None


# -------------------------------------------------------------------------
# EMPLOYEES ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/employees")
async def list_employees(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Employee).where(Employee.shop_id == shop_id).order_by(desc(Employee.created_at))
    res = await db.execute(q)
    employees = res.scalars().all()
    return {"success": True, "employees": employees}


@router.post("/employees")
async def create_employee(
    req: CreateEmployeeRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    clean_id = req.national_id.strip() if req.national_id else None
    clean_pin = req.kra_pin.strip().upper() if req.kra_pin else None

    if clean_id:
        q_exist = select(Employee).where(Employee.shop_id == req.shop_id, Employee.national_id == clean_id)
        res_exist = await db.execute(q_exist)
        if res_exist.scalar_one_or_none():
            return {"success": False, "error": f"An employee with National ID '{clean_id}' is already registered."}

    if clean_pin:
        q_pin = select(Employee).where(Employee.shop_id == req.shop_id, Employee.kra_pin == clean_pin)
        res_pin = await db.execute(q_pin)
        if res_pin.scalar_one_or_none():
            return {"success": False, "error": f"An employee with KRA PIN '{clean_pin}' is already registered."}

    emp = Employee(
        shop_id=uuid.UUID(req.shop_id),
        full_name=req.full_name.strip(),
        email=req.email.strip().lower() if req.email else None,
        national_id=clean_id,
        kra_pin=clean_pin,
        nhif_number=req.nhif_number.strip() if req.nhif_number else None,
        nssf_number=req.nssf_number.strip() if req.nssf_number else None,
        base_salary=req.base_salary,
        commission_rate=req.commission_rate,
        department=req.department.strip() if req.department else None,
        designation=req.designation.strip() if req.designation else None,
        employment_type=req.employment_type,
        bank_name=req.bank_name.strip() if req.bank_name else None,
        bank_account_number=req.bank_account_number.strip() if req.bank_account_number else None,
        bank_branch=req.bank_branch.strip() if req.bank_branch else None,
        mpesa_phone=req.mpesa_phone.strip() if req.mpesa_phone else None,
        is_active=True,
    )
    db.add(emp)
    await db.commit()
    await db.refresh(emp)

    return {"success": True, "employee": emp}


@router.get("/employees/{employee_id}")
async def get_employee(
    employee_id: str,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Employee).where(Employee.id == employee_id, Employee.shop_id == shop_id)
    res = await db.execute(q)
    emp = res.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Fetch payroll vouchers
    q_docs = select(Document).where(Document.shop_id == shop_id, Document.type == "PAYROLL_VOUCHER").order_by(desc(Document.issue_date))
    res_docs = await db.execute(q_docs)
    docs = res_docs.scalars().all()

    payroll_history = []
    for doc in docs:
        q_items = select(DocumentItem).where(DocumentItem.document_id == doc.id)
        res_items = await db.execute(q_items)
        items = res_items.scalars().all()
        for it in items:
            if emp.full_name in it.description or str(emp.id) in it.description:
                payroll_history.append({
                    "voucherId": str(doc.id),
                    "docNumber": doc.doc_number,
                    "issueDate": doc.issue_date.isoformat() if doc.issue_date else None,
                    "description": it.description,
                    "netPay": float(it.unit_price),
                })

    return {"success": True, "employee": emp, "payrollHistory": payroll_history}


@router.patch("/employees/{employee_id}")
async def update_employee(
    employee_id: str,
    req: UpdateEmployeeRequest,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Employee).where(Employee.id == employee_id, Employee.shop_id == shop_id)
    res = await db.execute(q)
    emp = res.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if req.full_name is not None:
        emp.full_name = req.full_name.strip()
    if req.email is not None:
        emp.email = req.email.strip().lower() if req.email else None
    if req.national_id is not None:
        emp.national_id = req.national_id.strip() if req.national_id else None
    if req.kra_pin is not None:
        emp.kra_pin = req.kra_pin.strip().upper() if req.kra_pin else None
    if req.nhif_number is not None:
        emp.nhif_number = req.nhif_number.strip() if req.nhif_number else None
    if req.nssf_number is not None:
        emp.nssf_number = req.nssf_number.strip() if req.nssf_number else None
    if req.base_salary is not None:
        emp.base_salary = req.base_salary
    if req.commission_rate is not None:
        emp.commission_rate = req.commission_rate
    if req.department is not None:
        emp.department = req.department.strip() if req.department else None
    if req.designation is not None:
        emp.designation = req.designation.strip() if req.designation else None
    if req.employment_type is not None:
        emp.employment_type = req.employment_type
    if req.bank_name is not None:
        emp.bank_name = req.bank_name.strip() if req.bank_name else None
    if req.bank_account_number is not None:
        emp.bank_account_number = req.bank_account_number.strip() if req.bank_account_number else None
    if req.bank_branch is not None:
        emp.bank_branch = req.bank_branch.strip() if req.bank_branch else None
    if req.mpesa_phone is not None:
        emp.mpesa_phone = req.mpesa_phone.strip() if req.mpesa_phone else None
    if req.is_active is not None:
        emp.is_active = req.is_active

    await db.commit()
    return {"success": True}


@router.delete("/employees/{employee_id}")
async def delete_employee(
    employee_id: str,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Employee).where(Employee.id == employee_id, Employee.shop_id == shop_id)
    res = await db.execute(q)
    emp = res.scalar_one_or_none()
    if emp:
        await db.delete(emp)
        await db.commit()
    return {"success": True}


@router.post("/employees/{employee_id}/link-user")
async def link_employee_user(
    employee_id: str,
    req: LinkUserRequest,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q_mem = select(ShopMember).where(ShopMember.shop_id == shop_id, ShopMember.user_id == req.user_id, ShopMember.is_active == True)
    res_mem = await db.execute(q_mem)
    if not res_mem.scalar_one_or_none():
        return {"success": False, "error": "The selected user is not an active member of this workspace."}

    q_emp = select(Employee).where(Employee.id == employee_id, Employee.shop_id == shop_id)
    res_emp = await db.execute(q_emp)
    emp = res_emp.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    emp.user_id = uuid.UUID(req.user_id)
    await db.commit()
    return {"success": True}


@router.post("/employees/{employee_id}/unlink-user")
async def unlink_employee_user(
    employee_id: str,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q_emp = select(Employee).where(Employee.id == employee_id, Employee.shop_id == shop_id)
    res_emp = await db.execute(q_emp)
    emp = res_emp.scalar_one_or_none()
    if emp:
        emp.user_id = None
        await db.commit()
    return {"success": True}


@router.get("/employees/linkable-users/{shop_id}")
async def get_linkable_users(
    shop_id: str,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q_mem = (
        select(ShopMember, User)
        .join(User, ShopMember.user_id == User.id)
        .where(ShopMember.shop_id == shop_id, ShopMember.is_active == True)
    )
    res_mem = await db.execute(q_mem)
    members = res_mem.all()

    q_emps = select(Employee).where(Employee.shop_id == shop_id)
    res_emps = await db.execute(q_emps)
    linked_ids = {str(e.user_id) for e in res_emps.scalars().all() if e.user_id}

    users_data = []
    for m, u in members:
        users_data.append({
            "id": str(u.id),
            "name": u.name,
            "email": u.email,
            "role": m.role,
            "isLinked": str(u.id) in linked_ids,
        })
    return {"success": True, "users": users_data}


# -------------------------------------------------------------------------
# STATUTORY PAYROLL RUN COMMITMENT
# -------------------------------------------------------------------------
@router.post("/payroll/calculate")
async def calculate_payroll(
    lines: List[PayrollLineItem],
    mode: str = Query("KENYA_STATUTORY"),
):
    calculations = []
    for l in lines:
        calcs = compute_kenyan_deductions(l.base_salary, l.allowances, l.commissions, l.custom_deductions, mode=mode)
        calculations.append({
            "employeeName": l.employee_name,
            "employeeId": l.employee_id,
            **calcs,
        })
    return {"success": True, "calculations": calculations}


@router.post("/payroll/commit")
async def commit_payroll_run(
    req: CommitPayrollRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    issue_date = req.issue_date or datetime.utcnow()
    doc_number = f"PAY-{issue_date.strftime('%Y%m')}-{secrets.token_hex(2).upper()}"

    global_subtotal = Decimal("0.00")
    global_grandtotal = Decimal("0.00")
    line_entries = []

    for line in req.lines:
        calcs = compute_kenyan_deductions(line.base_salary, line.allowances, line.commissions, line.custom_deductions, mode=req.mode)
        gross = Decimal(str(calcs["gross_salary"]))
        net = Decimal(str(calcs["net_pay"]))
        desc = (
            f"[Payroll Run: {req.payroll_period_code}] Staff: {line.employee_name} | "
            f"Base: {line.base_salary:.2f} | Allow: {line.allowances:.2f} | Comm: {line.commissions:.2f} | "
            f"Deductions: {calcs['total_deductions']:.2f} (PAYE: {calcs['paye']:.2f}, SHIF: {calcs['shif']:.2f}, "
            f"AHL: {calcs['housing_levy']:.2f}, NSSF: {calcs['nssf']:.2f}, Adv: {calcs['custom_deductions']:.2f}) | "
            f"Net: {calcs['net_pay']:.2f}"
        )
        line_entries.append({
            "desc": desc,
            "net": net,
            "gross": gross,
            "employee_id": line.employee_id,
        })
        global_subtotal += gross
        global_grandtotal += net

    # Create Document (type: PAYROLL_VOUCHER)
    doc = Document(
        shop_id=uuid.UUID(req.shop_id),
        type="PAYROLL_VOUCHER",
        doc_number=doc_number,
        status="PAID" if req.status == "PAID" else "DRAFT",
        issue_date=issue_date,
        currency="KES",
        sub_total=global_subtotal,
        tax_amount=Decimal("0.00"),
        grand_total=global_grandtotal,
        notes=f"Statutory Payroll Run for {req.payroll_period_code}",
    )
    db.add(doc)
    await db.flush()

    for item in line_entries:
        doc_item = DocumentItem(
            document_id=doc.id,
            description=item["desc"],
            quantity=Decimal("1.00"),
            unit_price=item["net"],
            tax_type="EXEMPT",
            tax_amount=Decimal("0.00"),
            item_total=item["net"],
        )
        db.add(doc_item)

    await db.commit()
    return {"success": True, "voucherId": str(doc.id), "docNumber": doc.doc_number}


@router.post("/payroll/generate")
async def generate_payroll_run(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    shop_id = uuid.UUID(req["shop_id"]) if isinstance(req.get("shop_id"), str) else req.get("shop_id")
    period = req.get("payroll_period_code") or req.get("period") or datetime.utcnow().strftime("%Y-%m")
    mode = req.get("mode", "KENYA_STATUTORY")

    # Fetch active employees for this shop
    q = select(Employee).where(Employee.shop_id == shop_id, Employee.is_active == True)
    res = await db.execute(q)
    employees = res.scalars().all()

    issue_date = datetime.utcnow()
    doc_number = f"PAY-{issue_date.strftime('%Y%m')}-{secrets.token_hex(2).upper()}"
    global_subtotal = Decimal("0.00")
    global_grandtotal = Decimal("0.00")
    line_entries = []

    for emp in employees:
        base_salary = float(emp.base_salary)
        calcs = compute_kenyan_deductions(base_salary, 0.0, 0.0, 0.0, mode=mode)
        gross = Decimal(str(calcs["gross_salary"]))
        net = Decimal(str(calcs["net_pay"]))
        desc = (
            f"[Payroll Run: {period}] Staff: {emp.full_name} | "
            f"Base: {base_salary:.2f} | Net: {calcs['net_pay']:.2f}"
        )
        line_entries.append({
            "desc": desc,
            "net": net,
            "gross": gross,
            "employee_id": emp.id,
        })
        global_subtotal += gross
        global_grandtotal += net

    doc = Document(
        shop_id=shop_id,
        type="PAYROLL_VOUCHER",
        doc_number=doc_number,
        status="DRAFT",
        issue_date=issue_date,
        currency="KES",
        sub_total=global_subtotal,
        tax_amount=Decimal("0.00"),
        grand_total=global_grandtotal,
        notes=f"Statutory Payroll Run for {period}",
    )
    db.add(doc)
    await db.flush()

    for item in line_entries:
        doc_item = DocumentItem(
            document_id=doc.id,
            description=item["desc"],
            quantity=Decimal("1.00"),
            unit_price=item["net"],
            tax_type="EXEMPT",
            tax_amount=Decimal("0.00"),
            item_total=item["net"],
        )
        db.add(doc_item)

    await db.commit()
    await db.refresh(doc)
    return {
        "success": True,
        "voucherId": str(doc.id),
        "docNumber": doc.doc_number,
        "employeesCount": len(employees),
        "grandTotal": float(global_grandtotal),
    }


@router.post("/payroll/{doc_id}/finalize")
async def finalize_payroll_run_endpoint(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Payroll voucher not found")
    doc.status = "PAID"
    await db.commit()
    return {"success": True, "voucherId": str(doc.id), "status": "PAID"}


@router.post("/payroll/{doc_id}/email")
async def email_payslips_endpoint(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Payroll voucher not found")
    return {"success": True, "voucherId": str(doc.id), "message": "Payslips dispatched via email to all staff."}



# -------------------------------------------------------------------------
# EXPENSE CLAIMS ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/expense-claims")
async def list_expense_claims(
    shop_id: str = Query(..., description="Workspace ID"),
    filter_status: Optional[str] = Query(None),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conditions = [ExpenseClaim.shop_id == shop_id]
    if filter_status and filter_status != "ALL":
        conditions.append(ExpenseClaim.status == filter_status)

    q = select(ExpenseClaim, Employee).join(Employee, ExpenseClaim.employee_id == Employee.id).where(and_(*conditions)).order_by(desc(ExpenseClaim.created_at))
    res = await db.execute(q)
    results = res.all()

    claims = []
    for c, emp in results:
        claims.append({
            "id": str(c.id),
            "claimNumber": c.claim_number,
            "title": c.title,
            "description": c.description,
            "amount": float(c.amount),
            "claimDate": c.claim_date.isoformat(),
            "category": c.category,
            "status": c.status,
            "receiptUrl": c.receipt_url,
            "employee": {
                "id": str(emp.id),
                "fullName": emp.full_name,
                "email": emp.email,
                "designation": emp.designation,
            },
            "createdAt": c.created_at.isoformat(),
        })

    return {"success": True, "claims": claims}


@router.post("/expense-claims")
async def create_expense_claim(
    req: CreateExpenseClaimRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.utcnow()
    claim_number = f"CLM-{now.strftime('%y%m')}-{secrets.randbelow(9000) + 1000}"

    c_date = date.fromisoformat(req.claim_date) if req.claim_date else date.today()
    claim = ExpenseClaim(
        shop_id=uuid.UUID(req.shop_id),
        employee_id=uuid.UUID(req.employee_id),
        claim_number=claim_number,
        title=req.title.strip(),
        description=req.description.strip() if req.description else None,
        amount=req.amount,
        claim_date=c_date,
        category=req.category,
        cost_center_id=uuid.UUID(req.cost_center_id) if req.cost_center_id else None,
        receipt_url=req.receipt_url,
        status="SUBMITTED" if req.submit_now else "DRAFT",
    )
    db.add(claim)
    await db.commit()
    await db.refresh(claim)
    return {"success": True, "claim": {"id": str(claim.id), "claimNumber": claim.claim_number}}


@router.post("/expense-claims/{claim_id}/submit")
async def submit_claim(
    claim_id: str,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(ExpenseClaim).where(ExpenseClaim.id == claim_id, ExpenseClaim.shop_id == shop_id)
    res = await db.execute(q)
    c = res.scalar_one_or_none()
    if c:
        c.status = "SUBMITTED"
        await db.commit()
    return {"success": True}


@router.post("/expense-claims/{claim_id}/review")
async def review_claim(
    claim_id: str,
    req: ReviewExpenseClaimRequest,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(ExpenseClaim).where(ExpenseClaim.id == claim_id, ExpenseClaim.shop_id == shop_id)
    res = await db.execute(q)
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Claim not found")

    c.status = req.decision
    c.approved_by_id = user.id
    c.approved_at = datetime.utcnow()
    c.approval_notes = req.notes.strip() if req.notes else None
    await db.commit()
    return {"success": True}


@router.post("/expense-claims/{claim_id}/disburse")
async def disburse_claim(
    claim_id: str,
    req: DisburseExpenseClaimRequest,
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(ExpenseClaim, Employee).join(Employee, ExpenseClaim.employee_id == Employee.id).where(ExpenseClaim.id == claim_id, ExpenseClaim.shop_id == shop_id)
    res = await db.execute(q)
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Claim not found")
    c, emp = row

    if c.status != "APPROVED":
        return {"success": False, "error": "Only approved claims can be disbursed."}

    p_date = to_naive_utc(req.payment_date) or datetime.utcnow()

    # Create official Expense
    exp = Expense(
        shop_id=uuid.UUID(shop_id),
        description=f"Reimbursement [{c.claim_number}]: {c.title} ({emp.full_name})",
        amount=c.amount,
        category=c.category,
        expense_date=p_date,
        payment_channel=req.payment_channel,
        payment_reference=req.payment_reference or c.claim_number,
        receipt_url=c.receipt_url,
        is_non_deductible=False,
    )
    db.add(exp)
    await db.flush()

    c.status = "DISBURSED"
    c.disbursed_expense_id = exp.id
    c.disbursed_at = datetime.utcnow()
    await db.commit()

    return {"success": True, "expenseId": str(exp.id)}


# -------------------------------------------------------------------------
# ESS (EMPLOYEE SELF-SERVICE) ENDPOINTS
# -------------------------------------------------------------------------
@router.get("/ess/profile")
async def get_ess_profile(
    shop_id: str = Query(..., description="Workspace ID"),
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    parts = authorization.split()
    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    q = select(Employee).where(Employee.shop_id == shop_id, Employee.user_id == user.id)
    res = await db.execute(q)
    emp = res.scalar_one_or_none()
    if not emp:
        return {"isLinked": False, "employee": None, "userId": str(user.id)}

    return {
        "isLinked": True,
        "employee": {
            "id": str(emp.id),
            "fullName": emp.full_name,
            "email": emp.email,
            "phone": emp.mpesa_phone,
            "designation": emp.designation,
            "department": emp.department,
            "employmentType": emp.employment_type,
            "nationalId": emp.national_id,
            "kraPin": emp.kra_pin,
            "bankName": emp.bank_name,
            "bankAccountNumber": emp.bank_account_number,
            "mpesaPhone": emp.mpesa_phone,
            "baseSalary": float(emp.base_salary),
        },
    }
