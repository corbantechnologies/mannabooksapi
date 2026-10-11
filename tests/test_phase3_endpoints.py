import pytest
import uuid
from decimal import Decimal
from datetime import date, datetime
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.identity import Shop, User
from app.models.item import Product
from app.models.client import Client
from app.models.supplier import Supplier
from app.models.inventory import StockLocation

settings = get_settings()
AUTH_HEADERS = {"X-Internal-Key": settings.INTERNAL_API_KEY}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def setup_test_shop():
    async with AsyncSessionLocal() as session:
        # Create user for owner_id
        user_id = uuid.uuid4()
        user = User(
            id=user_id,
            name="Phase 3 Test User",
            email=f"phase3_{user_id.hex[:8]}@example.com",
            password_hash="testpasswordhash",
            is_super_admin=False,
            plan="FREE",
            subscription_status="ACTIVE",
        )
        session.add(user)
        await session.flush()

        test_shop_id = uuid.uuid4()
        shop = Shop(
            id=test_shop_id,
            owner_id=user.id,
            name=f"Phase 3 Integration Shop {test_shop_id.hex[:6]}",
            slug=f"phase3-shop-{test_shop_id.hex[:6]}",
            currency="KES",
            tax_pin="P051234567X",
            is_cit_active=True,
            is_tot_active=False,
            cit_rate=Decimal("30.00"),
            estimated_annual_profit=Decimal("500000.00"),
        )
        session.add(shop)
        await session.flush()

        # Add location
        loc = StockLocation(
            shop_id=test_shop_id,
            name="Main Warehouse",
            code="WH-01",
            is_default=True,
            is_active=True,
        )
        session.add(loc)

        # Add raw material product
        raw_prod = Product(
            shop_id=test_shop_id,
            name="Raw Timber Board",
            unit_price=Decimal("1500.00"),
            cost_price=Decimal("800.00"),
            track_stock=True,
            stock_quantity=Decimal("100.00"),
        )
        session.add(raw_prod)

        # Add finished product
        fin_prod = Product(
            shop_id=test_shop_id,
            name="Dining Table Model X",
            unit_price=Decimal("12000.00"),
            cost_price=Decimal("6000.00"),
            track_stock=True,
            stock_quantity=Decimal("100.00"),
        )
        session.add(fin_prod)

        # Add Client
        client = Client(
            shop_id=test_shop_id,
            name="Acme Corporate Ltd",
            email="finance@acme.example.com",
            phone="+254700112233",
        )
        session.add(client)

        # Add Supplier
        supplier = Supplier(
            shop_id=test_shop_id,
            name="Timber Mills Kenya Ltd",
            email="sales@timbermills.example.com",
            phone="+254711223344",
        )
        session.add(supplier)

        await session.commit()
        await session.refresh(shop)
        await session.refresh(loc)
        await session.refresh(raw_prod)
        await session.refresh(fin_prod)
        await session.refresh(client)
        await session.refresh(supplier)

        return {
            "user": user,
            "shop": shop,
            "loc": loc,
            "raw_prod": raw_prod,
            "fin_prod": fin_prod,
            "client": client,
            "supplier": supplier,
        }


