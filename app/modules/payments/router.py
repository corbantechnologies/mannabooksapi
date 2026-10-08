import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.payments.schemas import (
    RecordPaymentRequest,
    MpesaC2BCallbackRequest,
    PaymentResponse,
    PaymentReceiptSummary,
)
from app.modules.payments.service import (
    record_payment,
    get_document_payments,
    handle_mpesa_c2b_callback,
    delete_payment,
)

router = APIRouter(prefix="/payments", tags=["Payments & Settlements"])


@router.post("/record", response_model=PaymentReceiptSummary, dependencies=[Depends(verify_internal_api_key)])
async def record_payment_endpoint(
    req: RecordPaymentRequest,
    shop_id: uuid.UUID = Query(...),
    user_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await record_payment(db, shop_id=shop_id, req=req, user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to record payment: {str(e)}")


@router.get("/by-document/{document_id}", response_model=List[PaymentResponse], dependencies=[Depends(verify_internal_api_key)])
async def get_document_payments_endpoint(
    document_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await get_document_payments(db, shop_id=shop_id, document_id=document_id)


@router.post("/mpesa/c2b-callback", response_model=Dict[str, Any])
async def mpesa_c2b_callback_endpoint(
    req: MpesaC2BCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """Handle Daraja C2B confirmation callbacks idempotently."""
    try:
        return await handle_mpesa_c2b_callback(db, req)
    except Exception as e:
        # Never fail Safaricom callbacks with 500 if possible, log and accept to avoid Daraja queue jam
        return {"ResultCode": 1, "ResultDesc": f"Processing error: {str(e)}"}


@router.delete("/{payment_id}", response_model=Dict[str, Any], dependencies=[Depends(verify_internal_api_key)])
async def delete_payment_endpoint(
    payment_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await delete_payment(db, shop_id=shop_id, payment_id=payment_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete payment: {str(e)}")

