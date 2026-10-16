"""
Regression tests for the authentication / tenancy fixes. None of these need a database:
every request below must be rejected before any query runs.
"""
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app
from app.modules.payroll.rates import SEED_RATE_SETS, compute_pay

settings = get_settings()
SHOP = str(uuid.uuid4())
INTERNAL = {"X-Internal-Key": settings.INTERNAL_API_KEY}


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_tenant_route_requires_auth(client):
    res = await client.get("/v1/clients", params={"shop_id": SHOP})
    assert res.status_code == 401


async def test_forged_nextauth_cookie_is_not_a_session(client):
    res = await client.get(
        "/v1/clients", params={"shop_id": SHOP}, cookies={"next-auth.session-token": "anything"}
    )
    assert res.status_code == 401


async def test_invalid_token_is_rejected_even_with_internal_key(client):
    res = await client.get(
        "/v1/clients",
        params={"shop_id": SHOP},
        headers={**INTERNAL, "Authorization": "Bearer not-a-real-token"},
    )
    assert res.status_code == 401


async def test_internal_key_alone_cannot_read_tenant_data(client):
    res = await client.get("/v1/clients", params={"shop_id": SHOP}, headers=INTERNAL)
    assert res.status_code == 401


async def test_admin_bootstrap_endpoint_is_gone(client):
    res = await client.post("/v1/admin/bootstrap", json={"email": "x@example.com"}, headers=INTERNAL)
    assert res.status_code in (401, 404, 405)


async def test_admin_routes_need_a_super_admin_user(client):
    res = await client.get("/v1/admin/users", headers=INTERNAL)
    assert res.status_code == 401


async def test_forgot_password_is_internal_only(client):
    res = await client.post("/v1/auth/forgot-password", json={"email": "victim@example.com"})
    assert res.status_code == 403


async def test_billing_status_override_is_internal_only(client):
    res = await client.post(
        f"/v1/billing/update-status/{uuid.uuid4()}", json={"status": "COMPLETED"}
    )
    assert res.status_code == 403


async def test_mpesa_callbacks_are_internal_only(client):
    assert (await client.post("/v1/billing/mpesa-callback", json={})).status_code == 403
    assert (await client.post("/v1/payments/mpesa/c2b-callback", json={})).status_code == 403


async def test_payroll_finalize_requires_auth(client):
    res = await client.post(f"/v1/payroll/{uuid.uuid4()}/finalize")
    assert res.status_code == 401


async def test_cookie_auth_mutation_requires_csrf_header(client):
    # Cookie-authenticated writes without X-Requested-With are refused before the token is even used.
    res = await client.post(
        "/v1/clients", params={"shop_id": SHOP}, json={}, cookies={"access_token": "garbage"}
    )
    assert res.status_code in (401, 403)


def test_payroll_math_uses_decimal():
    calc = compute_pay(SEED_RATE_SETS[-1]["rates"], Decimal("50000"))
    assert all(isinstance(v, Decimal) for v in calc.values())
    assert calc["net_pay"] == calc["gross_pay"] - calc["total_deductions"]

async def test_b2b_document_actions_require_auth(client):
    res = await client.patch(f"/v1/b2b/inbox/{uuid.uuid4()}/read", headers=INTERNAL)
    assert res.status_code == 401


async def test_catalog_quote_is_internal_only(client):
    res = await client.post("/v1/portal/catalog/any-shop/quote", json={"customerName": "Bot", "items": []})
    assert res.status_code == 403


async def test_statutory_rate_admin_requires_super_admin(client):
    assert (await client.get("/v1/admin/statutory-rates", headers=INTERNAL)).status_code == 401


async def test_payroll_returns_require_auth(client):
    res = await client.get("/v1/payroll/p10", params={"shop_id": SHOP, "period": "2026-10"})
    assert res.status_code == 401
