import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
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

