"""
Document workflow: allowed status changes, money-driven statuses, credit notes, stock and locks.
Runs against in-memory SQLite (only the tables these services touch).
"""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.core.datetime_utils import utcnow
from app.db.all_models import metadata
from app.models.document import Document
from app.models.identity import Shop, User
from app.models.inventory import StockLedger
from app.models.item import Product
from app.models.journal import JournalEntry
from app.models.payment import DocumentPayment
from app.modules.documents import service, workflow
from app.modules.documents.schemas import CreditNoteCreateRequest, DocumentCreateRequest, DocumentPatchRequest
from app.modules.documents.workflow import WorkflowError
from app.modules.payments.schemas import RecordPaymentRequest
from app.modules.payments.service import delete_payment, record_payment

TABLES = ["users", "shops", "clients", "suppliers", "chart_of_accounts", "journal_entries", "fiscal_years",
          "accounting_periods", "stock_locations", "products", "stock_ledger", "documents", "document_items",
          "document_tokens", "document_notes", "document_payments", "expenses", "incomes", "payment_methods"]


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
    await db.commit()
    s.owner = owner
    return s


@pytest.fixture
async def product(db, shop):
    p = Product(id=uuid.uuid4(), shop_id=shop.id, name="Widget", unit_price=D("1000"), cost_price=D("600"),
                track_stock=True, stock_quantity=D("10"))
    db.add(p)
    await db.commit()
    return p


async def new_invoice(db, shop, product=None, qty="1", status="DRAFT", due=None, **kw):
    req = DocumentCreateRequest.model_validate({
        "type": "INVOICE", "status": status, "due_date": due,
        "items": [{"product_id": str(product.id) if product else None, "description": "Widget",
                   "quantity": qty, "unit_price": "1000", "tax_type": "V_16"}],
        **kw,
    })
    return await service.create_document(db, shop.id, req, user_id=shop.owner.id)


async def act(db, shop, doc, action, reason=None):
    return await service.perform_document_action(db, shop.id, doc.id, action, user_id=shop.owner.id, reason=reason)


async def pay(db, shop, doc, amount):
    return await record_payment(db, shop.id, RecordPaymentRequest(document_id=doc.id, amount=D(amount),
                                                                  payment_channel="MPESA"), shop.owner.id)


async def stock(db, product):
    await db.refresh(product)
    return D(str(product.stock_quantity))


async def test_issue_moves_stock_and_void_restores_it(db, shop, product):
    doc = await new_invoice(db, shop, product, qty="3")
    assert doc.status == "DRAFT" and await stock(db, product) == D("10")
    doc = await act(db, shop, doc, "issue")
    assert doc.status == "ISSUED" and await stock(db, product) == D("7")
    doc = await act(db, shop, doc, "void", reason="Wrong customer")
    assert doc.status == "CANCELLED" and await stock(db, product) == D("10")
    entries = (await db.execute(select(JournalEntry).where(JournalEntry.source_id == doc.id))).scalars().all()
    assert entries == []
    with pytest.raises(WorkflowError):
        await act(db, shop, doc, "issue")  # cancelled is final


async def test_void_needs_a_reason(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop), "issue")
    with pytest.raises(WorkflowError):
        await act(db, shop, doc, "void")


async def test_partial_payment_locks_manual_status_changes(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop), "issue")
    await pay(db, shop, doc, "500")
    doc = await service.get_document_by_id(db, shop.id, doc.id)
    assert doc.status == "PARTIALLY_PAID"
    for target in ("DRAFT", "ISSUED", "OVERDUE", "PAID"):
        with pytest.raises(WorkflowError):
            await service.update_document_status(db, shop.id, doc.id, target)
    with pytest.raises(WorkflowError):
        await act(db, shop, doc, "void")
    flow = await workflow.available_actions(db, doc)
    assert flow["balanceDue"] == "660.00" and flow["canRecordPayment"] and not flow["canEdit"]


