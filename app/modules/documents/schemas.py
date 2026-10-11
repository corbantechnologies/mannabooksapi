import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Literal, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator


class DocumentItemInput(BaseModel):
    product_id: Optional[uuid.UUID] = None
    description: str = Field(..., min_length=1)
    notes: Optional[str] = None
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    tax_type: Literal["V_16", "V_0", "EXEMPT"] = "V_16"


class DocumentCreateRequest(BaseModel):
    client_id: Optional[uuid.UUID] = None
    supplier_id: Optional[uuid.UUID] = None
    type: str = "INVOICE"
    doc_number: Optional[str] = None
    status: str = "DRAFT"
    notes: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    currency: Optional[str] = "KES"
    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    location_id: Optional[uuid.UUID] = None
    payment_channel: Optional[str] = None
    payment_reference: Optional[str] = None
    requires_etims: bool = False
    items: List[DocumentItemInput] = Field(..., min_length=1)

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: Optional[str]) -> str:
        if not v:
            return "INVOICE"
        val = str(v).strip().upper()
        mapping = {
            "QUOTE": "QUOTATION",
            "QUOTATION": "QUOTATION",
            "ESTIMATE": "QUOTATION",
            "PROFORMA": "INVOICE",
            "PROFORMA_INVOICE": "INVOICE",
            "INV": "INVOICE",
            "INVOICE": "INVOICE",
            "RECEIPT": "RECEIPT",
            "REC": "RECEIPT",
            "LPO": "LPO",
            "PO": "PO",
            "PURCHASE_ORDER": "PO",
            "LSO": "LSO",
            "DELIVERY_NOTE": "DELIVERY_NOTE",
            "DN": "DELIVERY_NOTE",
            "CREDIT_NOTE": "CREDIT_NOTE",
            "CN": "CREDIT_NOTE",
            "DEBIT_NOTE": "DEBIT_NOTE",
            "GOODS_RECEIVED_NOTE": "GOODS_RECEIVED_NOTE",
            "GRN": "GOODS_RECEIVED_NOTE",
            "SERVICE_COMPLETION_NOTE": "SERVICE_COMPLETION_NOTE",
            "PAYMENT_VOUCHER": "PAYMENT_VOUCHER",
            "PV": "PAYMENT_VOUCHER",
            "PAYROLL_VOUCHER": "PAYROLL_VOUCHER",
        }
        return mapping.get(val, val)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: Optional[str]) -> str:
        if not v:
            return "DRAFT"
        val = str(v).strip().upper()
        mapping = {
            "SENT": "ISSUED",
            "OPEN": "ISSUED",
            "PENDING": "ISSUED",
            "APPROVED": "ISSUED",
        }
        return mapping.get(val, val)

    @field_validator("issue_date", "due_date", mode="before")
    @classmethod
    def normalize_dates(cls, v: Any) -> Any:
        from app.core.datetime_utils import to_naive_utc
        return to_naive_utc(v)


class CreditNoteItemInput(BaseModel):
    item_id: Optional[uuid.UUID] = None
    product_id: Optional[uuid.UUID] = None
    description: str
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    tax_type: Literal["V_16", "V_0", "EXEMPT"] = "V_16"
    condition: Literal["RESALABLE", "DAMAGED", "BILLING_CORRECTION"] = "RESALABLE"


class CreditNoteCreateRequest(BaseModel):
    reason_code: Literal["01", "02", "03"] = "01"  # 01 Goods Return, 02 Invoicing Error, 03 Price Adjustment
    reason_notes: str = Field(..., min_length=3)
    restock_inventory: bool = True  # Context-aware default
    items: Optional[List[CreditNoteItemInput]] = None  # None -> credits full parent invoice


class DocumentStatusUpdateRequest(BaseModel):
    status: str

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: Optional[str]) -> str:
        if not v:
            return "DRAFT"
        val = str(v).strip().upper()
        mapping = {
            "SENT": "ISSUED",
            "OPEN": "ISSUED",
            "PENDING": "ISSUED",
            "APPROVED": "ISSUED",
        }
        return mapping.get(val, val)


class DocumentPatchRequest(BaseModel):
    status: Optional[str] = None
    kra_cu_invoice_number: Optional[str] = None
    payment_channel: Optional[str] = None
    payment_reference: Optional[str] = None
    attachment_url: Optional[str] = None
    attachment_name: Optional[str] = None
    attachment_size: Optional[int] = None
    notes: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    due_date: Optional[datetime] = None
    is_recurring: Optional[bool] = None
    recurring_interval: Optional[str] = None
    next_recurring_date: Optional[datetime] = None


class DocumentNoteCreateRequest(BaseModel):
    note: str = Field(..., min_length=1)


class DocumentConvertRequest(BaseModel):
    target_type: str


class DocumentBulkStatusRequest(BaseModel):
    document_ids: List[uuid.UUID]
    new_status: str


class DocumentItemResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    product_id: Optional[uuid.UUID] = None
    description: str
    notes: Optional[str] = None
    quantity: Decimal
    unit_price: Decimal
    tax_type: str
    tax_amount: Decimal
    item_total: Decimal

    model_config = ConfigDict(from_attributes=True)


class PartySummary(BaseModel):
    id: uuid.UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    tax_pin: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    client_id: Optional[uuid.UUID] = None
    supplier_id: Optional[uuid.UUID] = None
    client: Optional[PartySummary] = None
    supplier: Optional[PartySummary] = None
    type: str
    doc_number: str
    status: str
    kra_cu_invoice_number: Optional[str] = None
    parent_document_id: Optional[uuid.UUID] = None
    requires_etims: bool
    notes: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    payment_channel: Optional[str] = None
    payment_reference: Optional[str] = None
    attachment_url: Optional[str] = None
    attachment_name: Optional[str] = None
    attachment_size: Optional[int] = None
    email_delivery_status: Optional[str] = "NOT_SENT"
    last_email_opened_at: Optional[datetime] = None
    is_recurring: Optional[bool] = False
    recurring_interval: Optional[str] = None
    next_recurring_date: Optional[datetime] = None
    currency: Optional[str] = "KES"
    sub_total: Decimal
    tax_amount: Decimal
    grand_total: Decimal
    issue_date: datetime
    due_date: Optional[datetime] = None
    location_id: Optional[uuid.UUID] = None
    created_at: datetime
    items: List[DocumentItemResponse] = []

    model_config = ConfigDict(from_attributes=True)