@pytest.mark.asyncio
async def test_tax_and_fixed_assets_lifecycle(setup_test_shop):
    fixture = setup_test_shop if not hasattr(setup_test_shop, "__await__") else await setup_test_shop
    shop_id = str(fixture["shop"].id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Fixed Asset
        res = await client.post(
            "/v1/finance/fixed-assets",
            json={
                "shop_id": shop_id,
                "name": "Dell Precision Workstation",
                "assetClass": "CLASS_1",
                "purchaseDate": "2026-01-15",
                "purchaseCost": 150000.0,
                "scrapValue": 10000.0,
            },
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200, res.text
        asset_data = res.json()["asset"]
        asset_id = asset_data["id"]
        assert asset_data["assetClass"] == "CLASS_1"

        # 2. Get Fixed Assets (via /v1/tax/fixed-assets for SSR and /v1/finance/fixed-assets for client)
        res_tax = await client.get(
            f"/v1/tax/fixed-assets?shop_id={shop_id}",
            headers=AUTH_HEADERS,
        )
        assert res_tax.status_code == 200
        assert len(res_tax.json()["assets"]) >= 1

        res_fin = await client.get(
            f"/v1/finance/fixed-assets?shop_id={shop_id}",
            headers=AUTH_HEADERS,
        )
        assert res_fin.status_code == 200

        # 3. Run Capital Allowances
        res_ca = await client.post(
            "/v1/finance/fixed-assets/capital-allowances",
            json={"shop_id": shop_id, "year": 2026},
            headers=AUTH_HEADERS,
        )
        assert res_ca.status_code == 200
        assert res_ca.json()["success"] is True
        assert res_ca.json()["amount"] > 0

        # 4. Tax Computation (Corporate Income Tax)
        res_comp = await client.get(
            f"/v1/finance/tax/computation?shop_id={shop_id}&year=2026",
            headers=AUTH_HEADERS,
        )
        assert res_comp.status_code == 200
        assert "taxRate" in res_comp.json()
        assert res_comp.json()["taxRate"] == 30.0

        # Also verify via /v1/tax/computation SSR
        res_comp_ssr = await client.get(
            f"/v1/tax/computation?shop_id={shop_id}&year=2026",
            headers=AUTH_HEADERS,
        )
        assert res_comp_ssr.status_code == 200

        # 5. Turnover Tax (TOT)
        res_tot = await client.get(
            f"/v1/tax/turnover-tax?shop_id={shop_id}&year=2026",
            headers=AUTH_HEADERS,
        )
        assert res_tot.status_code == 200
        assert len(res_tot.json()["quarters"]) == 4

        # 6. Tax Instalments Generate & Pay
        res_inst_gen = await client.post(
            "/v1/finance/tax/instalments/generate",
            json={"shop_id": shop_id, "year": 2026},
            headers=AUTH_HEADERS,
        )
        assert res_inst_gen.status_code == 200
        instalments = res_inst_gen.json()["instalments"]
        assert len(instalments) == 4
        inst_id = instalments[0]["id"]

        res_pay = await client.post(
            f"/v1/finance/tax/instalments/{inst_id}/pay",
            json={
                "shop_id": shop_id,
                "paidAmount": 10000.0,
                "paymentReference": "KRA-PAY-REF-9988",
            },
            headers=AUTH_HEADERS,
        )
        assert res_pay.status_code == 200
        assert res_pay.json()["instalment"]["status"] == "PAID"

        # 7. Asset Disposal
        res_disp = await client.post(
            f"/v1/finance/fixed-assets/{asset_id}/dispose",
            json={
                "shop_id": shop_id,
                "disposalDate": "2026-06-30",
                "disposalProceeds": 45000.0,
            },
            headers=AUTH_HEADERS,
        )
        assert res_disp.status_code == 200
        assert res_disp.json()["asset"]["isDisposed"] is True


@pytest.mark.asyncio
async def test_inventory_and_operations_flow(setup_test_shop):
    fixture = setup_test_shop if not hasattr(setup_test_shop, "__await__") else await setup_test_shop
    shop_id = str(fixture["shop"].id)
    raw_id = str(fixture["raw_prod"].id)
    fin_id = str(fixture["fin_prod"].id)
    loc_id = str(fixture["loc"].id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create new location
        res_loc = await client.post(
            "/v1/items/locations",
            json={
                "shop_id": shop_id,
                "name": "Branch Warehouse Mombasa",
                "code": "MSA-02",
                "is_default": False,
            },
            headers=AUTH_HEADERS,
        )
        assert res_loc.status_code == 200
        new_loc_id = res_loc.json()["location"]["id"]

        # 2. Stock Transfer
        res_xfer = await client.post(
            "/v1/items/transfers",
            json={
                "shop_id": shop_id,
                "source_location_id": loc_id,
                "destination_location_id": new_loc_id,
                "items": [{"productId": raw_id, "quantity": 5}],
            },
            headers=AUTH_HEADERS,
        )
        assert res_xfer.status_code == 200
        assert res_xfer.json()["success"] is True

        # 3. Create BOM & Run Production Order
        res_bom = await client.post(
            "/v1/operations/boms",
            json={
                "shop_id": shop_id,
                "finished_product_id": fin_id,
                "name": "Dining Table Batch Recipe",
                "labor_cost_estimate": 1200.0,
                "overhead_cost_estimate": 400.0,
                "items": [
                    {
                        "raw_material_product_id": raw_id,
                        "quantity_required": 2.0,
                        "wastage_percentage": 5.0,
                    }
                ],
            },
            headers=AUTH_HEADERS,
        )
        assert res_bom.status_code == 200
        bom_id = res_bom.json()["bomId"]

        # Run BOM
        res_run = await client.post(
            "/v1/operations/boms/run",
            json={
                "shop_id": shop_id,
                "bom_id": bom_id,
                "units_to_produce": 2.0,
                "target_location_id": loc_id,
                "notes": "Urgent production batch",
            },
            headers=AUTH_HEADERS,
        )
        assert res_run.status_code == 200
        assert res_run.json()["success"] is True
        assert res_run.json()["unitProductionCost"] > 0

        # 4. Stocktake flow
        res_stk = await client.post(
            "/v1/operations/stocktakes",
            json={"shop_id": shop_id, "location_id": loc_id},
            headers=AUTH_HEADERS,
        )
        assert res_stk.status_code == 200
        stk_id = res_stk.json()["id"]

        # Update Stocktake item count
        res_count = await client.patch(
            f"/v1/operations/stocktakes/{stk_id}/items/{fin_id}",
            json={"countedQuantity": 15.0, "notes": "Audited by inventory team"},
            headers=AUTH_HEADERS,
        )
        assert res_count.status_code == 200

        # Reconcile Stocktake
        res_rec = await client.post(
            f"/v1/operations/stocktakes/{stk_id}/reconcile",
            headers=AUTH_HEADERS,
        )
        assert res_rec.status_code == 200
        assert res_rec.json()["stocktake"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_reports_payroll_and_diagnostics(setup_test_shop):
    fixture = setup_test_shop if not hasattr(setup_test_shop, "__await__") else await setup_test_shop
    shop_id = str(fixture["shop"].id)
    client_id = str(fixture["client"].id)
    supplier_id = str(fixture["supplier"].id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. P&L statement alias /pl
        res_pl = await client.get(
            f"/v1/reports/pl?shop_id={shop_id}&period=THIS_MONTH",
            headers=AUTH_HEADERS,
        )
        assert res_pl.status_code == 200
        assert "total_revenue" in res_pl.json() or "sales_revenue" in res_pl.json()

        # 2. Cash Flow statement
        res_cf = await client.get(
            f"/v1/reports/cashflow?shop_id={shop_id}&period=THIS_MONTH",
            headers=AUTH_HEADERS,
        )
        assert res_cf.status_code == 200
        assert "operating" in res_cf.json()
        assert "summary" in res_cf.json()

        # 3. Client & Supplier Statement Emailers
        res_em_c = await client.post(
            f"/v1/reports/client-statement/{client_id}/email",
            json={"email": "client@example.com"},
            headers=AUTH_HEADERS,
        )
        assert res_em_c.status_code == 200
        assert res_em_c.json()["success"] is True

        res_em_s = await client.post(
            f"/v1/reports/supplier-statement/{supplier_id}/email",
            json={"email": "supplier@example.com"},
            headers=AUTH_HEADERS,
        )
        assert res_em_s.status_code == 200
        assert res_em_s.json()["success"] is True

        # 4. Payroll Generate & Finalize
        res_pay = await client.post(
            "/v1/payroll/generate",
            json={"shop_id": shop_id, "payroll_period_code": "2026-03"},
            headers=AUTH_HEADERS,
        )
        assert res_pay.status_code == 200
        voucher_id = res_pay.json()["voucherId"]

        res_fin_pay = await client.post(
            f"/v1/payroll/{voucher_id}/finalize",
            headers=AUTH_HEADERS,
        )
        assert res_fin_pay.status_code == 200
        assert res_fin_pay.json()["status"] == "PAID"

        # 5. Workspace Diagnostics (Ledger repair and export)
        res_rep = await client.post(
            f"/v1/workspaces/{shop_id}/ledger/repair",
            headers=AUTH_HEADERS,
        )
        assert res_rep.status_code == 200
        assert res_rep.json()["status"] == "HEALTHY"

        res_exp = await client.get(
            f"/v1/workspaces/{shop_id}/export",
            headers=AUTH_HEADERS,
        )
        assert res_exp.status_code == 200
        assert res_exp.json()["workspace"]["slug"] == fixture["shop"].slug
