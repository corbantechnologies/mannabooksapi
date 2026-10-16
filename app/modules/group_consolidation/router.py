import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.group import GroupEntity, GroupMembership, InterCompanyTransaction
from app.models.identity import Shop, User
from app.models.document import Document
from app.models.bill import VendorBill
from app.modules.auth.service import verify_and_get_session
from app.modules.reports.service import get_pl_statement, get_balance_sheet

router = APIRouter(prefix="/group-consolidation", tags=["Holding Company & Group Consolidation"])


class CreateGroupRequest(BaseModel):
    name: str
    code: str
    reporting_currency: str = "KES"
    description: Optional[str] = None
    initial_shop_id: Optional[str] = None


class AddShopToGroupRequest(BaseModel):
    shop_id: uuid.UUID
    entity_type: str = "SUBSIDIARY"
    ownership_percentage: Decimal = Decimal("100.00")


class RecordInterCompanyTxRequest(BaseModel):
    group_id: uuid.UUID
    source_shop_id: uuid.UUID
    target_shop_id: uuid.UUID
    source_document_id: Optional[uuid.UUID] = None
    target_bill_id: Optional[uuid.UUID] = None
    amount: Decimal
    currency: str = "KES"
    transaction_type: str = "MANAGEMENT_SERVICES"
    notes: Optional[str] = None


def format_group_dict(g: GroupEntity) -> Dict[str, Any]:
    return {
        "id": str(g.id),
        "name": g.name,
        "code": g.code,
        "reportingCurrency": g.reporting_currency,
        "ownerId": str(g.owner_id),
        "description": g.description,
        "createdAt": g.created_at.isoformat() if g.created_at else None,
        "memberships": [
            {
                "id": str(m.id),
                "groupId": str(m.group_id),
                "shopId": str(m.shop_id),
                "entityType": m.entity_type,
                "ownershipPercentage": str(m.ownership_percentage),
                "joinedAt": m.joined_at.isoformat() if m.joined_at else None,
                "shop": {
                    "id": str(m.shop.id),
                    "name": m.shop.name,
                    "slug": m.shop.slug,
                    "code": m.shop.code,
                    "currency": m.shop.currency,
                } if m.shop else None,
            }
            for m in g.memberships
        ],
        "interCompanyTransactions": [
            {
                "id": str(tx.id),
                "groupId": str(tx.group_id),
                "sourceShopId": str(tx.source_shop_id),
                "targetShopId": str(tx.target_shop_id),
                "sourceDocumentId": str(tx.source_document_id) if tx.source_document_id else None,
                "targetBillId": str(tx.target_bill_id) if tx.target_bill_id else None,
                "amount": str(tx.amount),
                "currency": tx.currency,
                "transactionType": tx.transaction_type,
                "isEliminated": tx.is_eliminated,
                "notes": tx.notes,
                "createdAt": tx.created_at.isoformat() if tx.created_at else None,
                "sourceShop": {"id": str(tx.source_shop.id), "name": tx.source_shop.name} if tx.source_shop else None,
                "targetShop": {"id": str(tx.target_shop.id), "name": tx.target_shop.name} if tx.target_shop else None,
            }
            for tx in g.transactions
        ],
    }


async def resolve_user(request: Request) -> User:
    """The signed-in caller. Never trust a user id from the request."""
    principal = getattr(request.state, "principal", None)
    if not principal or not principal.user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A signed-in user is required.")
    return principal.user


@router.get("/groups")
async def list_groups(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await resolve_user(request)
    stmt = (
        select(GroupEntity)
        .where(GroupEntity.owner_id == user.id)
        .options(
            selectinload(GroupEntity.memberships).selectinload(GroupMembership.shop),
            selectinload(GroupEntity.transactions).selectinload(InterCompanyTransaction.source_shop),
            selectinload(GroupEntity.transactions).selectinload(InterCompanyTransaction.target_shop),
        )
        .order_by(desc(GroupEntity.created_at))
    )
    res = await db.execute(stmt)
    groups = res.scalars().all()
    return {"success": True, "groups": [format_group_dict(g) for g in groups]}


@router.get("/groups/{group_id}")
async def get_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(GroupEntity)
        .where(GroupEntity.id == group_id)
        .options(
            selectinload(GroupEntity.memberships).selectinload(GroupMembership.shop),
            selectinload(GroupEntity.transactions).selectinload(InterCompanyTransaction.source_shop),
            selectinload(GroupEntity.transactions).selectinload(InterCompanyTransaction.target_shop),
            selectinload(GroupEntity.transactions).selectinload(InterCompanyTransaction.source_document),
            selectinload(GroupEntity.transactions).selectinload(InterCompanyTransaction.target_bill),
        )
    )
    res = await db.execute(stmt)
    group = res.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group entity not found")
    return {"success": True, "group": format_group_dict(group)}


