import uuid
from datetime import datetime
from app.core.datetime_utils import to_naive_utc
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select, and_, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key, get_acting_user_id
from app.modules.accounts.schemas import AccountCreateRequest, AccountResponse
from app.modules.accounts.service import list_accounts, create_account
from app.core.datetime_utils import utcnow

router = APIRouter(prefix="/accounts", tags=["Chart of Accounts"], dependencies=[Depends(verify_internal_api_key)])


async def _seed_if_needed(db: AsyncSession, shop_id: uuid.UUID) -> None:
    """GL workspaces must always have the standard chart (older activations never created it)."""
    from app.models.identity import Shop
    from app.modules.accounts.gl_service import ensure_default_accounts

    shop = await db.get(Shop, shop_id)
    if shop and shop.is_gl_enabled:
        before = len(await list_accounts(db, shop_id=shop_id))
        accounts = await ensure_default_accounts(db, shop_id)
        if len(accounts) != before:
            await db.commit()


@router.get("", response_model=List[AccountResponse])
async def list_accounts_endpoint(
    shop_id: uuid.UUID = Query(...),
    account_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _seed_if_needed(db, shop_id)
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

    from app.models.account import AccountingPeriod
    if (await db.execute(select(AccountingPeriod.id).where(AccountingPeriod.fiscal_year_id == fy.id, AccountingPeriod.status == "CLOSED"))).first():
        raise HTTPException(status_code=400, detail="Reopen its closed periods before deleting this fiscal year")
    await db.execute(delete(AccountingPeriod).where(AccountingPeriod.fiscal_year_id == fy.id))
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


@router.get("/chart")
async def get_chart_of_accounts(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    await _seed_if_needed(db, shop_id)
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
    from app.modules.accounts.gl_service import ensure_default_accounts

    shop.is_gl_enabled = True
    accounts = await ensure_default_accounts(db, sid)
    await db.commit()
    return {"success": True, "message": "General Ledger activated successfully", "accountsCount": len(accounts)}


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
    from app.modules.accounts.gl_service import run_full_gl_migration
    sid = uuid.UUID(req["shop_id"]) if isinstance(req["shop_id"], str) else req["shop_id"]
    try:
        return await run_full_gl_migration(db, sid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


