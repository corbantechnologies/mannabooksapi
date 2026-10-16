"""Background worker for retrying queued or pending KRA eTIMS transmissions.

Follows exponential backoff and respects the global platform kill switch.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.core.config import get_settings
from app.core.clock import now_nairobi
from app.db.session import async_session_factory
from app.models.etims import EtimsConfig, EtimsTransmission
from app.modules.etims.crypto import decrypt_cmc_key
from app.modules.etims.client import KraEtimsClient
from app.core.datetime_utils import utcnow

logger = logging.getLogger("etims.worker")


async def process_queued_transmissions(
    db: AsyncSession,
    batch_size: int = 50,
) -> Dict[str, Any]:
    """Process a batch of queued eTIMS transmissions.

    Returns summary stats of processed, fiscalized, failed, and skipped records.
    """
    settings = get_settings()

    if not settings.ENABLE_KRA_ETIMS_GLOBAL:
        logger.info("eTIMS background retry worker skipped: globally disabled.")
        return {
            "processed": 0,
            "fiscalized": 0,
            "failed": 0,
            "skipped": 0,
            "reason": "ENABLE_KRA_ETIMS_GLOBAL is false",
        }

    now = utcnow()

    # Query queued records where next_retry_at is null or has elapsed
    q = (
        select(EtimsTransmission)
        .where(
            and_(
                EtimsTransmission.status == "QUEUED",
                EtimsTransmission.retry_count < 10,
                or_(
                    EtimsTransmission.next_retry_at == None,
                    EtimsTransmission.next_retry_at <= now,
                ),
            )
        )
        .order_by(EtimsTransmission.created_at.asc())
        .limit(batch_size)
    )

    res = await db.execute(q)
    transmissions: List[EtimsTransmission] = list(res.scalars().all())

    if not transmissions:
        return {"processed": 0, "fiscalized": 0, "failed": 0, "skipped": 0}

    client = KraEtimsClient()
    stats = {"processed": len(transmissions), "fiscalized": 0, "failed": 0, "skipped": 0}

    # Cache configs by shop_id to avoid redundant DB queries in same batch
    config_cache: Dict[str, EtimsConfig | None] = {}

    for tx in transmissions:
        shop_key = str(tx.shop_id)
        if shop_key not in config_cache:
            cfg_q = select(EtimsConfig).where(
                and_(EtimsConfig.shop_id == tx.shop_id, EtimsConfig.is_active == True)
            )
            cfg_res = await db.execute(cfg_q)
            config_cache[shop_key] = cfg_res.scalar_one_or_none()

        config = config_cache[shop_key]
        if not config:
            logger.warning("Skipping transmission %s: shop %s has no active eTIMS config.", tx.id, tx.shop_id)
            stats["skipped"] += 1
            continue

        try:
            cmc_key = decrypt_cmc_key(config.communication_key_encrypted)
            kra_res = await client.save_sales_transaction(tx.request_payload, cmc_key)

            if kra_res.get("resultCd") == "000":
                tx.status = "FISCALIZED"
                tx.status_code = "000"
                tx.response_payload = kra_res
                tx.error_message = None
                stats["fiscalized"] += 1
                logger.info("Transmission %s successfully fiscalized with KRA.", tx.id)
            else:
                tx.retry_count += 1
                tx.status_code = kra_res.get("resultCd")
                tx.error_message = kra_res.get("resultMsg", "Rejected by KRA")
                tx.response_payload = kra_res

                if tx.retry_count >= 10:
                    tx.status = "FAILED"
                    stats["failed"] += 1
                    logger.error("Transmission %s permanently failed after 10 retries.", tx.id)
                else:
                    # Exponential backoff: 2^retries minutes (capped at 4 hours)
                    delay_minutes = min(2 ** tx.retry_count, 240)
                    tx.next_retry_at = utcnow() + timedelta(minutes=delay_minutes)
                    stats["failed"] += 1
        except Exception as exc:
            tx.retry_count += 1
            tx.error_message = f"Network/worker error: {str(exc)}"
            delay_minutes = min(2 ** tx.retry_count, 240)
            tx.next_retry_at = utcnow() + timedelta(minutes=delay_minutes)
            stats["failed"] += 1
            logger.exception("Error processing transmission %s", tx.id)

    await db.commit()
    return stats


async def run_worker_loop(poll_interval_seconds: int = 30):
    """Continuous polling worker loop."""
    logger.info("Starting eTIMS background retry worker daemon (poll interval: %ss)...", poll_interval_seconds)
    while True:
        try:
            async with async_session_factory() as db:
                stats = await process_queued_transmissions(db)
                if stats["processed"] > 0:
                    logger.info("Worker batch cycle completed: %s", stats)
        except Exception as exc:
            logger.error("Unhandled error in eTIMS worker loop: %s", exc)

        await asyncio.sleep(poll_interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(run_worker_loop())
