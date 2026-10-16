import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
from app.core.schemas import ApiModel


class RecordPaymentRequest(ApiModel):
    document_id: uuid.UUID
    amount: Decimal = Field(..., gt=0)
    payment_channel: Literal["MPESA", "BANK", "CASH", "CHEQUE", "OTHER"] = "BANK"
    payment_reference: Optional[str] = None
    payment_date: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("payment_date", mode="before")
    @classmethod
    def normalize_payment_date(cls, v: Any) -> Any:
        from app.core.datetime_utils import to_naive_utc
        return to_naive_utc(v)


class MpesaC2BCallbackRequest(ApiModel):
    TransactionType: str = "Pay Bill"
    TransID: str
    TransTime: str
    TransAmount: str
    BusinessShortCode: str
    BillRefNumber: str
    MSISDN: str
    FirstName: Optional[str] = None
    MiddleName: Optional[str] = None
    LastName: Optional[str] = None


class PaymentResponse(ApiModel):
    id: uuid.UUID
    document_id: uuid.UUID
    shop_id: uuid.UUID
    amount: Decimal
    payment_date: datetime
    payment_channel: str
    payment_reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentReceiptSummary(ApiModel):
    payment: PaymentResponse
    document_id: uuid.UUID
    doc_number: str
    grand_total: Decimal
    total_paid: Decimal
    remaining_balance: Decimal
    status: str
