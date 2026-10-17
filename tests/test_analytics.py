"""
Analytics overview: figures come from the P&L, so a paid invoice shows up (not silent zeros).
Runs against in-memory SQLite like test_document_workflow.
"""
import uuid
from decimal import Decimal as D

import pytest

from app.db.all_models import metadata
from app.models.identity import Shop, User
from app.modules.documents import service
from app.modules.documents.schemas import DocumentCreateRequest
from app.modules.payments.schemas import RecordPaymentRequest
from app.modules.payments.service import record_payment
from app.modules.reports.router import get_analytics_overview


@pytest.fixture
async def db():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    yield session
    await session.close()
    await engine.dispose()


async def test_analytics_reflects_a_paid_invoice(db):
    owner = User(id=uuid.uuid4(), name="Owner", email=f"{uuid.uuid4().hex}@x.test", password_hash="x")
    shop = Shop(id=uuid.uuid4(), owner_id=owner.id, name="Shop", slug=uuid.uuid4().hex[:10], code="TST",
                is_gl_enabled=True)
    db.add_all([owner, shop])
    await db.commit()
    doc = await service.create_document(db, shop.id, DocumentCreateRequest.model_validate({
        "type": "INVOICE", "status": "ISSUED",
        "items": [{"description": "Widget", "quantity": "1", "unit_price": "1000", "tax_type": "V_16"}],
    }), user_id=owner.id)
    await record_payment(db, shop.id, RecordPaymentRequest(document_id=doc.id, amount=D("1160"),
                                                           payment_channel="MPESA"), owner.id)

    res = await get_analytics_overview(shop_id=shop.id, timeframe="THIS_MONTH", fiscal_year_id=None,
                                       start_date=None, end_date=None, db=db)
    assert res["totalSettledInflow"] > 0 and res["netIncome"] > 0 and res["netGrossProfit"] > 0