async def test_payments_drive_paid_and_back(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop), "issue")
    await pay(db, shop, doc, "1160")
    doc = await service.get_document_by_id(db, shop.id, doc.id)
    assert doc.status == "PAID"
    pid = (await db.execute(select(DocumentPayment.id).where(DocumentPayment.document_id == doc.id))).scalar()
    await delete_payment(db, shop.id, pid)
    doc = await service.get_document_by_id(db, shop.id, doc.id)
    assert doc.status == "ISSUED"


async def test_payment_needs_an_issued_invoice(db, shop):
    doc = await new_invoice(db, shop)
    with pytest.raises(WorkflowError):
        await pay(db, shop, doc, "100")


async def test_overdue_keeps_partial_payment_and_clears_when_paid(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop, due=(utcnow() + timedelta(days=5)).isoformat()), "issue")
    await pay(db, shop, doc, "100")
    doc = await service.get_document_by_id(db, shop.id, doc.id)
    doc.due_date = utcnow() - timedelta(days=1)
    await db.flush()
    assert await workflow.refresh_settlement(db, doc) and doc.status == "OVERDUE"
    assert (await workflow.figures(db, doc)).paid == D("100")
    await pay(db, shop, doc, "1060")
    doc = await service.get_document_by_id(db, shop.id, doc.id)
    assert doc.status == "PAID"


async def test_credit_note_reduces_balance_and_is_capped(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop, qty="2"), "issue")  # 2,320
    one = {"description": "Widget", "quantity": "1", "unit_price": "1000", "tax_type": "V_16"}
    cn = await service.issue_credit_note(db, shop.id, doc.id, CreditNoteCreateRequest.model_validate(
        {"reason_code": "02", "reason_notes": "Overcharged", "items": [one]}))
    assert cn.status == "ISSUED"
    f = await workflow.figures(db, await service.get_document_by_id(db, shop.id, doc.id))
    assert f.balance == D("1160") and f.credited == D("1160")
    with pytest.raises(WorkflowError):
        await service.issue_credit_note(db, shop.id, doc.id, CreditNoteCreateRequest.model_validate(
            {"reason_code": "02", "reason_notes": "Again", "items": [{**one, "quantity": "2"}]}))
    await service.issue_credit_note(db, shop.id, doc.id, CreditNoteCreateRequest.model_validate(
        {"reason_code": "02", "reason_notes": "Rest", "items": [one]}))
    doc = await service.get_document_by_id(db, shop.id, doc.id)
    assert doc.status == "PAID"  # settled by credit notes
    gl = (await db.execute(select(JournalEntry).where(JournalEntry.source_id == doc.id))).scalars().all()
    assert all("settled" not in (e.description or "") for e in gl)  # no fake cash entry


async def test_credit_note_on_paid_invoice_is_a_refund(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop), "issue")
    await pay(db, shop, doc, "1160")
    cn = await service.issue_credit_note(db, shop.id, doc.id, CreditNoteCreateRequest.model_validate(
        {"reason_code": "01", "reason_notes": "Returned"}))
    assert cn.status == "PAID"


async def test_only_drafts_can_be_edited_or_deleted(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop), "issue")
    req = DocumentCreateRequest.model_validate({"type": "INVOICE", "items": [
        {"description": "Changed", "quantity": "1", "unit_price": "1", "tax_type": "V_16"}]})
    with pytest.raises(WorkflowError):
        await service.update_document(db, shop.id, doc.id, req)
    with pytest.raises(WorkflowError):
        await service.delete_document(db, shop.id, doc.id)
    with pytest.raises(WorkflowError):
        await service.patch_document(db, shop.id, doc.id, DocumentPatchRequest.model_validate({"termsAndConditions": "x"}))
    # Extending the due date of an unpaid invoice is fine.
    later = utcnow() + timedelta(days=30)
    doc = await service.patch_document(db, shop.id, doc.id, DocumentPatchRequest.model_validate({"due_date": later.isoformat()}))
    assert doc.due_date is not None


