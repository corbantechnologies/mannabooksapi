import pytest
import uuid
from datetime import datetime
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
from app.models.inventory import StockLocation, StockLedger
from app.models.document import Document, DocumentItem
from app.models.payment import DocumentPayment
from app.models.expense import Expense, Income
from app.models.account import ChartOfAccount
from app.models.journal import JournalEntry


@pytest.mark.asyncio
async def test_financial_reporting_wave_e_lifecycle():
    """Verify P&L, Balance Sheet, Trial Balance, KRA VAT Return, and Stock Ledger calculations."""
    settings = get_settings()
    test_user_id = uuid.uuid4()
    test_shop_id = uuid.uuid4()
    test_slug = f"test-wave-e-{test_shop_id.hex[:8]}"

    invoice_id = uuid.uuid4()
    purchase_id = uuid.uuid4()
    product_id = uuid.uuid4()
    location_id = uuid.uuid4()
    coa_dr_id = uuid.uuid4()
    coa_cr_id = uuid.uuid4()

    async with async_session_factory() as session:
        user = User(
            id=test_user_id,
            name="Wave E Chief Financial Officer",
            email=f"cfo-{test_user_id.hex[:8]}@example.com",
            password_hash="mock_hash",
        )
        session.add(user)
        await session.flush()

        shop = Shop(
            id=test_shop_id,
            owner_id=test_user_id,
            name="Wave E Holdings Ltd",
            slug=test_slug,
            currency="KES",
        )
        session.add(shop)
        await session.flush()

        client_rec = Client(
            shop_id=test_shop_id,
            name="East Africa Breweries",
            email="purchasing@eabl.co.ke",
            client_type="CORPORATE",
            tax_pin="P051111222K",
        )
        session.add(client_rec)

        supplier_rec = Supplier(
            shop_id=test_shop_id,
            name="Kenya Power & Lighting Co",
            email="billing@kplc.co.ke",
            tax_pin="P051333444M",
        )
        session.add(supplier_rec)

        prod = Product(
            id=product_id,
            shop_id=test_shop_id,
            name="Transformer Fluid 20L",
            sku="TF-20L",
            item_type="PRODUCT",
            track_stock=True,
            stock_quantity=Decimal("50.00"),
            unit_price=Decimal("15000.00"),
            cost_price=Decimal("10000.00"),
        )
        session.add(prod)

        loc = StockLocation(
            id=location_id,
            shop_id=test_shop_id,
            name="Main Depot",
            is_default=True,
        )
        session.add(loc)
        await session.flush()

        # Seed 1: Outbound Tax Invoice (Revenue = 30,000 + 4,800 VAT = 34,800)
        # 2 units * 10,000 cost = 20,000 COGS
        inv = Document(
            id=invoice_id,
            shop_id=test_shop_id,
            client_id=client_rec.id,
            type="INVOICE",
            doc_number=f"INV-WE-{test_shop_id.hex[:6]}",
            status="PAID",
            sub_total=Decimal("30000.00"),
            tax_amount=Decimal("4800.00"),
            grand_total=Decimal("34800.00"),
            issue_date=datetime.utcnow(),
        )
        session.add(inv)

        inv_item = DocumentItem(
            document_id=invoice_id,
            product_id=product_id,
            description="Transformer Fluid 20L",
            quantity=Decimal("2.00"),
            unit_price=Decimal("15000.00"),
            tax_type="V_16",
            tax_amount=Decimal("4800.00"),
            item_total=Decimal("30000.00"),
        )
        session.add(inv_item)

        # Payment for invoice
        pay = DocumentPayment(
            document_id=invoice_id,
            shop_id=test_shop_id,
            amount=Decimal("34800.00"),
            payment_channel="BANK",
            payment_reference="NCBA-9944",
            payment_date=datetime.utcnow(),
        )
        session.add(pay)

        # Seed 2: Inbound Vendor Bill / Procurement with Input VAT (SubTotal = 10,000, VAT = 1,600)
        purchase = Document(
            id=purchase_id,
            shop_id=test_shop_id,
            supplier_id=supplier_rec.id,
            type="LPO",
            doc_number=f"LPO-WE-{test_shop_id.hex[:6]}",
            status="ISSUED",
            sub_total=Decimal("10000.00"),
            tax_amount=Decimal("1600.00"),
            grand_total=Decimal("11600.00"),
            issue_date=datetime.utcnow(),
        )
        session.add(purchase)

        # Seed 3: Operating Expense
        exp = Expense(
            shop_id=test_shop_id,
            description="Office Internet Fiber",
            amount=Decimal("5000.00"),
            currency="KES",
            category="UTILITIES",
            expense_date=datetime.utcnow(),
        )
        session.add(exp)

        # Seed 4: Non-Operating Income
        inc = Income(
            shop_id=test_shop_id,
            description="Fixed Deposit Interest",
            amount=Decimal("2000.00"),
            currency="KES",
            category="INTEREST",
            income_date=datetime.utcnow(),
        )
        session.add(inc)

        # Seed 5: Stock Movement
        stock_mv = StockLedger(
            shop_id=test_shop_id,
            product_id=product_id,
            location_id=location_id,
            movement_type="PURCHASE_RECEIPT",
            quantity=Decimal("50.00"),
            unit_cost=Decimal("10000.00"),
            notes="Initial Batch Received",
        )
        session.add(stock_mv)

        # Seed 6: Chart of Accounts & Journal Entries for Trial Balance
        coa1 = ChartOfAccount(
            id=coa_dr_id,
            shop_id=test_shop_id,
            code="1200",
            name="Cash & Bank",
            account_type="ASSET",
        )
        coa2 = ChartOfAccount(
            id=coa_cr_id,
            shop_id=test_shop_id,
            code="4100",
            name="Commercial Sales Revenue",
            account_type="REVENUE",
        )
        session.add(coa1)
        session.add(coa2)
        await session.flush()

        je = JournalEntry(
            shop_id=test_shop_id,
            entry_date=datetime.utcnow(),
            description="Bank deposit from cash sales",
            debit_account_id=coa_dr_id,
            credit_account_id=coa_cr_id,
            amount=Decimal("15000.00"),
            source_type="manual",
        )
        session.add(je)

        await session.commit()

    headers = {"X-Internal-Key": settings.INTERNAL_API_KEY}
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Test Profit & Loss Statement
            res_pl = await client.get(
                f"/v1/reports/profit-loss?shop_id={test_shop_id}&period=THIS_MONTH",
                headers=headers,
            )
            assert res_pl.status_code == 200, res_pl.text
            pl_data = res_pl.json()
            assert Decimal(str(pl_data["sales_revenue"])) == Decimal("34800.00")
            assert Decimal(str(pl_data["cogs"])) == Decimal("20000.00")  # 2 * 10,000
            assert Decimal(str(pl_data["gross_profit"])) == Decimal("14800.00")
            assert Decimal(str(pl_data["total_operating_expenses"])) == Decimal("5000.00")
            assert Decimal(str(pl_data["non_operating_income"])) == Decimal("2000.00")
            # Net Income = 14,800 - 5,000 + 2,000 = 11,800
            assert Decimal(str(pl_data["net_income"])) == Decimal("11800.00")

            # 2. Test Balance Sheet
            res_bs = await client.get(
                f"/v1/reports/balance-sheet?shop_id={test_shop_id}",
                headers=headers,
            )
            assert res_bs.status_code == 200, res_bs.text
            bs_data = res_bs.json()
            assert bs_data["is_balanced"] is True
            # Inventory valuation = 50 * 10,000 = 500,000
            assert Decimal(str(bs_data["inventory_valuation"])) == Decimal("500000.00")
            # Cash & bank = 34,800 (invoice payment) + 2,000 (income) - 5,000 (expense) = 31,800
            assert Decimal(str(bs_data["cash_and_bank"])) == Decimal("31800.00")

            # 3. Test Trial Balance
            res_tb = await client.get(
                f"/v1/reports/trial-balance?shop_id={test_shop_id}",
                headers=headers,
            )
            assert res_tb.status_code == 200, res_tb.text
            tb_data = res_tb.json()
            assert tb_data["is_balanced"] is True
            assert Decimal(str(tb_data["total_debits"])) == Decimal("15000.00")
            assert Decimal(str(tb_data["total_credits"])) == Decimal("15000.00")

            # 4. Test Statutory KRA VAT Return Summary
            now_iso = datetime.utcnow().strftime("%Y-%m-%d")
            res_vat = await client.get(
                f"/v1/reports/vat-return?shop_id={test_shop_id}&start_date={now_iso}T00:00:00&end_date={now_iso}T23:59:59",
                headers=headers,
            )
            assert res_vat.status_code == 200, res_vat.text
            vat_data = res_vat.json()
            assert Decimal(str(vat_data["box_1_standard_rated_sales"])) == Decimal("30000.00")
            assert Decimal(str(vat_data["box_1_output_vat"])) == Decimal("4800.00")
            assert Decimal(str(vat_data["box_8_total_input_vat_claimed"])) == Decimal("1600.00")
            # Net VAT Due = 4,800 - 1,600 = 3,200
            assert Decimal(str(vat_data["net_vat_due"])) == Decimal("3200.00")
            assert len(vat_data["input_vat_entries"]) == 1
            assert vat_data["input_vat_entries"][0]["supplier_name"] == "Kenya Power & Lighting Co"

            # 5. Test Stock Movement Ledger
            res_stock = await client.get(
                f"/v1/reports/stock-ledger?shop_id={test_shop_id}",
                headers=headers,
            )
            assert res_stock.status_code == 200, res_stock.text
            stock_data = res_stock.json()
            assert len(stock_data) == 1
            assert stock_data[0]["product_name"] == "Transformer Fluid 20L"
            assert Decimal(str(stock_data[0]["quantity"])) == Decimal("50.00")

            # 6. Test Stock Valuation
            res_val = await client.get(
                f"/v1/reports/stock-valuation?shop_id={test_shop_id}",
                headers=headers,
            )
            assert res_val.status_code == 200, res_val.text
            val_data = res_val.json()
            assert len(val_data) == 1
            assert Decimal(str(val_data[0]["total_valuation"])) == Decimal("500000.00")

    finally:
        async with async_session_factory() as session:
            await session.execute(delete(JournalEntry).where(JournalEntry.shop_id == test_shop_id))
            await session.execute(delete(ChartOfAccount).where(ChartOfAccount.shop_id == test_shop_id))
            await session.execute(delete(StockLedger).where(StockLedger.shop_id == test_shop_id))
            await session.execute(delete(Expense).where(Expense.shop_id == test_shop_id))
            await session.execute(delete(Income).where(Income.shop_id == test_shop_id))
            await session.execute(delete(DocumentPayment).where(DocumentPayment.shop_id == test_shop_id))
            await session.execute(delete(DocumentItem).where(DocumentItem.document_id.in_([invoice_id, purchase_id])))
            await session.execute(delete(Document).where(Document.shop_id == test_shop_id))
            await session.execute(delete(StockLocation).where(StockLocation.shop_id == test_shop_id))
            await session.execute(delete(Product).where(Product.shop_id == test_shop_id))
            await session.execute(delete(Supplier).where(Supplier.shop_id == test_shop_id))
            await session.execute(delete(Client).where(Client.shop_id == test_shop_id))
            await session.execute(delete(Shop).where(Shop.id == test_shop_id))
            await session.execute(delete(User).where(User.id == test_user_id))
            await session.commit()