@router.post("/groups", status_code=status.HTTP_201_CREATED)
async def create_group(
    req: CreateGroupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await resolve_user(request)
    group = GroupEntity(
        name=req.name.strip(),
        code=req.code.strip().upper(),
        reporting_currency=req.reporting_currency.upper(),
        owner_id=user.id,
        description=req.description,
    )
    db.add(group)
    await db.flush()

    if req.initial_shop_id:
        init_shop = await db.get(Shop, uuid.UUID(req.initial_shop_id))
        if init_shop:
            mem = GroupMembership(
                group_id=group.id,
                shop_id=init_shop.id,
                entity_type="PARENT",
                ownership_percentage=Decimal("100.00"),
            )
            db.add(mem)

    await db.commit()
    await db.refresh(group)
    return {"success": True, "group": format_group_dict(group)}


@router.post("/groups/{group_id}/members")
async def add_shop_to_group(
    group_id: uuid.UUID,
    req: AddShopToGroupRequest,
    db: AsyncSession = Depends(get_db),
):
    group = await db.get(GroupEntity, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group entity not found")

    shop = await db.get(Shop, req.shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Workspace not found")

    mem = GroupMembership(
        group_id=group_id,
        shop_id=req.shop_id,
        entity_type=req.entity_type,
        ownership_percentage=req.ownership_percentage,
    )
    db.add(mem)
    await db.commit()
    return {"success": True}


@router.post("/transactions")
async def record_intercompany_tx(
    req: RecordInterCompanyTxRequest,
    db: AsyncSession = Depends(get_db),
):
    tx = InterCompanyTransaction(
        group_id=req.group_id,
        source_shop_id=req.source_shop_id,
        target_shop_id=req.target_shop_id,
        source_document_id=req.source_document_id,
        target_bill_id=req.target_bill_id,
        amount=req.amount,
        currency=req.currency,
        transaction_type=req.transaction_type,
        is_eliminated=True,
        notes=req.notes,
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    return {"success": True, "transactionId": str(tx.id)}


@router.get("/pnl")
async def get_consolidated_pnl(
    group_id: uuid.UUID = Query(...),
    period: str = Query("THIS_MONTH"),
    db: AsyncSession = Depends(get_db),
):
    group = await db.get(GroupEntity, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    q_m = select(GroupMembership).where(GroupMembership.group_id == group_id).options(selectinload(GroupMembership.shop))
    res_m = await db.execute(q_m)
    memberships = res_m.scalars().all()

    total_sales = Decimal("0.00")
    total_cogs = Decimal("0.00")
    total_gross = Decimal("0.00")
    total_expenses = Decimal("0.00")
    total_net = Decimal("0.00")
    entity_results = []

    for mem in memberships:
        try:
            pl = await get_pl_statement(db, shop_id=mem.shop_id, period=period)
            share = mem.ownership_percentage / Decimal("100.00")
            s_sales = pl.sales_revenue * share
            s_cogs = pl.cogs * share
            s_gross = pl.gross_profit * share
            s_exp = pl.total_operating_expenses * share
            s_net = pl.net_income * share

            total_sales += s_sales
            total_cogs += s_cogs
            total_gross += s_gross
            total_expenses += s_exp
            total_net += s_net

            entity_results.append({
                "shopId": str(mem.shop_id),
                "shopName": mem.shop.name if mem.shop else "",
                "entityType": mem.entity_type,
                "ownershipPercentage": str(mem.ownership_percentage),
                "salesRevenue": str(s_sales),
                "cogs": str(s_cogs),
                "grossProfit": str(s_gross),
                "totalOperatingExpenses": str(s_exp),
                "netIncome": str(s_net),
            })
        except Exception:
            continue

    return {
        "success": True,
        "groupId": str(group_id),
        "groupName": group.name,
        "currency": group.reporting_currency,
        "totalSales": str(total_sales),
        "totalCogs": str(total_cogs),
        "totalGrossProfit": str(total_gross),
        "totalExpenses": str(total_expenses),
        "totalNetProfit": str(total_net),
        "subsidiaries": entity_results,
    }


@router.get("/balance-sheet")
async def get_consolidated_balance_sheet(
    group_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    group = await db.get(GroupEntity, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    q_m = select(GroupMembership).where(GroupMembership.group_id == group_id).options(selectinload(GroupMembership.shop))
    res_m = await db.execute(q_m)
    memberships = res_m.scalars().all()

    total_assets = Decimal("0.00")
    total_liabilities = Decimal("0.00")
    total_equity = Decimal("0.00")
    entity_results = []

    for mem in memberships:
        try:
            bs = await get_balance_sheet(db, shop_id=mem.shop_id)
            share = mem.ownership_percentage / Decimal("100.00")
            s_assets = bs.total_assets * share
            s_liab = bs.total_liabilities * share
            s_eq = bs.total_equity * share

            total_assets += s_assets
            total_liabilities += s_liab
            total_equity += s_eq

            entity_results.append({
                "shopId": str(mem.shop_id),
                "shopName": mem.shop.name if mem.shop else "",
                "entityType": mem.entity_type,
                "ownershipPercentage": str(mem.ownership_percentage),
                "totalAssets": str(s_assets),
                "totalLiabilities": str(s_liab),
                "totalEquity": str(s_eq),
            })
        except Exception:
            continue

    return {
        "success": True,
        "groupId": str(group_id),
        "groupName": group.name,
        "currency": group.reporting_currency,
        "totalAssets": str(total_assets),
        "totalLiabilities": str(total_liabilities),
        "totalEquity": str(total_equity),
        "isBalanced": abs(total_assets - (total_liabilities + total_equity)) < Decimal("1.00"),
        "subsidiaries": entity_results,
    }


class ToggleEliminationRequest(BaseModel):
    is_eliminated: bool


@router.delete("/memberships/{membership_id}")
async def delete_group_membership(
    membership_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    mem = await db.get(GroupMembership, membership_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Membership not found")
    await db.delete(mem)
    await db.commit()
    return {"success": True, "message": "Membership removed successfully"}


@router.patch("/transactions/{tx_id}/elimination")
async def toggle_tx_elimination(
    tx_id: uuid.UUID,
    req: ToggleEliminationRequest,
    db: AsyncSession = Depends(get_db),
):
    tx = await db.get(InterCompanyTransaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    tx.is_eliminated = req.is_eliminated
    await db.commit()
    await db.refresh(tx)
    return {"success": True, "isEliminated": tx.is_eliminated}

