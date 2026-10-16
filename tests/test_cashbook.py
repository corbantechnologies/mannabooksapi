"""Expenses/other-income validation (no database needed)."""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.main import app
from app.modules.cashbook.router import EntryCreate, EntryUpdate, _values
from app.modules.cashbook.service import EXPENSE, INCOME

SHOP = SimpleNamespace(id=uuid.uuid4(), currency="KES")


def _create(**kw):
    base = {"shop_id": str(SHOP.id), "description": "Office rent", "amount": "25000"}
    base.update(kw)
    return EntryCreate.model_validate(base)


def test_valid_expense_defaults():
    v = _values(EXPENSE, _create(), SHOP, partial=False)
    assert v["amount"] == Decimal("25000.00") and v["category"] == "OTHER" and v["currency"] == "KES"
    assert v["is_non_deductible"] is False and "expense_date" in v


@pytest.mark.parametrize("amount", ["0", "-5"])
def test_rejects_non_positive_amount(amount):
    with pytest.raises(ValidationError):
        _create(amount=amount)


def test_rejects_unsafe_attachment_url():
    with pytest.raises(ValidationError):
        _create(receiptUrl="javascript:alert(1)")


def test_rejects_unknown_category_and_foreign_currency():
    with pytest.raises(HTTPException):
        _values(EXPENSE, _create(category="CAR_WASH"), SHOP, partial=False)
    with pytest.raises(HTTPException):
        _values(INCOME, _create(category="INTEREST", currency="USD"), SHOP, partial=False)


def test_partial_update_only_touches_sent_fields():
    v = _values(EXPENSE, EntryUpdate.model_validate({"amount": "10.5"}), SHOP, partial=True)
    assert v == {"amount": Decimal("10.50")}


def test_camel_case_payload_from_ui():
    req = _create(paymentChannel="mpesa", paymentReference=" QK12 ", incomeDate="2026-10-01", category="interest")
    v = _values(INCOME, req, SHOP, partial=False)
    assert v["payment_channel"] == "MPESA" and v["payment_reference"] == "QK12"
    assert v["category"] == "INTEREST" and v["income_date"].year == 2026


async def test_cashbook_routes_require_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/v1/expenses", params={"shop_id": str(SHOP.id)})).status_code == 401
        assert (await c.patch(f"/v1/incomes/{uuid.uuid4()}", json={"amount": "1"})).status_code == 401
