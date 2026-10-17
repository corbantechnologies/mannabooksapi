"""
Loyalty on sales: redemption as a discount that lowers the taxable value, points earned when a sale
settles, reversals on void and credit notes. Runs against in-memory SQLite like test_document_workflow.
"""
import uuid
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.db.all_models import metadata
from app.models.account import ChartOfAccount
from app.models.client import Client
from app.models.identity import Shop, User
from app.models.journal import JournalEntry
from app.models.operations import ClientLoyaltyAccount, LoyaltyLedger, LoyaltyProgram, MembershipTier
from app.models.payment import DocumentPayment
from app.modules.documents import service, workflow
from app.modules.documents.schemas import CreditNoteCreateRequest, DocumentCreateRequest
from app.modules.documents.workflow import WorkflowError
from app.modules.payments.schemas import RecordPaymentRequest
from app.modules.payments.service import delete_payment, record_payment

TABLES = ["users", "shops", "clients", "suppliers", "chart_of_accounts", "journal_entries", "fiscal_years",
          "accounting_periods", "stock_locations", "products", "stock_ledger", "documents", "document_items",
          "document_tokens", "document_notes", "document_payments", "expenses", "incomes", "payment_methods",
          "loyalty_programs", "membership_tiers", "client_loyalty_accounts", "loyalty_ledger"]


@pytest.fixture
async def db():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: metadata.create_all(c, tables=[metadata.tables[t] for t in TABLES]))
    session = async_sessionmaker(engine, expire_on_commit=False)()
    yield session
    await session.close()
    await engine.dispose()


@pytest.fixture
async def shop(db):
    owner = User(id=uuid.uuid4(), name="Owner", email=f"{uuid.uuid4().hex}@x.test", password_hash="x")
    s = Shop(id=uuid.uuid4(), owner_id=owner.id, name="Shop", slug=uuid.uuid4().hex[:10], code="TST", is_gl_enabled=True)
    db.add_all([owner, s])
    await db.flush()
    # 1 point per KES 100 spent, a point is worth KES 1, redeem from 100 points.
    db.add(LoyaltyProgram(shop_id=s.id, is_enabled=True, engine_mode="HYBRID", earn_rate_kes=D("100"),
                          point_value_kes=D("1"), min_redeem_points=100))
    await db.commit()
    s.owner = owner
    return s


@pytest.fixture
async def member(db, shop):
    c = Client(id=uuid.uuid4(), shop_id=shop.id, name="Wanjiku", email="w@x.test", phone="0700000000")
    tier = MembershipTier(id=uuid.uuid4(), shop_id=shop.id, name="Gold", discount_percent=D("10"),
                          points_multiplier=D("1.5"))
    db.add_all([c, tier])
    await db.flush()
    acc = ClientLoyaltyAccount(shop_id=shop.id, client_id=c.id, member_number="MEM-1", tier_id=tier.id,
                               current_points=500, lifetime_points=500)
    db.add(acc)
    await db.commit()
    c.account = acc
    return c


def line(price="1000", qty="1"):
    return {"description": "Widget", "quantity": qty, "unit_price": price, "tax_type": "V_16"}


async def sale(db, shop, client, doc_type="INVOICE", status="ISSUED", points=0, tier=False, items=None):
    req = DocumentCreateRequest.model_validate({
        "type": doc_type, "status": status, "client_id": str(client.id) if client else None,
        "loyaltyPointsRedeemed": points, "applyTierDiscount": tier, "items": items or [line()],
    })
    return await service.create_document(db, shop.id, req, user_id=shop.owner.id)


async def points(db, client):
    await db.refresh(client.account)
    return client.account.current_points


async def ledger(db, doc):
    return (await db.execute(
        select(LoyaltyLedger.movement_type, LoyaltyLedger.points_delta)
        .where(LoyaltyLedger.source_document_id == doc.id).order_by(LoyaltyLedger.created_at)
    )).all()


async def balances(db, shop, source_id):
    """Net debit per account code for one record's journal entries."""
    codes = dict((await db.execute(select(ChartOfAccount.id, ChartOfAccount.code).where(
        ChartOfAccount.shop_id == shop.id))).all())
    out: dict[str, D] = {}
    for e in (await db.execute(select(JournalEntry).where(JournalEntry.source_id == source_id))).scalars():
        out[codes[e.debit_account_id]] = out.get(codes[e.debit_account_id], D(0)) + e.amount
        out[codes[e.credit_account_id]] = out.get(codes[e.credit_account_id], D(0)) - e.amount
    return {k: v for k, v in out.items() if v}


async def pay(db, shop, doc, amount):
    return await record_payment(db, shop.id, RecordPaymentRequest(document_id=doc.id, amount=D(amount),
                                                                  payment_channel="MPESA"), shop.owner.id)


