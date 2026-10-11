import uuid
from datetime import datetime
from app.core.datetime_utils import to_naive_utc
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.accounts.schemas import AccountCreateRequest, AccountResponse
from app.modules.accounts.service import list_accounts, create_account

router = APIRouter(prefix="/accounts", tags=["Chart of Accounts"], dependencies=[Depends(verify_internal_api_key)])


@router.get("", response_model=List[AccountResponse])
async def list_accounts_endpoint(
    shop_id: uuid.UUID = Query(...),
    account_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await list_accounts(db, shop_id=shop_id, account_type=account_type)


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account_endpoint(
    req: AccountCreateRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await create_account(db, shop_id=shop_id, req=req)


@router.get("/fiscal-years")
async def list_fiscal_years_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, desc
    from app.models.account import FiscalYear

    stmt = select(FiscalYear).where(FiscalYear.shop_id == shop_id).order_by(desc(FiscalYear.start_date))
    res = await db.execute(stmt)
    fys = res.scalars().all()
    return {
        "success": True,
        "fiscalYears": [
            {
                "id": str(fy.id),
                "label": fy.label,
                "startDate": str(fy.start_date),
                "endDate": str(fy.end_date),
                "isClosed": bool(fy.is_closed),
                "isCurrent": bool(fy.is_current),
            }
            for fy in fys
        ],
    }


class DeclareFiscalYearRequest(BaseModel):
    shop_id: uuid.UUID
    label: str
    startDate: Optional[str] = None
    start_date: Optional[str] = None
    endDate: Optional[str] = None
    end_date: Optional[str] = None


class CloseFiscalYearRequest(BaseModel):
    shop_id: uuid.UUID


@router.post("/fiscal-years", status_code=status.HTTP_201_CREATED)
async def declare_fiscal_year_endpoint(
    req: DeclareFiscalYearRequest,
    db: AsyncSession = Depends(get_db),
):
    from datetime import date
    from app.models.account import FiscalYear

    s_str = req.startDate or req.start_date
    e_str = req.endDate or req.end_date
    if not s_str or not e_str:
        raise HTTPException(status_code=400, detail="Start date and end date are required")

    try:
        s_date = date.fromisoformat(s_str[:10])
        e_date = date.fromisoformat(e_str[:10])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD")

    fy = FiscalYear(
        shop_id=req.shop_id,
        label=req.label.strip(),
        start_date=s_date,
        end_date=e_date,
        is_closed=False,
        is_current=False,
    )
    db.add(fy)
    await db.commit()
    await db.refresh(fy)

    return {
        "success": True,
        "fiscalYear": {
            "id": str(fy.id),
            "label": fy.label,
            "startDate": str(fy.start_date),
            "endDate": str(fy.end_date),
            "isClosed": fy.is_closed,
            "isCurrent": fy.is_current,
        },
    }


@router.post("/fiscal-years/{fy_id}/close")
async def close_fiscal_year_endpoint(
    fy_id: uuid.UUID,
    req: CloseFiscalYearRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.models.account import FiscalYear

    fy = await db.get(FiscalYear, fy_id)
    if not fy or fy.shop_id != req.shop_id:
        raise HTTPException(status_code=404, detail="Fiscal year not found")

    fy.is_closed = True
    fy.is_current = False
    await db.commit()
    await db.refresh(fy)

    return {"success": True, "message": f"Fiscal Year '{fy.label}' closed successfully."}


@router.delete("/fiscal-years/{fy_id}")
async def delete_fiscal_year_endpoint(
    fy_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models.account import FiscalYear

    fy = await db.get(FiscalYear, fy_id)
    if not fy or fy.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Fiscal year not found")

    if fy.is_closed:
        raise HTTPException(status_code=400, detail="Closed fiscal years cannot be deleted")

    await db.delete(fy)
    await db.commit()

    return {"success": True, "message": "Fiscal year deleted successfully."}



class CreateJournalEntryRequest(BaseModel):
    entry_date: Optional[datetime] = None
    description: str
    debit_account_id: uuid.UUID
    credit_account_id: uuid.UUID
    amount: Decimal
    reference_number: Optional[str] = None
    cost_center_id: Optional[uuid.UUID] = None
    backdated_reason: Optional[str] = None
    created_by_id: Optional[uuid.UUID] = None


@router.get("/journal-entries")
async def list_journal_entries_endpoint(
    shop_id: uuid.UUID = Query(...),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, desc
    from sqlalchemy.orm import aliased
    from app.models.journal import JournalEntry
    from app.models.account import ChartOfAccount
    from app.models.identity import User
    from app.models.operations import CostCenter

    DebitAcc = aliased(ChartOfAccount)
    CreditAcc = aliased(ChartOfAccount)

    stmt = (
        select(
            JournalEntry,
            DebitAcc.code.label("debit_code"),
            DebitAcc.name.label("debit_name"),
            CreditAcc.code.label("credit_code"),
            CreditAcc.name.label("credit_name"),
            User.name.label("created_by_name"),
            CostCenter.code.label("cost_center_code"),
            CostCenter.name.label("cost_center_name"),
        )
        .outerjoin(DebitAcc, JournalEntry.debit_account_id == DebitAcc.id)
        .outerjoin(CreditAcc, JournalEntry.credit_account_id == CreditAcc.id)
        .outerjoin(User, JournalEntry.created_by_id == User.id)
        .outerjoin(CostCenter, JournalEntry.cost_center_id == CostCenter.id)
        .where(JournalEntry.shop_id == shop_id)
        .order_by(desc(JournalEntry.entry_date), desc(JournalEntry.created_at))
        .limit(limit)
    )
    res = await db.execute(stmt)
    rows = res.all()
    entries = []
    for je, d_code, d_name, c_code, c_name, u_name, cc_code, cc_name in rows:
        entries.append({
            "id": str(je.id),
            "entryDate": je.entry_date.isoformat() if je.entry_date else "",
            "description": je.description,
            "debitAccountCode": d_code or "",
            "debitAccountName": d_name or "",
            "creditAccountCode": c_code or "",
            "creditAccountName": c_name or "",
            "amount": str(je.amount),
            "sourceType": je.source_type,
            "periodName": None,
            "isBackdated": bool(je.is_backdated),
            "backdatedReason": je.backdated_reason,
            "createdByName": u_name,
            "costCenterCode": cc_code,
            "costCenterName": cc_name,
        })
    return {"success": True, "entries": entries}


@router.post("/journal-entries", status_code=status.HTTP_201_CREATED)
async def create_journal_entry_endpoint(
    req: CreateJournalEntryRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models.journal import JournalEntry
    from app.models.account import ChartOfAccount

    # Validate accounts
    debit_acc = await db.get(ChartOfAccount, req.debit_account_id)
    credit_acc = await db.get(ChartOfAccount, req.credit_account_id)
    if not debit_acc or not credit_acc:
        raise HTTPException(status_code=400, detail="Invalid debit or credit account ID")

    now = datetime.utcnow()
    entry_date = req.entry_date or now
    is_backdated = (now - entry_date).days > 1 if entry_date < now else False

    entry = JournalEntry(
        shop_id=shop_id,
        entry_date=entry_date,
        description=req.description.strip(),
        debit_account_id=req.debit_account_id,
        credit_account_id=req.credit_account_id,
        amount=req.amount,
        reference_number=req.reference_number,
        cost_center_id=req.cost_center_id,
        source_type="manual",
        created_by_id=req.created_by_id,
        is_backdated=is_backdated,
        backdated_reason=req.backdated_reason if is_backdated else None,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {
        "success": True,
        "entry": {
            "id": str(entry.id),
            "entryDate": entry.entry_date.isoformat(),
            "description": entry.description,
            "amount": str(entry.amount),
        },
    }


@router.delete("/{account_id}")
async def delete_account_endpoint(
    account_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models.account import ChartOfAccount
    account = await db.get(ChartOfAccount, account_id)
    if not account or account.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system account")
    await db.delete(account)
    await db.commit()
    return {"success": True, "message": "Account deleted successfully"}


@router.get("/chart")
async def get_chart_of_accounts(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    accounts = await list_accounts(db, shop_id=shop_id)
    return {
        "success": True,
        "accounts": [
            {
                "id": str(a.id),
                "code": a.code,
                "name": a.name,
                "type": a.account_type,
                "accountType": a.account_type,
                "isSystem": bool(a.is_system),
                "parentCode": a.parent_code,
            }
            for a in accounts
        ],
    }


@router.get("/fiscal-years/{fy_id}")
async def get_fiscal_year_detail_endpoint(
    fy_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, and_, asc
    from app.models.account import FiscalYear, AccountingPeriod
    fy = await db.get(FiscalYear, fy_id)
    if not fy or fy.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Fiscal year not found")

    p_q = select(AccountingPeriod).where(AccountingPeriod.fiscal_year_id == fy_id).order_by(asc(AccountingPeriod.start_date))
    res_p = await db.execute(p_q)
    periods = res_p.scalars().all()

    fy_dict = {
        "id": str(fy.id),
        "label": fy.label,
        "startDate": str(fy.start_date),
        "endDate": str(fy.end_date),
        "isClosed": bool(fy.is_closed),
        "isCurrent": bool(fy.is_current),
    }

    return {
        "success": True,
        "fiscalYear": fy_dict,
        "periods": [
            {
                "id": str(p.id),
                "periodName": p.period_name,
                "startDate": str(p.start_date),
                "endDate": str(p.end_date),
                "status": p.status,
                "closedAt": p.closed_at.isoformat() if p.closed_at else None,
                "fiscalYearId": str(p.fiscal_year_id) if p.fiscal_year_id else None,
                "fiscalYearLabel": fy.label,
            }
            for p in periods
        ],
        "kpis": {
            "totalRevenue": 0,
            "totalExpenses": 0,
            "netIncome": 0,
            "entryCount": 0,
        },
        "revenueAccounts": [],
        "topExpenseAccounts": [],
        "closingChecklist": [],
    }


@router.post("/fiscal-years/{fy_id}/reopen")
async def reopen_fiscal_year_endpoint(
    fy_id: uuid.UUID,
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.account import FiscalYear
    fy = await db.get(FiscalYear, fy_id)
    if not fy:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    fy.is_closed = False
    await db.commit()
    await db.refresh(fy)
    return {"success": True, "message": f"Fiscal year '{fy.label}' reopened."}


@router.get("/periods")
async def list_accounting_periods_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, and_, desc
    from app.models.account import AccountingPeriod, FiscalYear
    q = select(AccountingPeriod).where(AccountingPeriod.shop_id == shop_id).order_by(desc(AccountingPeriod.start_date))
    res = await db.execute(q)
    periods = res.scalars().all()

    fy_ids = {p.fiscal_year_id for p in periods if p.fiscal_year_id}
    fy_map = {}
    if fy_ids:
        fys_q = select(FiscalYear).where(FiscalYear.id.in_(fy_ids))
        fys_res = await db.execute(fys_q)
        fy_map = {fy.id: fy for fy in fys_res.scalars().all()}

    return {
        "success": True,
        "periods": [
            {
                "id": str(p.id),
                "periodName": p.period_name,
                "startDate": str(p.start_date),
                "endDate": str(p.end_date),
                "status": p.status,
                "closedAt": p.closed_at.isoformat() if p.closed_at else None,
                "closedByName": None,
                "fiscalYearId": str(p.fiscal_year_id) if p.fiscal_year_id else None,
                "fiscalYearLabel": fy_map[p.fiscal_year_id].label if p.fiscal_year_id in fy_map else "Unassigned",
                "fiscalYear": {
                    "id": str(p.fiscal_year_id),
                    "label": fy_map[p.fiscal_year_id].label,
                } if p.fiscal_year_id in fy_map else None,
            }
            for p in periods
        ],
    }


@router.get("/periods/{period_id}/details")
async def get_period_details_endpoint(
    period_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.account import AccountingPeriod
    from app.models.journal import JournalEntry
    p = await db.get(AccountingPeriod, period_id)
    if not p:
        raise HTTPException(status_code=404, detail="Accounting period not found")

    je_q = select(JournalEntry).where(JournalEntry.period_id == period_id).limit(100)
    res_je = await db.execute(je_q)
    entries = res_je.scalars().all()

    return {
        "success": True,
        "period": {
            "id": str(p.id),
            "periodName": p.period_name,
            "startDate": str(p.start_date),
            "endDate": str(p.end_date),
            "status": p.status,
            "closedAt": p.closed_at.isoformat() if p.closed_at else None,
        },
        "kpis": {
            "entryCount": len(entries),
            "totalRevenue": 0,
            "totalExpenses": 0,
            "netIncome": 0,
            "isBalanced": True,
            "totalDebits": 0,
            "totalCredits": 0,
        },
        "journalEntries": entries,
    }


@router.post("/periods/{period_id}/close")
async def close_period_endpoint(
    period_id: uuid.UUID,
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.account import AccountingPeriod
    p = await db.get(AccountingPeriod, period_id)
    if not p:
        raise HTTPException(status_code=404, detail="Accounting period not found")
    p.status = "CLOSED"
    p.closed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(p)
    return {"success": True, "message": f"Period '{p.period_name}' closed successfully."}


@router.post("/periods/{period_id}/reopen")
async def reopen_period_endpoint(
    period_id: uuid.UUID,
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.account import AccountingPeriod
    p = await db.get(AccountingPeriod, period_id)
    if not p:
        raise HTTPException(status_code=404, detail="Accounting period not found")
    p.status = "OPEN"
    p.closed_at = None
    await db.commit()
    await db.refresh(p)
    return {"success": True, "message": f"Period '{p.period_name}' reopened."}


@router.post("/activate-gl")
async def activate_gl_endpoint(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.identity import Shop
    sid = uuid.UUID(req["shop_id"]) if isinstance(req["shop_id"], str) else req["shop_id"]
    shop = await db.get(Shop, sid)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    shop.is_gl_enabled = True
    await db.commit()
    return {"success": True, "message": "General Ledger activated successfully"}


@router.post("/disable-onboarding")
async def disable_onboarding_endpoint(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from app.models.identity import Shop
    sid = uuid.UUID(req["shop_id"]) if isinstance(req["shop_id"], str) else req["shop_id"]
    shop = await db.get(Shop, sid)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    shop.gl_onboarding_mode = False
    await db.commit()
    return {"success": True, "message": "Onboarding mode disabled"}


@router.post("/run-gl-migration")
async def run_gl_migration_endpoint(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import and_
    from app.models.identity import Shop
    from app.models.account import ChartOfAccount
    sid = uuid.UUID(req["shop_id"]) if isinstance(req["shop_id"], str) else req["shop_id"]
    shop = await db.get(Shop, sid)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    
    default_accounts = [
        {"code": "1000", "name": "Current Assets", "account_type": "ASSET", "is_system": True},
        {"code": "1200", "name": "Cash and Bank", "account_type": "ASSET", "is_system": True},
        {"code": "1300", "name": "Accounts Receivable", "account_type": "ASSET", "is_system": True},
        {"code": "1400", "name": "Inventory Asset", "account_type": "ASSET", "is_system": True},
        {"code": "2000", "name": "Current Liabilities", "account_type": "LIABILITY", "is_system": True},
        {"code": "2100", "name": "Accounts Payable", "account_type": "LIABILITY", "is_system": True},
        {"code": "2200", "name": "VAT Payable (Output VAT)", "account_type": "LIABILITY", "is_system": True},
        {"code": "3000", "name": "Owner's Equity", "account_type": "EQUITY", "is_system": True},
        {"code": "3100", "name": "Retained Earnings", "account_type": "EQUITY", "is_system": True},
        {"code": "3200", "name": "Opening Balances Equity", "account_type": "EQUITY", "is_system": True},
        {"code": "4000", "name": "Sales Revenue", "account_type": "REVENUE", "is_system": True},
        {"code": "5000", "name": "Cost of Goods Sold (COGS)", "account_type": "EXPENSE", "is_system": True},
        {"code": "6000", "name": "Operating Expenses", "account_type": "EXPENSE", "is_system": True},
    ]

    for da in default_accounts:
        chk = await db.execute(
            select(ChartOfAccount).where(
                and_(ChartOfAccount.shop_id == sid, ChartOfAccount.code == da["code"])
            )
        )
        if not chk.scalar_one_or_none():
            acc = ChartOfAccount(
                shop_id=sid,
                code=da["code"],
                name=da["name"],
                account_type=da["account_type"],
                is_system=da["is_system"],
            )
            db.add(acc)

    shop.is_gl_enabled = True
    await db.commit()
    return {"success": True, "message": "General Ledger migration completed successfully"}


@router.post("/compound-journal-entries", status_code=status.HTTP_201_CREATED)
async def create_compound_journal_entry_endpoint(
    req: dict,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models.journal import JournalEntry

    lines = req.get("lines") or []
    if not lines:
        raise HTTPException(status_code=400, detail="Journal entry lines required")

    entry_date = to_naive_utc(req.get("entry_date")) or datetime.utcnow()
    desc_text = (req.get("description") or "Compound Journal Entry").strip()
    ref_num = req.get("reference_number")
    cc_id = uuid.UUID(req["cost_center_id"]) if req.get("cost_center_id") else None

    debits = [l for l in lines if Decimal(str(l.get("debit_amount") or 0)) > 0]
    credits = [l for l in lines if Decimal(str(l.get("credit_amount") or 0)) > 0]

    if not debits or not credits:
        raise HTTPException(status_code=400, detail="Compound entry must contain both debits and credits")

    created_entries = []
    if len(debits) == 1:
        d_acc = uuid.UUID(debits[0]["account_id"])
        for c in credits:
            amt = Decimal(str(c.get("credit_amount") or 0))
            je = JournalEntry(
                shop_id=shop_id,
                entry_date=entry_date,
                description=c.get("line_description") or desc_text,
                debit_account_id=d_acc,
                credit_account_id=uuid.UUID(c["account_id"]),
                amount=amt,
                reference_number=ref_num,
                cost_center_id=cc_id,
                source_type="manual",
            )
            db.add(je)
            created_entries.append(je)
    elif len(credits) == 1:
        c_acc = uuid.UUID(credits[0]["account_id"])
        for d in debits:
            amt = Decimal(str(d.get("debit_amount") or 0))
            je = JournalEntry(
                shop_id=shop_id,
                entry_date=entry_date,
                description=d.get("line_description") or desc_text,
                debit_account_id=uuid.UUID(d["account_id"]),
                credit_account_id=c_acc,
                amount=amt,
                reference_number=ref_num,
                cost_center_id=cc_id,
                source_type="manual",
            )
            db.add(je)
            created_entries.append(je)
    else:
        for d in debits:
            amt = Decimal(str(d.get("debit_amount") or 0))
            c_acc = uuid.UUID(credits[0]["account_id"])
            je = JournalEntry(
                shop_id=shop_id,
                entry_date=entry_date,
                description=d.get("line_description") or desc_text,
                debit_account_id=uuid.UUID(d["account_id"]),
                credit_account_id=c_acc,
                amount=amt,
                reference_number=ref_num,
                cost_center_id=cc_id,
                source_type="manual",
            )
            db.add(je)
            created_entries.append(je)

    await db.commit()
    return {"success": True, "count": len(created_entries), "reference": ref_num}


@router.post("/opening-balances", status_code=status.HTTP_201_CREATED)
async def create_opening_balances_endpoint(
    req: dict,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import and_
    from app.models.journal import JournalEntry
    from app.models.account import ChartOfAccount

    as_of = to_naive_utc(req.get("as_of_date")) or datetime.utcnow()
    lines = req.get("lines") or []

    eq_q = select(ChartOfAccount).where(
        and_(ChartOfAccount.shop_id == shop_id, ChartOfAccount.code == "3200")
    )
    eq_acc = (await db.execute(eq_q)).scalar_one_or_none()
    if not eq_acc:
        eq_acc = ChartOfAccount(
            shop_id=shop_id,
            code="3200",
            name="Opening Balances Equity",
            account_type="EQUITY",
            is_system=True,
        )
        db.add(eq_acc)
        await db.flush()

    entries = []
    for line in lines:
        acc_id = uuid.UUID(line["accountId"]) if line.get("accountId") else None
        if not acc_id or acc_id == eq_acc.id:
            continue
        debit = Decimal(str(line.get("debit") or 0))
        credit = Decimal(str(line.get("credit") or 0))

        if debit > 0:
            je = JournalEntry(
                shop_id=shop_id,
                entry_date=as_of,
                description="Opening Balance for Account",
                debit_account_id=acc_id,
                credit_account_id=eq_acc.id,
                amount=debit,
                reference_number="OPENING-BALANCES",
                source_type="opening_balance",
            )
            db.add(je)
            entries.append(je)
        elif credit > 0:
            je = JournalEntry(
                shop_id=shop_id,
                entry_date=as_of,
                description="Opening Balance for Account",
                debit_account_id=eq_acc.id,
                credit_account_id=acc_id,
                amount=credit,
                reference_number="OPENING-BALANCES",
                source_type="opening_balance",
            )
            db.add(je)
            entries.append(je)

    await db.commit()
    return {"success": True, "createdCount": len(entries)}



