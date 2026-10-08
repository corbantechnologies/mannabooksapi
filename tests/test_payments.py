import pytest
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select
from app.main import app
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.identity import Shop, User
from app.models.client import Client
from app.models.document import Document, DocumentItem
from app.models.payment import DocumentPayment


@pytest.mark.asyncio
async def test_payments_and_mpesa_c2b_flow():
    """Verify installment payments, status transitions (PARTIALLY_PAID -> PAID), and M-Pesa C2B idempotency."""
    settings = get_settings()
    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-wave-d-{test_shop_id.hex[:8]}"
    invoice_id = uuid.uuid4()
    invoice_doc_num = f"INV-WD-{test_shop_id.hex[:6]}"

    async with async_session_factory() as session:
        user = User(
            id=test_user_id,
            name="Wave D Operator",
            email=f"wave-d-{test_user_id.hex[:8]}@example.com",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Wave D Enterprise",
            slug=test_slug,
            currency="KES",
        )
        session.add(shop)
        await session.flush()

        client_rec = Client(
            shop_id=test_shop_id,
            name="Safari Traders",
            email="finance@safaritraders.co.ke",
            client_type="CORPORATE",
            tax_pin="P051999888Z",
        )
        session.add(client_rec)
        await session.flush()

        # Seed Invoice with grand_total = 10,000.00
        invoice = Document(
            id=invoice_id,
            shop_id=test_shop_id,
            client_id=client_rec.id,
            type="INVOICE",
            doc_number=invoice_doc_num,
            status="ISSUED",
            sub_total=Decimal("10000.00"),
            tax_amount=Decimal("0.00"),
            grand_total=Decimal("10000.00"),
        )
        session.add(invoice)

        item = DocumentItem(
            document_id=invoice_id,
            description="Consultancy Service",
            quantity=Decimal("1.00"),
            unit_price=Decimal("10000.00"),
            tax_type="V_0",
            tax_amount=Decimal("0.00"),
            item_total=Decimal("10000.00"),
        )
        session.add(item)
        await session.commit()

    headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Record Partial Payment (KES 4,000)
            pay1_payload = {
                "document_id": str(invoice_id),
                "amount": "4000.00",
                "payment_channel": "BANK",
                "payment_reference": "EFT-884920",
                "notes": "First installment via NCBA Bank",
            }
            res_pay1 = await client.post(
                f"/v1/payments/record?shop_id={test_shop_id}&user_id={test_user_id}",
                json=pay1_payload,
                headers=headers,
            )
            assert res_pay1.status_code == 200, res_pay1.text
            pay1_data = res_pay1.json()
            assert pay1_data["status"] == "PARTIALLY_PAID"
            assert Decimal(str(pay1_data["total_paid"])) == Decimal("4000.00")
            assert Decimal(str(pay1_data["remaining_balance"])) == Decimal("6000.00")

            # 2. Record Second Payment (KES 6,000) to settle in full
            pay2_payload = {
                "document_id": str(invoice_id),
                "amount": "6000.00",
                "payment_channel": "BANK",
                "payment_reference": "EFT-884921",
                "notes": "Final settlement",
            }
            res_pay2 = await client.post(
                f"/v1/payments/record?shop_id={test_shop_id}&user_id={test_user_id}",
                json=pay2_payload,
                headers=headers,
            )
            assert res_pay2.status_code == 200, res_pay2.text
            pay2_data = res_pay2.json()
            assert pay2_data["status"] == "PAID"
            assert Decimal(str(pay2_data["total_paid"])) == Decimal("10000.00")
            assert Decimal(str(pay2_data["remaining_balance"])) == Decimal("0.00")

            # 3. Retrieve payment history for document
            res_history = await client.get(
                f"/v1/payments/by-document/{invoice_id}?shop_id={test_shop_id}",
                headers=headers,
            )
            assert res_history.status_code == 200, res_history.text
            history = res_history.json()
            assert len(history) == 2
            refs = [p["payment_reference"] for p in history]
            assert "EFT-884920" in refs
            assert "EFT-884921" in refs

            # 4. M-Pesa C2B Callback Simulation against a fresh invoice
            c2b_invoice_id = uuid.uuid4()
            c2b_doc_num = f"INV-C2B-{test_shop_id.hex[:6]}"
            async with async_session_factory() as session:
                c2b_invoice = Document(
                    id=c2b_invoice_id,
                    shop_id=test_shop_id,
                    type="INVOICE",
                    doc_number=c2b_doc_num,
                    status="ISSUED",
                    sub_total=Decimal("2500.00"),
                    tax_amount=Decimal("0.00"),
                    grand_total=Decimal("2500.00"),
                )
                session.add(c2b_invoice)
                await session.commit()

            c2b_payload = {
                "TransactionType": "Pay Bill",
                "TransID": f"QHK{uuid.uuid4().hex[:7].upper()}",
                "TransTime": "20261008120000",
                "TransAmount": "2500.00",
                "BusinessShortCode": "600100",
                "BillRefNumber": c2b_doc_num,
                "MSISDN": "254712345678",
                "FirstName": "Grace",
                "LastName": "Wanjiku",
            }

            # First callback processing
            res_c2b = await client.post("/v1/payments/mpesa/c2b-callback", json=c2b_payload)
            assert res_c2b.status_code == 200, res_c2b.text
            assert res_c2b.json()["ResultCode"] == 0
            assert res_c2b.json()["ResultDesc"] == "Success"

            # Check invoice status updated to PAID
            async with async_session_factory() as session:
                q_verify = select(Document).where(Document.id == c2b_invoice_id)
                res_v = await session.execute(q_verify)
                verified_doc = res_v.scalar_one()
                assert verified_doc.status == "PAID"
                assert verified_doc.payment_channel == "MPESA"
                assert verified_doc.payment_reference == c2b_payload["TransID"]

            # Duplicate callback replay (Idempotency test)
            res_c2b_dup = await client.post("/v1/payments/mpesa/c2b-callback", json=c2b_payload)
            assert res_c2b_dup.status_code == 200, res_c2b_dup.text
            assert res_c2b_dup.json()["ResultCode"] == 0
            assert "Duplicate" in res_c2b_dup.json()["ResultDesc"]

    finally:
        # Cleanup
        async with async_session_factory() as session:
            await session.execute(delete(DocumentPayment).where(DocumentPayment.shop_id == test_shop_id))
            await session.execute(delete(DocumentItem).where(DocumentItem.document_id.in_([invoice_id, c2b_invoice_id])))
            await session.execute(delete(Document).where(Document.shop_id == test_shop_id))
            await session.execute(delete(Client).where(Client.shop_id == test_shop_id))
            await session.execute(delete(Shop).where(Shop.id == test_shop_id))
            await session.execute(delete(User).where(User.id == test_user_id))
            await session.commit()
