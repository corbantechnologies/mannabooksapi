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
