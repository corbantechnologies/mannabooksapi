import logging
import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key, require_internal_service, get_acting_user_id
from app.modules.payments.schemas import (
    RecordPaymentRequest,
    PaymentResponse,
    PaymentReceiptSummary,
)
from app.modules.payments.service import (
    record_payment,
    get_document_payments,
    delete_payment,
)

logger = logging.getLogger("mannabooks.payments")

router = APIRouter(prefix="/payments", tags=["Payments & Settlements"])


@router.post("/record", response_model=PaymentReceiptSummary, dependencies=[Depends(verify_internal_api_key)])
async def record_payment_endpoint(
    request: Request,
    req: RecordPaymentRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await record_payment(db, shop_id=shop_id, req=req, user_id=get_acting_user_id(request))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/by-document/{document_id}", response_model=List[PaymentResponse], dependencies=[Depends(verify_internal_api_key)])
async def get_document_payments_endpoint(
    document_id: uuid.UUID,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await get_document_payments(db, shop_id=shop_id, document_id=document_id)


@router.post("/mpesa/c2b-callback", response_model=Dict[str, Any], dependencies=[Depends(require_internal_service)])
async def mpesa_c2b_callback_endpoint():
    """
    Disabled. MannaBooks' M-Pesa integration is only for subscription billing (STK Push to the
    MannaBooks paybill); workspaces do not have their own Daraja integration yet. Keeping this
    live would let a payment to the MannaBooks paybill mark a tenant's invoice as paid.
    `handle_mpesa_c2b_callback` is kept for the future per-workspace payments module.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Workspace M-Pesa C2B collection is not enabled.",
    )


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

