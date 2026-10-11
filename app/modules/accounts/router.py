import uuid
from datetime import datetime
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


