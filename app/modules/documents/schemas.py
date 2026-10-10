import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, ConfigDict


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


class DocumentResponse(BaseModel):
    id: uuid.UUID
    shop_id: uuid.UUID
    client_id: Optional[uuid.UUID] = None
    supplier_id: Optional[uuid.UUID] = None
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