async def test_receipt_only_after_full_payment(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop), "issue")
    with pytest.raises(WorkflowError):
        await service.convert_document(db, shop.id, doc.id, "RECEIPT")
    await pay(db, shop, doc, "1160")
    res = await service.convert_document(db, shop.id, doc.id, "RECEIPT")
    receipt = await service.get_document_by_id(db, shop.id, uuid.UUID(res["new_document_id"]))
    assert receipt.status == "PAID"
    assert (await service.get_document_by_id(db, shop.id, doc.id)).status == "PAID"


async def test_quotation_flow(db, shop, product):
    req = DocumentCreateRequest.model_validate({
        "type": "QUOTATION", "due_date": (utcnow() - timedelta(days=1)).isoformat(),
        "items": [{"product_id": str(product.id), "description": "Widget", "quantity": "1", "unit_price": "1000"}],
    })
    quote = await service.create_document(db, shop.id, req)
    with pytest.raises(WorkflowError):
        await service.convert_document(db, shop.id, quote.id, "INVOICE")  # still a draft
    quote = await act(db, shop, quote, "issue")
    await workflow.sweep_overdue_and_expired(db, shop.id)
    await db.commit()
    quote = await service.get_document_by_id(db, shop.id, quote.id)
    await db.refresh(quote)
    assert quote.status == "EXPIRED"
    quote = await act(db, shop, quote, "revise")
    assert quote.status == "DRAFT"
    await service.patch_document(db, shop.id, quote.id, DocumentPatchRequest.model_validate(
        {"due_date": (utcnow() + timedelta(days=14)).isoformat()}))
    quote = await act(db, shop, await service.get_document_by_id(db, shop.id, quote.id), "issue")
    assert await stock(db, product) == D("10")  # quotations don't move stock
    await service.convert_document(db, shop.id, quote.id, "INVOICE")
    assert (await service.get_document_by_id(db, shop.id, quote.id)).status == "CONFIRMED"
    assert await stock(db, product) == D("9")  # the invoice did
    with pytest.raises(WorkflowError):
        await service.convert_document(db, shop.id, quote.id, "INVOICE")


async def test_invoice_created_paid_records_the_payment(db, shop):
    doc = await new_invoice(db, shop, status="PAID", payment_channel="cash")
    assert doc.status == "PAID"
    payments = (await db.execute(select(DocumentPayment).where(DocumentPayment.document_id == doc.id))).scalars().all()
    assert len(payments) == 1 and payments[0].amount == D("1160") and payments[0].payment_channel == "CASH"


async def test_legacy_paid_invoice_without_payments_stays_paid(db, shop):
    doc = Document(id=uuid.uuid4(), shop_id=shop.id, type="INVOICE", doc_number="OLD-1", status="PAID",
                   issue_date=datetime(2026, 1, 5), due_date=datetime(2026, 1, 20), currency="KES",
                   sub_total=D("1000"), tax_amount=D("160"), grand_total=D("1160"))
    db.add(doc)
    await db.commit()
    assert not await workflow.refresh_settlement(db, doc) and doc.status == "PAID"
    assert (await workflow.figures(db, doc)).balance == 0


async def test_bulk_status_skips_what_the_workflow_forbids(db, shop):
    draft = await new_invoice(db, shop)
    issued = await act(db, shop, await new_invoice(db, shop), "issue")
    await pay(db, shop, issued, "10")
    res = await service.bulk_update_document_status(db, shop.id, [draft.id, issued.id], "ISSUED", shop.owner.id)
    assert res["updated"] == 1 and len(res["skipped"]) == 1


async def test_status_changes_are_noted(db, shop):
    doc = await act(db, shop, await new_invoice(db, shop), "issue")
    detail = await service.get_document_full_detail(db, shop.id, doc.id)
    assert any("Issue invoice" in n["note"] for n in detail["notesList"])
    assert {a["action"] for a in detail["workflow"]["actions"]} == {"void"}


def test_every_type_has_a_workflow_and_valid_targets():
    from app.models.document import Document as Doc

    types = Doc.__table__.c.type.type.enums
    statuses = set(Doc.__table__.c.status.type.enums)
    for t in types:
        assert t in workflow.TRANSITIONS
        for tr in workflow.TRANSITIONS[t]:
            assert tr.target in statuses and set(tr.sources) <= statuses
