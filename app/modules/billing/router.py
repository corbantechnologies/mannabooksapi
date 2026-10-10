import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.identity import BillingTransaction, Shop, User

router = APIRouter(prefix="/billing", tags=["Subscription Billing & M-Pesa"])


class InitiateBillingRequest(BaseModel):
    shop_id: uuid.UUID
    checkout_request_id: str
    merchant_request_id: Optional[str] = None
    phone_number: str
    amount: Decimal
    target_plan: str = "PRO"
    billing_months: int = 1


class UpdateBillingStatusRequest(BaseModel):
    status: str
    result_code: Optional[int] = None
    result_desc: Optional[str] = None
    mpesa_receipt_number: Optional[str] = None


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


@router.post("/update-status/{transaction_id}", dependencies=[Depends(verify_internal_api_key)])
async def update_billing_status(
    transaction_id: uuid.UUID,
    req: UpdateBillingStatusRequest,
    db: AsyncSession = Depends(get_db),
):
    tx = await db.get(BillingTransaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Billing transaction not found")

    tx.status = req.status
    if req.result_code is not None:
        tx.result_code = req.result_code
    if req.result_desc is not None:
        tx.result_desc = req.result_desc
    if req.mpesa_receipt_number is not None:
        tx.mpesa_receipt_number = req.mpesa_receipt_number
    if req.status == "COMPLETED":
        tx.completed_at = datetime.utcnow()

        # Activate shop plan
        shop = await db.get(Shop, tx.shop_id)
        if shop:
            shop.plan = tx.target_plan
            shop.subscription_status = "ACTIVE"
            now = datetime.utcnow()
            days = 30 * tx.billing_months
            shop.subscription_expires_at = now + timedelta(days=days)

    await db.commit()
    return {"success": True}


@router.post("/mpesa-callback")
async def mpesa_stk_callback(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    """Handle Daraja STK Push callback idempotently."""
    try:
        body = payload.get("Body", {}).get("stkCallback", {})
        checkout_id = body.get("CheckoutRequestID")
        result_code = body.get("ResultCode", -1)
        result_desc = body.get("ResultDesc", "")

        if not checkout_id:
            return {"ResultCode": 0, "ResultDesc": "Ignored, missing checkout ID"}

        q = select(BillingTransaction).where(BillingTransaction.checkout_request_id == checkout_id)
        res = await db.execute(q)
        tx = res.scalar_one_or_none()

        if not tx:
            return {"ResultCode": 0, "ResultDesc": "Transaction not found"}

        if result_code == 0:
            receipt = None
            items = body.get("CallbackMetadata", {}).get("Item", [])
            for it in items:
                if it.get("Name") == "MpesaReceiptNumber":
                    receipt = str(it.get("Value"))
                    break

            tx.status = "COMPLETED"
            tx.result_code = 0
            tx.result_desc = result_desc
            tx.mpesa_receipt_number = receipt or f"MP{int(datetime.utcnow().timestamp())}"
            tx.completed_at = datetime.utcnow()

            # Activate shop subscription
            shop = await db.get(Shop, tx.shop_id)
            if shop:
                shop.plan = tx.target_plan
                shop.subscription_status = "ACTIVE"
                days = 30 * tx.billing_months
                shop.subscription_expires_at = datetime.utcnow() + timedelta(days=days)

        else:
            tx.status = "FAILED"
            tx.result_code = result_code
            tx.result_desc = result_desc

        await db.commit()
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    except Exception as e:
        return {"ResultCode": 1, "ResultDesc": f"Error: {str(e)}"}
