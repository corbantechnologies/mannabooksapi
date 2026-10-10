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
from app.models.item import Product
from app.models.inventory import StockLedger
from app.models.document import Document, DocumentItem, DocumentToken


@pytest.mark.asyncio
async def test_documents_and_credit_note_lifecycle():
    """Verify Invoice creation, statutory Credit Note linkage, and smart inventory restocking."""
    settings = get_settings()
    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-wave-c-{test_shop_id.hex[:8]}"

    async with async_session_factory() as session:
        user = User(
            id=test_user_id,
            name="Wave C Operator",
            email=f"wave-c-{test_user_id.hex[:8]}@example.com",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Wave C Enterprise",
            slug=test_slug,
            currency="KES",
        )
        session.add(shop)
        await session.flush()

        client_rec = Client(
            shop_id=test_shop_id,
            name="KenGen Ltd",
            email="procurement@kengen.co.ke",
            client_type="CORPORATE",
            tax_pin="P051222333A",
            requires_etims=True,
        )
        session.add(client_rec)

        # Physical trackable product
        prod = Product(
            shop_id=test_shop_id,
            name="Industrial Circuit Breaker 63A",
            sku="CB-63A-01",
            item_type="PRODUCT",
            unit_price=Decimal("10000.00"),
            cost_price=Decimal("7500.00"),
            default_tax_type="V_16",
            track_stock=True,
            stock_quantity=Decimal("20.00"),
            item_cls_cd="24101601",
            pkg_unit_cd="PCE",
        )
        session.add(prod)
        await session.commit()

        client_id = client_rec.id
        prod_id = prod.id

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}

            # 1. Create Tax Invoice
            inv_payload = {
                "client_id": str(client_id),
                "type": "INVOICE",
                "status": "ISSUED",
                "requires_etims": True,
                "items": [
                    {
                        "product_id": str(prod_id),
                        "description": "Industrial Circuit Breaker 63A",
                        "quantity": "2.00",
                        "unit_price": "10000.00",
                        "tax_type": "V_16",
                    }
                ],
            }
            inv_res = await client.post(f"/v1/documents?shop_id={test_shop_id}", json=inv_payload, headers=headers)
            assert inv_res.status_code == 201
            inv_data = inv_res.json()
            assert inv_data["doc_number"] == "INV-0001"
            assert Decimal(inv_data["sub_total"]) == Decimal("20000.00")
            assert Decimal(inv_data["tax_amount"]) == Decimal("3200.00")  # 16% of 20,000
            assert Decimal(inv_data["grand_total"]) == Decimal("23200.00")
            parent_inv_id = inv_data["id"]

            # Simulate stock decrement for sales
            async with async_session_factory() as session:
                q = select(Product).where(Product.id == prod_id)
                res = await session.execute(q)
                p = res.scalar_one()
                p.stock_quantity = Decimal("18.00")  # 20 - 2 = 18
                await session.commit()

            # 2. Issue Statutory Credit Note against the Parent Invoice (Return of 1 Item in RESALABLE condition)
            cn_payload = {
                "reason_code": "01",  # Goods return
                "reason_notes": "Customer returned 1 unit surplus",
                "restock_inventory": True,
                "items": [
                    {
                        "product_id": str(prod_id),
                        "description": "Industrial Circuit Breaker 63A",
                        "quantity": "1.00",
                        "unit_price": "10000.00",
                        "tax_type": "V_16",
                        "condition": "RESALABLE",
                    }
                ],
            }
            cn_res = await client.post(
                f"/v1/documents/{parent_inv_id}/credit-note?shop_id={test_shop_id}",
                json=cn_payload,
                headers=headers,
            )
            assert cn_res.status_code == 201
            cn_data = cn_res.json()
            assert cn_data["type"] == "CREDIT_NOTE"
            assert cn_data["doc_number"] == "CN-0001"
            assert cn_data["parent_document_id"] == parent_inv_id
            assert Decimal(cn_data["sub_total"]) == Decimal("10000.00")
            assert Decimal(cn_data["tax_amount"]) == Decimal("1600.00")
            assert Decimal(cn_data["grand_total"]) == Decimal("11600.00")

            # 3. Verify Product Inventory Was Restocked back from 18 to 19
            async with async_session_factory() as session:
                q = select(Product).where(Product.id == prod_id)
                res = await session.execute(q)
                p = res.scalar_one()
                assert p.stock_quantity == Decimal("19.00")

                # Verify StockLedger audit entry exists
                q_ledger = select(StockLedger).where(
                    StockLedger.shop_id == test_shop_id,
                    StockLedger.movement_type == "RETURN",
                )
                res_ledger = await session.execute(q_ledger)
                entry = res_ledger.scalar_one_or_none()
                assert entry is not None
                assert entry.quantity == Decimal("1.00")
                assert "CN-0001" in entry.notes

            # 4. Update Document Status
            status_res = await client.patch(
                f"/v1/documents/{parent_inv_id}/status?shop_id={test_shop_id}",
                json={"status": "PAID"},
                headers=headers,
            )
            assert status_res.status_code == 200
            assert status_res.json()["status"] == "PAID"

            # 5. Test recurring-cycle generation
            recurring_res = await client.post(
                f"/v1/documents/{parent_inv_id}/recurring-cycle?shop_id={test_shop_id}",
                headers=headers,
            )
            assert recurring_res.status_code == 200
            rec_data = recurring_res.json()
            assert rec_data["success"] is True
            assert "newDocId" in rec_data

    finally:
        async with async_session_factory() as session:
            await session.execute(delete(StockLedger).where(StockLedger.shop_id == test_shop_id))
            shop_doc_ids = select(Document.id).where(Document.shop_id == test_shop_id)
            await session.execute(delete(DocumentToken).where(DocumentToken.document_id.in_(shop_doc_ids)))
            await session.execute(delete(DocumentItem).where(DocumentItem.document_id.in_(shop_doc_ids)))
            await session.execute(delete(DocumentItem).where(DocumentItem.product_id == prod_id))
            await session.execute(delete(Document).where(Document.shop_id == test_shop_id))
            await session.execute(delete(Product).where(Product.shop_id == test_shop_id))
            await session.execute(delete(Client).where(Client.shop_id == test_shop_id))
            await session.execute(delete(Shop).where(Shop.id == test_shop_id))
            await session.execute(delete(User).where(User.id == test_user_id))
            await session.commit()
