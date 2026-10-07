import uuid
from typing import Optional, List
from pydantic import BaseModel, Field


class EtimsInitRequest(BaseModel):
    shop_id: uuid.UUID
    kra_pin: str = Field(..., pattern=r"^[A-Z0-9]{11}$", description="Kenyan KRA PIN (11 chars)")
    branch_id: str = "00"
    device_serial: str
    communication_key: str
    environment: str = "SANDBOX"


class EtimsItemPayload(BaseModel):
    itemSeq: int
    itemCd: str
    itemClsCd: str = "24101601"
    itemNm: str
    pkgUnitCd: str = "PCE"
    qty: float
    prc: float
    splyAmt: float
    dcRt: float = 0.0
    dcAmt: float = 0.0
    taxTyCd: str = "A"  # A (16%), B (0%), C (Exempt), D (Non-VAT), E (8%)
    taxblAmt: float
    taxAmt: float
    totAmt: float


class EtimsSalesTransmissionRequest(BaseModel):
    shop_id: uuid.UUID
    document_id: uuid.UUID
    customer_tin: Optional[str] = None
    customer_name: str = "Walk-In Customer"
    sales_type_code: str = "N"  # N (Normal), C (Credit Note)
    receipt_type_code: str = "S"  # S (Sale), R (Refund)
    payment_type_code: str = "01"  # 01 Cash, 02 Credit, 03 M-Pesa, 04 Bank
    items: List[EtimsItemPayload]


class EtimsResponseEnvelope(BaseModel):
    success: bool
    status: str
    invoice_number: Optional[int] = None
    cu_serial: Optional[str] = None
    cu_invoice_number: Optional[str] = None
    qr_code_url: Optional[str] = None
    error: Optional[str] = None


class EtimsStatusResponse(BaseModel):
    is_active: bool
    global_enabled: bool
    kra_pin: Optional[str] = None
    branch_id: Optional[str] = None
    device_serial: Optional[str] = None
    sdc_id: Optional[str] = None
    mrc_no: Optional[str] = None
    environment: Optional[str] = None
    last_sync_at: Optional[str] = None
    queued_transmissions: int = 0

