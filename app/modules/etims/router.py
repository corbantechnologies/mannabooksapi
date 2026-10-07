import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.core.config import get_settings
from app.models.etims import EtimsConfig, EtimsTransmission
from app.modules.etims.schemas import (
    EtimsInitRequest,
    EtimsSalesTransmissionRequest,
    EtimsResponseEnvelope,
    EtimsStatusResponse,
)
from app.modules.etims.service import register_etims_device, transmit_fiscal_invoice
from app.workers.etims_worker import process_queued_transmissions

router = APIRouter(prefix="/etims", tags=["eTIMS Fiscalization"])


@router.post("/initialize", dependencies=[Depends(verify_internal_api_key)])
async def initialize_device_endpoint(
    req: EtimsInitRequest,
    db: AsyncSession = Depends(get_db),
):
    """Device Handshake & Initialization with KRA OSCU."""
    return await register_etims_device(db, req)


@router.post("/transmit", response_model=EtimsResponseEnvelope, dependencies=[Depends(verify_internal_api_key)])
async def transmit_invoice_endpoint(
    req: EtimsSalesTransmissionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Fiscalize Sales Invoice or POS Receipt with KRA."""
    return await transmit_fiscal_invoice(db, req)


@router.get("/status/{shop_id}", response_model=EtimsStatusResponse, dependencies=[Depends(verify_internal_api_key)])
async def get_shop_etims_status(
    shop_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Fetch shop eTIMS configuration status and queue health."""
    settings = get_settings()

    q_cfg = select(EtimsConfig).where(EtimsConfig.shop_id == shop_id)
    res_cfg = await db.execute(q_cfg)
    config = res_cfg.scalar_one_or_none()

    q_queued = select(func.count(EtimsTransmission.id)).where(
        and_(EtimsTransmission.shop_id == shop_id, EtimsTransmission.status == "QUEUED")
    )
    res_queued = await db.execute(q_queued)
    queued_count = res_queued.scalar() or 0

    if not config:
        return EtimsStatusResponse(
            is_active=False,
            global_enabled=settings.ENABLE_KRA_ETIMS_GLOBAL,
            queued_transmissions=queued_count,
        )

    return EtimsStatusResponse(
        is_active=config.is_active,
        global_enabled=settings.ENABLE_KRA_ETIMS_GLOBAL,
        kra_pin=config.kra_pin,
        branch_id=config.branch_id,
        device_serial=config.device_serial,
        sdc_id=config.sdc_id,
        mrc_no=config.mrc_no,
        environment=config.environment,
        last_sync_at=config.last_sync_at.isoformat() if config.last_sync_at else None,
        queued_transmissions=queued_count,
    )


@router.post("/worker/process-queue", dependencies=[Depends(verify_internal_api_key)])
async def trigger_worker_queue_processing(
    db: AsyncSession = Depends(get_db),
):
    """Manually or via cron trigger processing of queued eTIMS transmissions."""
    return await process_queued_transmissions(db)
