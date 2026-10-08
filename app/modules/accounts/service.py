import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, asc
from app.models.account import ChartOfAccount
from app.modules.accounts.schemas import AccountCreateRequest


async def list_accounts(
    db: AsyncSession,
    shop_id: uuid.UUID,
    account_type: Optional[str] = None,
) -> List[ChartOfAccount]:
    q = select(ChartOfAccount).where(ChartOfAccount.shop_id == shop_id)
    if account_type:
        q = q.where(ChartOfAccount.account_type == account_type)
    q = q.order_by(asc(ChartOfAccount.code))
    res = await db.execute(q)
    return list(res.scalars().all())


async def create_account(
    db: AsyncSession,
    shop_id: uuid.UUID,
    req: AccountCreateRequest,
) -> ChartOfAccount:
    account = ChartOfAccount(
        shop_id=shop_id,
        code=req.code.strip(),
        name=req.name.strip(),
        account_type=req.account_type,
        parent_code=req.parent_code.strip() if req.parent_code else None,
        is_system=False,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account
