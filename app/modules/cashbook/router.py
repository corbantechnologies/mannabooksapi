# app/modules/cashbook/router.py
"""Expenses and other income. Mounted at /expenses, /incomes and the legacy /operations/* aliases."""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AliasChoices, BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_acting_user_id
from app.core.datetime_utils import to_naive_utc
from app.db.session import get_db
from app.models.identity import Shop
from app.modules.cashbook.service import (
    EXPENSE,
    INCOME,
    PAYMENT_CHANNELS,
    Kind,
    create_entry,
    delete_entry,
    list_entries,
    now,
    serialize,
    update_entry,
)
from app.core.schemas import ApiModel

router = APIRouter(tags=["Expenses & Other Income"])

MAX_AMOUNT = Decimal("1000000000000")


def _alias(*names: str):
    return Field(default=None, validation_alias=AliasChoices(*names))


class EntryBase(ApiModel):
    description: Optional[str] = Field(None, max_length=500)
    amount: Optional[Decimal] = None
    category: Optional[str] = None
    date: Optional[datetime] = _alias("date", "expense_date", "income_date", "expenseDate", "incomeDate")
    currency: Optional[str] = Field(None, max_length=3)
    payment_channel: Optional[str] = _alias("payment_channel", "paymentChannel")
    payment_reference: Optional[str] = Field(
        None, max_length=100, validation_alias=AliasChoices("payment_reference", "paymentReference")
    )
    attachment_url: Optional[str] = _alias("attachment_url", "receipt_url", "attachmentUrl", "receiptUrl")
    is_non_deductible: Optional[bool] = _alias("is_non_deductible", "isNonDeductible")

    @field_validator("description")
    @classmethod
    def _desc(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Description must be at least 2 characters.")
        return v

    @field_validator("amount")
    @classmethod
    def _amount(cls, v):
        if v is None:
            return v
        if v <= 0:
            raise ValueError("Amount must be greater than zero.")
        if v >= MAX_AMOUNT:
            raise ValueError("Amount is too large.")
        return v.quantize(Decimal("0.01"))

    @field_validator("date")
    @classmethod
    def _date(cls, v):
        v = to_naive_utc(v)
        if v is None:
            return v
        if v.year < 2000 or v > now() + timedelta(days=366):
            raise ValueError("Date is out of range.")
        return v

    @field_validator("payment_channel")
    @classmethod
    def _channel(cls, v):
        if v in (None, ""):
            return None
        v = v.strip().upper()
        v = {"BANK_TRANSFER": "BANK", "CREDIT_CARD": "CARD", "DEBIT_CARD": "CARD", "M-PESA": "MPESA"}.get(v, v)
        if v not in PAYMENT_CHANNELS:
            raise ValueError(f"Payment channel must be one of {', '.join(PAYMENT_CHANNELS)}.")
        return v

    @field_validator("attachment_url")
    @classmethod
    def _url(cls, v):
        if v in (None, ""):
            return None
        v = v.strip()
        # Only links to our storage over https (blocks javascript:, data: and the like).
        if not v.lower().startswith("https://") or len(v) > 2000:
            raise ValueError("Attachment must be an https link.")
        return v


class EntryCreate(EntryBase):
    shop_id: uuid.UUID = Field(..., validation_alias=AliasChoices("shop_id", "shopId"))
    description: str = Field(..., max_length=500)
    amount: Decimal


class EntryUpdate(EntryBase):
    pass


def _values(kind: Kind, req: EntryBase, shop: Shop, partial: bool) -> dict:
    data = req.model_dump(exclude_unset=partial)
    data.pop("shop_id", None)
    values: dict = {}
    for key in ("description", "amount", "payment_channel", "payment_reference"):
        if key in data:
            values[key] = data[key].strip() if key == "payment_reference" and data[key] else data[key]

    if "category" in data or not partial:
        category = (data.get("category") or "OTHER").strip().upper()
        if category not in kind.categories:
            raise HTTPException(status_code=422, detail=f"Category must be one of {', '.join(kind.categories)}.")
        values["category"] = category

    if "currency" in data or not partial:
        currency = (data.get("currency") or shop.currency or "KES").upper()
        if currency != (shop.currency or "KES").upper():
            raise HTTPException(
                status_code=422,
                detail=f"Entries must be recorded in the workspace currency ({shop.currency or 'KES'}).",
            )
        values["currency"] = currency

    if "date" in data or not partial:
        values[kind.date_field] = data.get("date") or now()
    if "attachment_url" in data:
        values[kind.attachment_field] = data["attachment_url"]
    if kind is EXPENSE and ("is_non_deductible" in data or not partial):
        values["is_non_deductible"] = bool(data.get("is_non_deductible") or False)
    return values


async def _load(db: AsyncSession, kind: Kind, entry_id: uuid.UUID):
    row = await db.get(kind.model, entry_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"{kind.label} not found")
    return row


def _register(kind: Kind, path: str) -> None:
    @router.get(path, name=f"list_{kind.name}s_{path}")
    async def list_endpoint(
        shop_id: uuid.UUID = Query(...),
        date_from: Optional[datetime] = Query(None),
        date_to: Optional[datetime] = Query(None),
        category: Optional[str] = Query(None),
        q: Optional[str] = Query(None, max_length=100),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        db: AsyncSession = Depends(get_db),
    ):
        return await list_entries(
            db, kind, shop_id, to_naive_utc(date_from), to_naive_utc(date_to),
            category.upper() if category else None, q, limit, offset,
        )

    @router.post(path, name=f"create_{kind.name}_{path}")
    async def create_endpoint(req: EntryCreate, request: Request, db: AsyncSession = Depends(get_db)):
        shop = await db.get(Shop, req.shop_id)
        if not shop:
            raise HTTPException(status_code=404, detail="Workspace not found")
        row = await create_entry(db, kind, shop.id, _values(kind, req, shop, partial=False), get_acting_user_id(request))
        return {"success": True, kind.name: serialize(kind, row)}

    @router.patch(path + "/{entry_id}", name=f"update_{kind.name}_{path}")
    async def update_endpoint(entry_id: uuid.UUID, req: EntryUpdate, request: Request, db: AsyncSession = Depends(get_db)):
        row = await _load(db, kind, entry_id)
        shop = await db.get(Shop, row.shop_id)
        row = await update_entry(db, kind, row, _values(kind, req, shop, partial=True), get_acting_user_id(request))
        return {"success": True, kind.name: serialize(kind, row)}

    @router.delete(path + "/{entry_id}", name=f"delete_{kind.name}_{path}")
    async def delete_endpoint(entry_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
        row = await _load(db, kind, entry_id)
        await delete_entry(db, kind, row, get_acting_user_id(request))
        return {"success": True}


for _kind, _paths in ((EXPENSE, ("/expenses", "/operations/expenses")), (INCOME, ("/incomes", "/operations/incomes"))):
    for _path in _paths:
        _register(_kind, _path)
