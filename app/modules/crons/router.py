import logging
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
from app.core.datetime_utils import utcnow

router = APIRouter(prefix="/crons", tags=["Lifecycle Automations & Crons"], dependencies=[Depends(verify_cron_or_internal_key)])
logger = logging.getLogger("mannabooks.api")


@router.post("/overdue-sweep", response_model=OverdueSweepResponse)
async def overdue_sweep_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_overdue_sweep(db, shop_id=shop_id)
    except Exception:
        logger.exception("Failed to execute overdue sweep")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute overdue sweep. Please try again or contact support.",
        )


@router.post("/process-recurring", response_model=ProcessRecurringResponse)
async def process_recurring_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_process_recurring(db, shop_id=shop_id)
    except Exception:
        logger.exception("Failed to execute recurring processor")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute recurring processor. Please try again or contact support.",
        )


@router.post("/quote-expiry", response_model=QuoteExpiryResponse)
async def quote_expiry_endpoint(
    shop_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_quote_expiry_sweep(db, shop_id=shop_id)
    except Exception:
        logger.exception("Failed to execute quote expiry sweep")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute quote expiry sweep. Please try again or contact support.",
        )


@router.post("/etims-retry", response_model=EtimsRetryCronResponse)
async def etims_retry_endpoint(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await run_etims_retry_daemon(db, limit=limit)
    except Exception:
        logger.exception("Failed to trigger eTIMS retry daemon")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to trigger eTIMS retry daemon. Please try again or contact support.",
        )


@router.post("/subscription-lifecycle")
async def subscription_lifecycle_endpoint(
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime
    from sqlalchemy import select, and_
    from app.models.identity import Shop, User

    now = utcnow()
    # Find shops whose subscription expired
    q = select(Shop).where(
        and_(
            Shop.subscription_expires_at.isnot(None),
            Shop.subscription_expires_at < now,
            Shop.subscription_status == "ACTIVE",
            Shop.is_lifetime_pro == False,
        )
    )
    res = await db.execute(q)
    expired_shops = res.scalars().all()

    soft_locks_executed = 0
    for shop in expired_shops:
        shop.subscription_status = "EXPIRED"
        soft_locks_executed += 1

    await db.commit()
    return {
        "success": True,
        "preRenewalAlertsSent": 0,
        "renewalStkPromptsTriggered": 0,
        "gracePeriodsEntered": 0,
        "softLocksExecuted": soft_locks_executed,
    }

