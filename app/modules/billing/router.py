import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
import logging
from app.api.deps import verify_internal_api_key, require_internal_service

logger = logging.getLogger("mannabooks.billing")
from app.models.identity import BillingTransaction, Shop, User
from app.core.datetime_utils import utcnow
from app.core.schemas import ApiModel

router = APIRouter(prefix="/billing", tags=["Subscription Billing & M-Pesa"])


class InitiateBillingRequest(ApiModel):
    shop_id: uuid.UUID
    checkout_request_id: str
    merchant_request_id: Optional[str] = None
    phone_number: str
    amount: Decimal
    target_plan: str = "PRO"
    billing_months: int = 1


class UpdateBillingStatusRequest(ApiModel):
    status: str
    result_code: Optional[int] = None
    result_desc: Optional[str] = None
    mpesa_receipt_number: Optional[str] = None


def _activate_subscription(shop: Shop, tx: BillingTransaction) -> None:
    now = utcnow()
    # Extend from the current expiry if the plan is still running, otherwise from now.
    base = shop.subscription_expires_at if shop.subscription_expires_at and shop.subscription_expires_at > now else now
    shop.plan = tx.target_plan
    shop.subscription_status = "ACTIVE"
    shop.subscription_expires_at = base + timedelta(days=30 * tx.billing_months)


@router.post("/initiate", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_internal_api_key)])
async def initiate_billing_transaction(
    req: InitiateBillingRequest,
    db: AsyncSession = Depends(get_db),
):
    shop = await db.get(Shop, req.shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Workspace not found")

    tx = BillingTransaction(
        shop_id=req.shop_id,
        checkout_request_id=req.checkout_request_id,
        merchant_request_id=req.merchant_request_id,
        phone_number=req.phone_number,
        amount=req.amount,
        target_plan=req.target_plan.upper(),
        billing_months=req.billing_months,
        status="PENDING",
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    return {"success": True, "transactionId": str(tx.id)}


@router.get("/status/{transaction_id}", dependencies=[Depends(verify_internal_api_key)])
async def get_billing_status(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    tx = await db.get(BillingTransaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Billing transaction not found")

    return {
        "success": True,
        "status": tx.status,
        "mpesaReceipt": tx.mpesa_receipt_number,
        "targetPlan": tx.target_plan,
        "amount": str(tx.amount),
        "resultDesc": tx.result_desc,
    }


@router.post("/update-status/{transaction_id}", dependencies=[Depends(require_internal_service)])
async def update_billing_status(
    transaction_id: uuid.UUID,
    req: UpdateBillingStatusRequest,
    db: AsyncSession = Depends(get_db),
):
    tx = (
        await db.execute(select(BillingTransaction).where(BillingTransaction.id == transaction_id).with_for_update())
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Billing transaction not found")
    if tx.status != "PENDING":
        return {"success": True, "status": tx.status, "message": "Already processed"}
    if req.status not in ("COMPLETED", "FAILED", "CANCELLED"):
        raise HTTPException(status_code=400, detail="Invalid status")
    if req.status == "COMPLETED" and not req.mpesa_receipt_number:
        raise HTTPException(status_code=400, detail="A confirmed M-Pesa receipt is required to complete a payment")

    tx.status = req.status
    if req.result_code is not None:
        tx.result_code = req.result_code
    if req.result_desc is not None:
        tx.result_desc = req.result_desc
    if req.mpesa_receipt_number is not None:
        tx.mpesa_receipt_number = req.mpesa_receipt_number
    if req.status == "COMPLETED":
        tx.completed_at = utcnow()
        shop = await db.get(Shop, tx.shop_id)
        if shop:
            _activate_subscription(shop, tx)

    await db.commit()
    return {"success": True}


@router.post("/mpesa-callback", dependencies=[Depends(require_internal_service)])
async def mpesa_stk_callback(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    """
    Daraja STK Push callback, forwarded by the Next.js server after it verified the callback secret.
    Idempotent: only a PENDING transaction can change state, and the paid amount must match.
    """
    body = payload.get("Body", {}).get("stkCallback", {}) if isinstance(payload, dict) else {}
    checkout_id = body.get("CheckoutRequestID")
    result_code = body.get("ResultCode", -1)
    result_desc = str(body.get("ResultDesc", ""))[:500]

    if not checkout_id:
        return {"ResultCode": 0, "ResultDesc": "Ignored"}

    tx = (
        await db.execute(
            select(BillingTransaction)
            .where(BillingTransaction.checkout_request_id == checkout_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if not tx:
        logger.warning("STK callback for unknown CheckoutRequestID %s", checkout_id)
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    if tx.status != "PENDING":
        # Replayed or duplicate callback – never re-apply.
        return {"ResultCode": 0, "ResultDesc": "Already processed"}

    if result_code == 0:
        meta = {it.get("Name"): it.get("Value") for it in body.get("CallbackMetadata", {}).get("Item", []) if isinstance(it, dict)}
        receipt = meta.get("MpesaReceiptNumber")
        try:
            paid = Decimal(str(meta.get("Amount")))
        except Exception:
            paid = Decimal("0")

        if not receipt or paid < Decimal(str(tx.amount)):
            tx.status = "FAILED"
            tx.result_code = -2
            tx.result_desc = f"Amount/receipt verification failed (paid={paid}, expected={tx.amount})"
            logger.error("STK callback verification failed for tx %s: paid=%s expected=%s", tx.id, paid, tx.amount)
            await db.commit()
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        tx.status = "COMPLETED"
        tx.result_code = 0
        tx.result_desc = result_desc
        tx.mpesa_receipt_number = str(receipt)
        tx.completed_at = utcnow()

        shop = await db.get(Shop, tx.shop_id)
        if shop:
            _activate_subscription(shop, tx)
    else:
        tx.status = "FAILED"
        tx.result_code = int(result_code) if str(result_code).lstrip("-").isdigit() else -1
        tx.result_desc = result_desc

    await db.commit()
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
