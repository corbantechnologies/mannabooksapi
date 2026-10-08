import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class OverdueSweepResponse(BaseModel):
    total_evaluated: int
    marked_overdue: int
    aging_buckets: Dict[str, int]  # "1-30_days", "31-60_days", "61-90_days", "90+_days"
    total_overdue_amount: Decimal
    errors: List[str] = []


class RecurringScheduleProcessedItem(BaseModel):
    original_document_id: uuid.UUID
    new_document_id: uuid.UUID
    new_doc_number: str
    status: str
    next_recurring_date: Optional[datetime] = None


class ProcessRecurringResponse(BaseModel):
    total_evaluated: int
    created_invoices: int
    advanced_schedules: int
    items: List[RecurringScheduleProcessedItem] = []
    errors: List[str] = []


class QuoteExpiryResponse(BaseModel):
    total_evaluated: int
    expired_quotes: int
    errors: List[str] = []


class EtimsRetryCronResponse(BaseModel):
    batches_processed: int
    total_transmissions_evaluated: int
    succeeded: int
    failed: int
    errors: List[str] = []