async def test_redemption_is_a_discount_paying_earns_and_voiding_reverses(db, shop, member):
    doc = await sale(db, shop, member, points=116)
    # 1,160 gross; 116 off (VAT inclusive): VAT is charged on 900 net.
    assert (doc.sub_total, doc.tax_amount, doc.grand_total) == (D("1000"), D("144"), D("1044"))
    assert doc.loyalty_points_redeemed == 116 and doc.loyalty_discount_amount == D("116")
    assert await points(db, member) == 384
    assert await balances(db, shop, doc.id) == {"1300": D("1044"), "4900": D("100"), "4000": D("-1000"),
                                                "2200": D("-144")}

    await pay(db, shop, doc, "1044")
    doc = await service.get_document_by_id(db, shop.id, doc.id)
    assert doc.status == "PAID" and doc.loyalty_points_earned == 15  # 1,044 / 100 x 1.5 (Gold)
    assert await points(db, member) == 399

    # Viewing and refreshing again changes nothing (idempotent).
    await service.get_document_full_detail(db, shop.id, doc.id)
    await workflow.refresh_settlement(db, doc)
    assert await ledger(db, doc) == [("REDEEM", -116), ("EARN", 15)]

    # Undo the payment: the earned points go; void: the redeemed points come back.
    pid = (await db.execute(select(DocumentPayment.id).where(DocumentPayment.document_id == doc.id))).scalar()
    await delete_payment(db, shop.id, pid)
    assert await points(db, member) == 384
    doc = await service.perform_document_action(db, shop.id, doc.id, "void", shop.owner.id, "Customer changed mind")
    assert doc.status == "CANCELLED" and await points(db, member) == 500
    assert await balances(db, shop, doc.id) == {}
    await db.refresh(member.account)
    assert member.account.lifetime_points == 500


async def test_cash_receipt_earns_at_once_and_void_reverses(db, shop, member):
    rec = await sale(db, shop, member, doc_type="RECEIPT", status="PAID", points=200, items=[line("2000")])
    assert rec.grand_total == D("2120") and rec.loyalty_points_earned == 31  # 2,120 / 100 x 1.5
    assert await points(db, member) == 500 - 200 + 31
    assert await balances(db, shop, rec.id) == {"1200": D("2120"), "4900": D("172.41"), "4000": D("-2000"),
                                                "2200": D("-292.41")}
    await service.perform_document_action(db, shop.id, rec.id, "void", shop.owner.id, "Keyed twice")
    assert await points(db, member) == 500


async def test_draft_holds_no_points_until_issued(db, shop, member):
    doc = await sale(db, shop, member, status="DRAFT", points=300)
    assert doc.grand_total == D("860") and await points(db, member) == 500
    # The client spends points elsewhere before the draft is issued.
    await sale(db, shop, member, doc_type="RECEIPT", status="PAID", points=400, items=[line("500")])
    with pytest.raises(WorkflowError):
        await service.perform_document_action(db, shop.id, doc.id, "issue", shop.owner.id)


@pytest.mark.parametrize("kwargs, message", [
    ({"points": 600}, "not enough"),
    ({"points": 1200}, "not enough"),
    ({"points": 50, "client": None}, "Choose a client"),
    ({"points": 100, "doc_type": "QUOTATION", "status": "DRAFT"}, "invoices and cash receipts"),
])
async def test_redemption_is_validated(db, shop, member, kwargs, message):
    client = kwargs.pop("client", member)
    with pytest.raises(WorkflowError, match=message):
        await sale(db, shop, client, **kwargs)


async def test_redemption_cannot_exceed_the_total_or_go_below_the_minimum(db, shop, member):
    with pytest.raises(WorkflowError, match="Redeem at most 116"):
        await sale(db, shop, member, points=400, items=[line("100")])
    member.account.current_points = 80
    await db.commit()
    with pytest.raises(WorkflowError, match="at least 100"):
        await sale(db, shop, member, points=50)


async def test_points_off_means_no_redemption_or_earning(db, shop, member):
    prog = (await db.execute(select(LoyaltyProgram))).scalar_one()
    prog.engine_mode = "TIERS_ONLY"
    await db.commit()
    with pytest.raises(WorkflowError, match="doesn't use points"):
        await sale(db, shop, member, points=100)
    rec = await sale(db, shop, member, doc_type="RECEIPT", status="PAID")
    assert rec.loyalty_points_earned == 0 and await points(db, member) == 500


async def test_tier_discount_lowers_vat_and_posts_to_loyalty_discounts(db, shop, member):
    doc = await sale(db, shop, member, tier=True)
    assert (doc.sub_total, doc.tax_amount, doc.grand_total) == (D("1000"), D("144"), D("1044"))
    assert await points(db, member) == 500
    assert (await balances(db, shop, doc.id))["4900"] == D("100")


async def test_full_credit_note_returns_points_and_takes_back_earned(db, shop, member):
    doc = await sale(db, shop, member, points=116)
    await pay(db, shop, doc, "1044")
    assert await points(db, member) == 399
    cn = await service.issue_credit_note(db, shop.id, doc.id, CreditNoteCreateRequest.model_validate(
        {"reason_code": "01", "reason_notes": "Returned"}))
    assert cn.status == "PAID" and cn.grand_total == D("1044")
    assert await points(db, member) == 500
    assert await balances(db, shop, cn.id) == {"4000": D("1000"), "2200": D("144"), "4900": D("-100"),
                                               "1200": D("-1044")}


