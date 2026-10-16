import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from app.core.schemas import ApiModel


class OverdueSweepResponse(ApiModel):
    total_evaluated: int
    marked_overdue: int
    aging_buckets: Dict[str, int]  # "1-30_days", "31-60_days", "61-90_days", "90+_days"
    total_overdue_amount: Decimal
    errors: List[str] = []


class RecurringScheduleProcessedItem(ApiModel):
    original_document_id: uuid.UUID
    new_document_id: uuid.UUID
    new_doc_number: str
    status: str
    next_recurring_date: Optional[datetime] = None


class ProcessRecurringResponse(ApiModel):
    total_evaluated: int
    created_invoices: int
    advanced_schedules: int
    items: List[RecurringScheduleProcessedItem] = []
    errors: List[str] = []


class QuoteExpiryResponse(ApiModel):
    total_evaluated: int
    expired_quotes: int
    errors: List[str] = []


class EtimsRetryCronResponse(ApiModel):
    batches_processed: int
    total_transmissions_evaluated: int
    succeeded: int
    failed: int
    errors: List[str] = []
