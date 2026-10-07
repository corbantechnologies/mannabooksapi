import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete
from app.main import app
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.identity import Shop, User
from app.models.etims import EtimsConfig, EtimsSequence, EtimsTransmission


@pytest.mark.asyncio
async def test_global_kill_switch_skips_transmission():
    """Verify that when ENABLE_KRA_ETIMS_GLOBAL is False, transmission safely skips."""
    settings = get_settings()
    original_switch = settings.ENABLE_KRA_ETIMS_GLOBAL
    settings.ENABLE_KRA_ETIMS_GLOBAL = False

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}
            payload = {
                "shop_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
                "customer_name": "Test Customer",
                "sales_type_code": "N",
                "receipt_type_code": "S",
                "payment_type_code": "01",
                "items": [
                    {
                        "itemSeq": 1,
                        "itemCd": "ITM001",
                        "itemClsCd": "24101601",
                        "itemNm": "Office Supplies",
                        "pkgUnitCd": "PCE",
                        "qty": 2.0,
                        "prc": 1000.0,
                        "splyAmt": 2000.0,
                        "dcRt": 0.0,
                        "dcAmt": 0.0,
                        "taxTyCd": "A",
                        "taxblAmt": 1724.14,
                        "taxAmt": 275.86,
                        "totAmt": 2000.0,
                    }
                ],
            }
            resp = await client.post("/v1/etims/transmit", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert data["status"] == "SKIPPED"
            assert "globally disabled" in data["error"]
    finally:
        settings.ENABLE_KRA_ETIMS_GLOBAL = original_switch


@pytest.mark.asyncio
async def test_status_endpoint_returns_correct_flag():
    """Verify GET /v1/etims/status/{shop_id} returns global and shop status."""
    settings = get_settings()
    shop_id = str(uuid.uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}
        resp = await client.get(f"/v1/etims/status/{shop_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is False
        assert "global_enabled" in data
        assert data["queued_transmissions"] == 0


@pytest.mark.asyncio
async def test_internal_api_key_guard():
    """Verify endpoint rejects requests with invalid or missing internal API key."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/v1/etims/status/{uuid.uuid4()}", headers={"X-Internal-Key": "wrong-key"})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_full_etims_onboarding_and_transmission_mock():
    """End-to-end test: initialize device, allocate monotonic sequences, transmit fiscal receipts."""
    settings = get_settings()
    orig_global = settings.ENABLE_KRA_ETIMS_GLOBAL
    orig_mock = settings.KRA_ETIMS_MOCK_MODE
    settings.KRA_ETIMS_MOCK_MODE = True
    settings.ENABLE_KRA_ETIMS_GLOBAL = True

    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-shop-{test_shop_id.hex[:8]}"

    async with async_session_factory() as session:
        # Create test owner user
        user = User(
            id=test_user_id,
            name="Test Owner",
            email=f"owner-{test_user_id.hex[:8]}@example.com",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        # Create test shop in database
        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Test eTIMS Enterprise",
            slug=test_slug,
            currency="KES",
        )
        session.add(shop)
        await session.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}

            # 1. Initialize Device Handshake
            init_payload = {
                "shop_id": str(test_shop_id),
                "kra_pin": "P051234567Z",
                "branch_id": "00",
                "device_serial": "SIMULATED-DVC-001",
                "communication_key": "TESTCOMMKEY1234567890ABCDEF1234",
                "environment": "SANDBOX",
            }
            init_resp = await client.post("/v1/etims/initialize", json=init_payload, headers=headers)
            assert init_resp.status_code == 200
            init_data = init_resp.json()
            assert init_data["success"] is True
            assert "sdcId" in init_data

            # 2. Check Status endpoint shows active
            status_resp = await client.get(f"/v1/etims/status/{test_shop_id}", headers=headers)
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            assert status_data["is_active"] is True
            assert status_data["kra_pin"] == "P051234567Z"

            # 3. Transmit First Fiscal Sales Invoice
            doc_id_1 = str(uuid.uuid4())
            tx_payload_1 = {
                "shop_id": str(test_shop_id),
                "document_id": doc_id_1,
                "customer_name": "Acme Kenya Ltd",
                "sales_type_code": "N",
                "receipt_type_code": "S",
                "payment_type_code": "01",
                "items": [
                    {
                        "itemSeq": 1,
                        "itemCd": "SVC01",
                        "itemClsCd": "24101601",
                        "itemNm": "Accounting Consultation",
                        "pkgUnitCd": "PCE",
                        "qty": 1.0,
                        "prc": 5000.0,
                        "splyAmt": 5000.0,
                        "dcRt": 0.0,
                        "dcAmt": 0.0,
                        "taxTyCd": "A",
                        "taxblAmt": 4310.34,
                        "taxAmt": 689.66,
                        "totAmt": 5000.0,
                    }
                ],
            }
            tx_resp_1 = await client.post("/v1/etims/transmit", json=tx_payload_1, headers=headers)
            assert tx_resp_1.status_code == 200
            tx_data_1 = tx_resp_1.json()
            assert tx_data_1["success"] is True
            assert tx_data_1["status"] == "FISCALIZED"
            assert tx_data_1["invoice_number"] == 1
            assert tx_data_1["cu_serial"] is not None
            assert tx_data_1["cu_invoice_number"] is not None
            assert "qrCode" in tx_data_1["qr_code_url"]

            # 4. Transmit Second Fiscal Invoice - Verify Monotonic Sequence Increment
            doc_id_2 = str(uuid.uuid4())
            tx_payload_2 = dict(tx_payload_1, document_id=doc_id_2)
            tx_resp_2 = await client.post("/v1/etims/transmit", json=tx_payload_2, headers=headers)
            assert tx_resp_2.status_code == 200
            tx_data_2 = tx_resp_2.json()
            assert tx_data_2["success"] is True
            assert tx_data_2["invoice_number"] == 2

            # 5. Verify Background Worker process-queue endpoint
            worker_resp = await client.post("/v1/etims/worker/process-queue", headers=headers)
            assert worker_resp.status_code == 200
            worker_data = worker_resp.json()
            assert "processed" in worker_data

    finally:
        # Restore configuration switches and clean up test data
        settings.ENABLE_KRA_ETIMS_GLOBAL = orig_global
        settings.KRA_ETIMS_MOCK_MODE = orig_mock

        async with async_session_factory() as session:
            await session.execute(delete(EtimsTransmission).where(EtimsTransmission.shop_id == test_shop_id))
            await session.execute(delete(EtimsSequence).where(EtimsSequence.shop_id == test_shop_id))
            await session.execute(delete(EtimsConfig).where(EtimsConfig.shop_id == test_shop_id))
            await session.execute(delete(Shop).where(Shop.id == test_shop_id))
            await session.execute(delete(User).where(User.id == test_user_id))
            await session.commit()