async def test_voiding_a_credit_note_takes_the_returned_points_again(db, shop, member):
    doc = await sale(db, shop, member, points=116)  # unpaid: the credit note reduces what is owed
    cn = await service.issue_credit_note(db, shop.id, doc.id, CreditNoteCreateRequest.model_validate(
        {"reason_code": "02", "reason_notes": "Half", "items": [line("450")]}))  # 522 of 1,044
    assert cn.status == "ISSUED" and await points(db, member) == 384 + 58
    await service.perform_document_action(db, shop.id, cn.id, "void", shop.owner.id, "Raised in error")
    assert await points(db, member) == 384


async def test_partial_credit_note_reduces_points_in_proportion(db, shop, member):
    doc = await sale(db, shop, member, points=200, items=[line("1000", "2")])  # 2,320 - 200 = 2,120
    await pay(db, shop, doc, "2120")
    assert await points(db, member) == 300 + 31
    await service.issue_credit_note(db, shop.id, doc.id, CreditNoteCreateRequest.model_validate(
        {"reason_code": "02", "reason_notes": "Overcharged", "items": [line("530")]}))  # 614.80 back
    # Kept 1,505.20 of 2,120: earned 22, redeemed 142 (58 returned).
    assert await points(db, member) == 500 - 142 + 22
    count = (await db.execute(select(func.count(LoyaltyLedger.id)))).scalar()
    await workflow.refresh_settlement(db, await service.get_document_by_id(db, shop.id, doc.id))
    assert (await db.execute(select(func.count(LoyaltyLedger.id)))).scalar() == count


async def test_tiers_follow_spend_and_old_sales_keep_their_points(db, shop, member):
    plat = MembershipTier(id=uuid.uuid4(), shop_id=shop.id, name="Platinum", min_spend_kes=D("3000"),
                          discount_percent=D("15"), points_multiplier=D("2"))
    db.add(plat)
    await db.commit()
    inv = await sale(db, shop, member)
    await pay(db, shop, inv, "1160")  # spend 1,160: Gold
    rec = await sale(db, shop, member, doc_type="RECEIPT", status="PAID", items=[line("2000")])
    assert rec.loyalty_points_earned == 34  # 2,320 at Gold's 1.5, the tier held before this sale
    await db.refresh(member.account)
    assert member.account.tier_id == plat.id  # spend 3,480
    before = await ledger(db, rec)
    await service.get_document_full_detail(db, shop.id, rec.id)
    from app.modules.documents.loyalty import sync_document_loyalty
    await sync_document_loyalty(db, await service.get_document_by_id(db, shop.id, rec.id))
    assert await ledger(db, rec) == before
    # A refund takes the client back below Platinum.
    await service.issue_credit_note(db, shop.id, inv.id, CreditNoteCreateRequest.model_validate(
        {"reason_code": "01", "reason_notes": "Returned"}))
    await db.refresh(member.account)
    assert member.account.tier_id != plat.id


async def test_points_expire_first_in_first_out(db, shop, member):
    from datetime import timedelta

    from app.core.datetime_utils import utcnow
    from app.modules.documents.loyalty import expire_points

    acc = member.account
    now = utcnow()
    acc.current_points = 250
    db.add_all([
        LoyaltyLedger(shop_id=shop.id, account_id=acc.id, movement_type="EARN", points_delta=300,
                      created_at=now - timedelta(days=400)),
        LoyaltyLedger(shop_id=shop.id, account_id=acc.id, movement_type="REDEEM", points_delta=-100,
                      created_at=now - timedelta(days=10)),
        LoyaltyLedger(shop_id=shop.id, account_id=acc.id, movement_type="EARN", points_delta=50,
                      created_at=now - timedelta(days=5)),
    ])
    await db.commit()
    res = await expire_points(db, shop.id, now)
    assert res["expiredPoints"] == 200 and await points(db, member) == 50  # the recent 50 stay
    assert (await expire_points(db, shop.id, now))["expiredPoints"] == 0
    prog = (await db.execute(select(LoyaltyProgram))).scalar_one()
    prog.points_expiry_days = 0  # never expire
    await db.commit()
    assert (await expire_points(db, shop.id, now + timedelta(days=1000)))["expiredPoints"] == 0


async def test_etims_lines_carry_the_discount(db, shop, member):
    from app.modules.etims.service import sales_items_for_document

    doc = await sale(db, shop, member, points=150, items=[
        line("1000"), {"description": "Service", "quantity": "2", "unit_price": "250", "tax_type": "EXEMPT"}])
    assert doc.grand_total == D("1510")
    lines = await sales_items_for_document(db, doc)
    assert sum(D(str(x.dcAmt)) for x in lines) == D("150")
    assert sum(D(str(x.taxAmt)) for x in lines) == doc.tax_amount
    assert sum(D(str(x.totAmt)) for x in lines) == doc.grand_total
    for x in lines:
        assert D(str(x.taxblAmt)) == D(str(x.splyAmt)) - D(str(x.dcAmt)) and x.dcRt > 0
    assert lines[0].taxTyCd == "A" and lines[1].taxTyCd == "C" and lines[1].prc == 250.0
