import pytest
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete
from app.main import app
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.identity import Shop, User
from app.models.client import Client
from app.models.supplier import Supplier
from app.models.item import Product
from app.models.inventory import StockLedger
from app.models.account import ChartOfAccount


@pytest.mark.asyncio
async def test_master_data_wave_b_lifecycle():
    """Verify Clients, Suppliers, Items with KRA classifications, and Stock Adjustments."""
    settings = get_settings()
    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-wave-b-{test_shop_id.hex[:8]}"

    async with async_session_factory() as session:
        user = User(
            id=test_user_id,
            name="Wave B Owner",
            email=f"wave-b-{test_user_id.hex[:8]}@example.com",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Wave B Test Shop",
            slug=test_slug,
            currency="KES",
        )
        session.add(shop)
        await session.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}

            # 1. Create and List Client
            client_payload = {
                "name": "Safaricom PLC",
                "email": "procurement@safaricom.co.ke",
                "phone": "+254722000000",
                "client_type": "CORPORATE",
                "tax_pin": "P051123456Z",
                "requires_etims": True,
            }
            c_res = await client.post(f"/v1/clients?shop_id={test_shop_id}", json=client_payload, headers=headers)
            assert c_res.status_code == 201
            client_data = c_res.json()
            assert client_data["name"] == "Safaricom PLC"
            assert client_data["tax_pin"] == "P051123456Z"

            list_c = await client.get(f"/v1/clients?shop_id={test_shop_id}", headers=headers)
            assert list_c.status_code == 200
            assert len(list_c.json()) == 1

            # 2. Create and List Supplier
            supp_payload = {
                "name": "Text Book Centre",
                "email": "sales@tbc.co.ke",
                "phone": "+254711000000",
                "supplier_type": "CORPORATE",
                "tax_pin": "P051987654A",
                "payment_terms": "NET_30",
            }
            s_res = await client.post(f"/v1/suppliers?shop_id={test_shop_id}", json=supp_payload, headers=headers)
            assert s_res.status_code == 201
            supp_data = s_res.json()
            assert supp_data["name"] == "Text Book Centre"

            # 3. Create Product with Auto KRA Goods Classification (24101601)
            prod_payload = {
                "name": "A4 Printing Paper (Box of 5)",
                "sku": "PAP-A4-001",
                "item_type": "PRODUCT",
                "unit_price": "3500.00",
                "cost_price": "2800.00",
                "default_tax_type": "V_16",
                "track_stock": True,
                "stock_quantity": "50.00",
                "reorder_threshold": "10.00",
            }
            p_res = await client.post(f"/v1/items?shop_id={test_shop_id}", json=prod_payload, headers=headers)
            assert p_res.status_code == 201
            prod_data = p_res.json()
            assert prod_data["item_cls_cd"] == "24101601"  # Auto-defaulted
            assert prod_data["pkg_unit_cd"] == "PCE"
            prod_id = prod_data["id"]

            # 4. Create Service with Auto KRA Service Classification (80141600)
            svc_payload = {
                "name": "IT Consultancy & Auditing",
                "sku": "SVC-IT-01",
                "item_type": "SERVICE",
                "unit_price": "25000.00",
                "cost_price": "0.00",
                "default_tax_type": "V_16",
                "track_stock": False,
            }
            svc_res = await client.post(f"/v1/items?shop_id={test_shop_id}", json=svc_payload, headers=headers)
            assert svc_res.status_code == 201
            svc_data = svc_res.json()
            assert svc_data["item_cls_cd"] == "80141600"  # Auto-defaulted for services

            # 5. Atomic Stock Adjustment
            adj_payload = {
                "product_id": prod_id,
                "quantity": "5.00",
                "movement_type": "ADJUSTMENT_IN",
                "adjustment_reason": "COUNT_CORRECTION",
                "notes": "Physical audit reconciliation",
            }
            adj_res = await client.post(f"/v1/items/inventory/adjust?shop_id={test_shop_id}", json=adj_payload, headers=headers)
            assert adj_res.status_code == 201
            adj_data = adj_res.json()
            assert Decimal(adj_data["running_balance"]) == Decimal("55.00")

            # 6. Chart of Accounts Creation and Query
            acc_payload = {
                "code": "4000",
                "name": "Sales Revenue",
                "account_type": "REVENUE",
            }
            acc_res = await client.post(f"/v1/accounts?shop_id={test_shop_id}", json=acc_payload, headers=headers)
            assert acc_res.status_code == 201
            acc_data = acc_res.json()
            assert acc_data["code"] == "4000"

    finally:
        async with async_session_factory() as session:
            await session.execute(delete(StockLedger).where(StockLedger.shop_id == test_shop_id))
            await session.execute(delete(Product).where(Product.shop_id == test_shop_id))
            await session.execute(delete(Supplier).where(Supplier.shop_id == test_shop_id))
            await session.execute(delete(Client).where(Client.shop_id == test_shop_id))
            await session.execute(delete(ChartOfAccount).where(ChartOfAccount.shop_id == test_shop_id))
            await session.execute(delete(Shop).where(Shop.id == test_shop_id))
            await session.execute(delete(User).where(User.id == test_user_id))
            await session.commit()
