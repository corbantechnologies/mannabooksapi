import pytest
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select
from app.main import app
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.identity import Shop, User
from app.models.client import Client
from app.models.item import Product
from app.models.document import Document, DocumentItem


@pytest.mark.asyncio
async def test_lifecycle_crons_wave_f_flow():
    """Verify Overdue Sweep, Recurring Invoice safely creating DRAFTs, Quote Expiry, and fail-closed CRON_SECRET guards."""
    settings = get_settings()
    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-wave-f-{test_shop_id.hex[:8]}"

    overdue_inv_id = uuid.uuid4()
    current_inv_id = uuid.uuid4()
    recurring_inv_id = uuid.uuid4()
    quote_id = uuid.uuid4()
    product_id = uuid.uuid4()

    now = datetime.utcnow()

    async with async_session_factory() as session:
        user = User(
            id=test_user_id,
            name="Wave F Automation Admin",
            email=f"admin-{test_user_id.hex[:8]}@example.com",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Wave F Automation Co",
            slug=test_slug,
            currency="KES",
        )
        session.add(shop)
        await session.flush()

        client_rec = Client(
            shop_id=test_shop_id,
            name="Standard Group Media",
            email="finance@standardmedia.co.ke",
            client_type="CORPORATE",
            tax_pin="P051777888S",
        )
        session.add(client_rec)

        prod = Product(
            id=product_id,
            shop_id=test_shop_id,
            name="Cloud Retainer Service",
            sku="CLOUD-RET-01",
            item_type="SERVICE",
            unit_price=Decimal("45000.00"),
        )
        session.add(prod)
        await session.flush()

        # 1. Past-Due Invoice (15 days overdue)
        overdue_inv = Document(
            id=overdue_inv_id,
            shop_id=test_shop_id,
            client_id=client_rec.id,
            type="INVOICE",
            doc_number=f"INV-OD-{test_shop_id.hex[:6]}",
            status="ISSUED",
            sub_total=Decimal("20000.00"),
            tax_amount=Decimal("3200.00"),
            grand_total=Decimal("23200.00"),
            issue_date=now - timedelta(days=45),
            due_date=now - timedelta(days=15),
        )
        session.add(overdue_inv)

        # 2. Current Invoice (due in 15 days, NOT overdue)
        current_inv = Document(
            id=current_inv_id,
            shop_id=test_shop_id,
            client_id=client_rec.id,
            type="INVOICE",
            doc_number=f"INV-CUR-{test_shop_id.hex[:6]}",
            status="ISSUED",
            sub_total=Decimal("10000.00"),
            tax_amount=Decimal("1600.00"),
            grand_total=Decimal("11600.00"),
            issue_date=now - timedelta(days=5),
            due_date=now + timedelta(days=15),
        )
        session.add(current_inv)

        # 3. Recurring Parent Contract (due for monthly generation)
        recurring_inv = Document(
            id=recurring_inv_id,
            shop_id=test_shop_id,
            client_id=client_rec.id,
            type="INVOICE",
            doc_number=f"INV-REC-PARENT-{test_shop_id.hex[:6]}",
            status="ISSUED",
            is_recurring=True,
            recurring_interval="MONTHLY",
            next_recurring_date=now - timedelta(hours=2),  # Eligible
            sub_total=Decimal("45000.00"),
            tax_amount=Decimal("7200.00"),
            grand_total=Decimal("52200.00"),
            issue_date=now - timedelta(days=30),
            due_date=now,
        )
        session.add(recurring_inv)

        rec_item = DocumentItem(
            document_id=recurring_inv_id,
            product_id=product_id,
            description="Cloud Retainer Service Monthly",
            quantity=Decimal("1.00"),
            unit_price=Decimal("45000.00"),
            tax_type="V_16",
            tax_amount=Decimal("7200.00"),
            item_total=Decimal("45000.00"),
        )
        session.add(rec_item)

        # 4. Expired Quotation (validity expired 7 days ago)
        quote = Document(
            id=quote_id,
            shop_id=test_shop_id,
            client_id=client_rec.id,
            type="QUOTATION",
            doc_number=f"QT-EXP-{test_shop_id.hex[:6]}",
            status="ISSUED",
            sub_total=Decimal("8000.00"),
            tax_amount=Decimal("0.00"),
            grand_total=Decimal("8000.00"),
            issue_date=now - timedelta(days=37),
            due_date=now - timedelta(days=7),
        )
        session.add(quote)

        await session.commit()

    transport = ASGITransport(app=app)
    headers_auth = {"Authorization": f"Bearer {settings.CRON_SECRET}"}
    headers_internal = {"X-Internal-Key": settings.INTERNAL_API_KEY}

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Security Check: Unauthenticated request should fail closed with 401
            res_unauth = await client.post("/v1/crons/overdue-sweep")
            assert res_unauth.status_code == 401, res_unauth.text

            # 2. Overdue Sweep execution
            res_sweep = await client.post(
                f"/v1/crons/overdue-sweep?shop_id={test_shop_id}",
                headers=headers_auth,
            )
            assert res_sweep.status_code == 200, res_sweep.text
            sweep_data = res_sweep.json()
            assert sweep_data["marked_overdue"] >= 1
            assert sweep_data["aging_buckets"]["1-30_days"] >= 1

            # Verify in DB that overdue invoice was transitioned and current was not touched
            async with async_session_factory() as session:
                q1 = select(Document).where(Document.id == overdue_inv_id)
                r1 = await session.execute(q1)
                assert r1.scalar_one().status == "OVERDUE"

                q2 = select(Document).where(Document.id == current_inv_id)
                r2 = await session.execute(q2)
                assert r2.scalar_one().status == "ISSUED"

            # 3. Process Recurring Schedules
            res_rec = await client.post(
                f"/v1/crons/process-recurring?shop_id={test_shop_id}",
                headers=headers_internal,
            )
            assert res_rec.status_code == 200, res_rec.text
            rec_data = res_rec.json()
            assert rec_data["created_invoices"] == 1
            created_item = rec_data["items"][0]
            # Safety verification: new invoice created in DRAFT/PENDING_REVIEW state
            assert created_item["status"] == "DRAFT"

            # Verify parent next_recurring_date was advanced by ~30 days
            async with async_session_factory() as session:
                q_p = select(Document).where(Document.id == recurring_inv_id)
                r_p = await session.execute(q_p)
                p_doc = r_p.scalar_one()
                assert p_doc.next_recurring_date > now

            # 4. Quotation Expiry Sweep
            res_qt = await client.post(
                f"/v1/crons/quote-expiry?shop_id={test_shop_id}",
                headers=headers_auth,
            )
            assert res_qt.status_code == 200, res_qt.text
            assert res_qt.json()["expired_quotes"] >= 1

            # Verify quote was marked CANCELLED
            async with async_session_factory() as session:
                q_q = select(Document).where(Document.id == quote_id)
                r_q = await session.execute(q_q)
                assert r_q.scalar_one().status == "CANCELLED"

            # 5. eTIMS Retry Daemon Trigger
            res_etims = await client.post("/v1/crons/etims-retry?limit=5", headers=headers_auth)
            assert res_etims.status_code == 200, res_etims.text

    finally:
        async with async_session_factory() as session:
            # Delete all documents created for test shop
            await session.execute(delete(DocumentItem).where(DocumentItem.product_id == product_id))
            await session.execute(delete(Document).where(Document.shop_id == test_shop_id))
            await session.execute(delete(Product).where(Product.shop_id == test_shop_id))
            await session.execute(delete(Client).where(Client.shop_id == test_shop_id))
            await session.execute(delete(Shop).where(Shop.id == test_shop_id))
            await session.execute(delete(User).where(User.id == test_user_id))
            await session.commit()
