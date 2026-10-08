import uuid
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.reports.schemas import (
    PLStatementResponse,
    BalanceSheetResponse,
    TrialBalanceResponse,
    VatReturnSummaryResponse,
    StockLedgerEntryResponse,
    StockValuationRowResponse,
)
from app.modules.reports.service import (
    get_pl_statement,
    get_balance_sheet,
    get_trial_balance,
    get_vat_return_summary,
    get_stock_ledger_report,
    get_stock_valuation_report,
)

router = APIRouter(prefix="/reports", tags=["Financial Reports & Statutory Returns"], dependencies=[Depends(verify_internal_api_key)])


@router.get("/profit-loss", response_model=PLStatementResponse)
async def get_pl_statement_endpoint(
    shop_id: uuid.UUID = Query(...),
    period: str = Query("THIS_MONTH"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_pl_statement(
            db, shop_id=shop_id, period=period, start_date=start_date, end_date=end_date
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Profit & Loss statement: {str(e)}",
        )


@router.get("/balance-sheet", response_model=BalanceSheetResponse)
async def get_balance_sheet_endpoint(
    shop_id: uuid.UUID = Query(...),
    as_of_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_balance_sheet(db, shop_id=shop_id, as_of_date=as_of_date)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Balance Sheet: {str(e)}",
        )


@router.get("/trial-balance", response_model=TrialBalanceResponse)
async def get_trial_balance_endpoint(
    shop_id: uuid.UUID = Query(...),
    period_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_trial_balance(db, shop_id=shop_id, period_id=period_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Trial Balance: {str(e)}",
        )


@router.get("/vat-return", response_model=VatReturnSummaryResponse)
async def get_vat_return_endpoint(
    shop_id: uuid.UUID = Query(...),
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_vat_return_summary(
            db, shop_id=shop_id, start_date=start_date, end_date=end_date
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate VAT Return summary: {str(e)}",
        )


@router.get("/stock-ledger", response_model=List[StockLedgerEntryResponse])
async def get_stock_ledger_endpoint(
    shop_id: uuid.UUID = Query(...),
    product_id: Optional[uuid.UUID] = Query(None),
    location_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_stock_ledger_report(
            db,
            shop_id=shop_id,
            product_id=product_id,
            location_id=location_id,
            limit=limit,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Stock Ledger: {str(e)}",
        )


@router.get("/stock-valuation", response_model=List[StockValuationRowResponse])
async def get_stock_valuation_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_stock_valuation_report(db, shop_id=shop_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Stock Valuation: {str(e)}",
        )
