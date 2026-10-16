"""
Posting rules and period locks, against in-memory SQLite (only the tables the posting service touches).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.all_models import metadata
from app.models.account import AccountingPeriod, ChartOfAccount, FiscalYear
from app.models.document import Document
from app.models.identity import Shop, User
from app.models.journal import JournalEntry
from app.models.payment import DocumentPayment
from app.modules.accounts.posting import (
    PeriodLockedError,
    integrity_report,
    remove_payment_gl,
    repair_ledger,
    sync_document_gl,
    sync_payment_gl,
)

TABLES = ["users", "shops", "chart_of_accounts", "journal_entries", "fiscal_years", "accounting_periods",
          "documents", "document_payments", "expenses", "incomes"]


@pytest.fixture
async def db():
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
    s = Shop(id=uuid.uuid4(), owner_id=owner.id, name="Shop", slug=uuid.uuid4().hex[:10], is_gl_enabled=True)
    db.add_all([owner, s])
    await db.commit()
    return s


def invoice(shop, status="ISSUED", when=datetime(2026, 3, 10), net="1000", tax="160"):
    return Document(id=uuid.uuid4(), shop_id=shop.id, type="INVOICE", doc_number=f"INV-{uuid.uuid4().hex[:4]}",
                    status=status, issue_date=when, currency="KES", sub_total=D(net), tax_amount=D(tax),
                    grand_total=D(net) + D(tax))


async def entries_for(db, source_id):
    rows = (await db.execute(select(JournalEntry).where(JournalEntry.source_id == source_id))).scalars().all()
    codes = {a.id: a.code for a in (await db.execute(select(ChartOfAccount))).scalars().all()}
    return sorted((codes[e.debit_account_id], codes[e.credit_account_id], e.amount) for e in rows)


async def test_invoice_lifecycle(db, shop):
    doc = invoice(shop)
    db.add(doc)
    await db.flush()
    await sync_document_gl(db, shop.id, doc)
    assert await entries_for(db, doc.id) == [("1300", "2200", D("160")), ("1300", "4000", D("1000"))]

    doc.status = "PAID"  # paid at issue, no payment records -> settlement entry
    await sync_document_gl(db, shop.id, doc)
    assert ("1200", "1300", D("1160")) in await entries_for(db, doc.id)

    doc.status = "CANCELLED"
    await sync_document_gl(db, shop.id, doc)
    assert await entries_for(db, doc.id) == []


async def test_payment_posts_and_reverses(db, shop):
    doc = invoice(shop)
    db.add(doc)
    await db.flush()
    await sync_document_gl(db, shop.id, doc)
    pay = DocumentPayment(id=uuid.uuid4(), document_id=doc.id, shop_id=shop.id, amount=D("500"),
                          payment_date=datetime(2026, 3, 12), payment_channel="MPESA")
    db.add(pay)
    await db.flush()
    await sync_payment_gl(db, shop.id, pay, doc)
    assert await entries_for(db, pay.id) == [("1200", "1300", D("500"))]
    await remove_payment_gl(db, shop.id, pay.id)
    assert await entries_for(db, pay.id) == []


async def test_closed_period_blocks_posting_and_changes(db, shop):
    fy = FiscalYear(id=uuid.uuid4(), shop_id=shop.id, label="FY2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    march = AccountingPeriod(id=uuid.uuid4(), shop_id=shop.id, fiscal_year_id=fy.id, period_name="Mar 2026",
                             start_date=date(2026, 3, 1), end_date=date(2026, 3, 31), status="OPEN")
    db.add_all([fy, march])
    doc = invoice(shop)
    db.add(doc)
    await db.flush()
    await sync_document_gl(db, shop.id, doc)

    march.status = "CLOSED"
    await db.flush()
    doc.status = "CANCELLED"
    with pytest.raises(PeriodLockedError):
        await sync_document_gl(db, shop.id, doc)  # can't silently remove entries from a closed month
    new_doc = invoice(shop, when=datetime(2026, 3, 20))
    db.add(new_doc)
    await db.flush()
    with pytest.raises(PeriodLockedError):
        await sync_document_gl(db, shop.id, new_doc)


async def test_integrity_flags_and_repairs(db, shop):
    doc = invoice(shop)
    db.add(doc)
    await db.commit()
    report = await integrity_report(db, shop.id)
    assert not report["healthy"] and report["issues"]["documents_not_posted"]["count"] == 1
    result = await repair_ledger(db, shop.id)
    assert result["fixed"] == 1 and result["after"]["healthy"]


async def test_gl_disabled_posts_nothing(db, shop):
    shop.is_gl_enabled = False
    doc = invoice(shop)
    db.add(doc)
    await db.flush()
    assert await sync_document_gl(db, shop.id, doc) == []
