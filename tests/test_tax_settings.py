import pytest
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.identity import Shop, User


@pytest.mark.asyncio
async def test_tax_settings_and_fiscal_years_lifecycle():
    settings = get_settings()
    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-tax-{test_shop_id.hex[:8]}"

    async with async_session_factory() as session:
        user = User(
            id=test_user_id,
            name="Tax Owner",
            email=f"tax-owner-{test_user_id.hex[:8]}@example.com",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Tax Test Shop",
            slug=test_slug,
            currency="KES",
            tax_regime="CIT",
            is_cit_active=True,
            is_tot_active=False,
            cit_rate=Decimal("30.00"),
            estimated_annual_profit=Decimal("150000.00"),
        )
        session.add(shop)
        await session.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}

            # 1. GET Tax Settings
            get_res = await client.get(f"/v1/finance/tax/settings?shop_id={test_shop_id}", headers=headers)
            assert get_res.status_code == 200
            data = get_res.json()
            assert data["success"] is True
            assert data["isCitActive"] is True
            assert data["isTotActive"] is False
            assert float(data["citRate"]) == 30.00

            # 2. POST Tax Settings update (as sent by TaxSettingsClient.tsx)
            update_payload = {
                "shop_id": str(test_shop_id),
                "isCitActive": True,
                "isTotActive": True,
                "citRate": 25.0,
                "estimatedAnnualProfit": 500000.0,
                "taxPin": "P051987654X",
            }
            post_res = await client.post("/v1/finance/tax/settings", json=update_payload, headers=headers)
            assert post_res.status_code == 200
            updated = post_res.json()
            assert updated["success"] is True
            assert updated["isTotActive"] is True
            assert float(updated["citRate"]) == 25.0
            assert float(updated["estimatedAnnualProfit"]) == 500000.0
            assert updated["taxPin"] == "P051987654X"

            # 3. Fiscal Year Declaration
            fy_payload = {
                "shop_id": str(test_shop_id),
                "label": "Fiscal Year 2026/2027",
                "startDate": "2026-01-01",
                "endDate": "2026-12-31",
            }
            fy_res = await client.post("/v1/accounts/fiscal-years", json=fy_payload, headers=headers)
            assert fy_res.status_code == 201
            fy_data = fy_res.json()
            assert fy_data["success"] is True
            fy_id = fy_data["fiscalYear"]["id"]

            # 4. Close Fiscal Year
            close_res = await client.post(
                f"/v1/accounts/fiscal-years/{fy_id}/close",
                json={"shop_id": str(test_shop_id)},
                headers=headers,
            )
            assert close_res.status_code == 200
            assert close_res.json()["success"] is True

    finally:
        # Cleanup
        async with async_session_factory() as session:
            await session.delete(shop)
            await session.delete(user)
            await session.commit()
