import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_cron_or_internal_key
from app.modules.crons.schemas import (
    OverdueSweepResponse,
    ProcessRecurringResponse,
    QuoteExpiryResponse,
    EtimsRetryCronResponse,
)
from app.modules.crons.service import (
    run_overdue_sweep,
    run_process_recurring,
    run_quote_expiry_sweep,
    run_etims_retry_daemon,
)

router = APIRouter(prefix="/crons", tags=["Lifecycle Automations & Crons"], dependencies=[Depends(verify_cron_or_internal_key)])


@router.post("/overdue-sweep", response_model=OverdueSweepResponse)
async def overdue_sweep_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_overdue_sweep(db, shop_id=shop_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute overdue sweep: {str(e)}",
        )


@router.post("/process-recurring", response_model=ProcessRecurringResponse)
async def process_recurring_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_process_recurring(db, shop_id=shop_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute recurring processor: {str(e)}",
        )


@router.post("/quote-expiry", response_model=QuoteExpiryResponse)
async def quote_expiry_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_quote_expiry_sweep(db, shop_id=shop_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute quote expiry sweep: {str(e)}",
        )


@router.post("/etims-retry", response_model=EtimsRetryCronResponse)
async def etims_retry_endpoint(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_etims_retry_daemon(db, limit=limit)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger eTIMS retry daemon: {str(e)}",
        )
