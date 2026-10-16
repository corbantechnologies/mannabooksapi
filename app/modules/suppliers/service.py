from decimal import Decimal
import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc
from app.models.supplier import Supplier
from app.modules.suppliers.schemas import SupplierCreateRequest, SupplierUpdateRequest
from datetime import datetime, date, timezone, timedelta
from app.core.datetime_utils import utcnow


async def list_suppliers(
    db: AsyncSession,
    shop_id: uuid.UUID,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Supplier]:
    q = select(Supplier).where(Supplier.shop_id == shop_id)
    if search:
        s = f"%{search.strip()}%"
        q = q.where(or_(Supplier.name.ilike(s), Supplier.email.ilike(s), Supplier.tax_pin.ilike(s)))
    q = q.order_by(desc(Supplier.created_at)).offset(offset).limit(limit)
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_supplier_by_id(db: AsyncSession, shop_id: uuid.UUID, supplier_id: uuid.UUID) -> Optional[Supplier]:
    q = select(Supplier).where(and_(Supplier.id == supplier_id, Supplier.shop_id == shop_id))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def create_supplier(db: AsyncSession, shop_id: uuid.UUID, req: SupplierCreateRequest) -> Supplier:
    supplier = Supplier(
        shop_id=shop_id,
        name=req.name.strip(),
        email=req.email.lower().strip() if req.email else None,
        phone=req.phone.strip() if req.phone else None,
        supplier_type=req.supplier_type,
        tax_pin=req.tax_pin.upper().strip() if req.tax_pin else None,
        requires_etims=req.requires_etims or bool(req.tax_pin),
        payment_terms=req.payment_terms or "NET_30",
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return supplier


async def update_supplier(
    db: AsyncSession, shop_id: uuid.UUID, supplier_id: uuid.UUID, req: SupplierUpdateRequest
) -> Optional[Supplier]:
    supplier = await get_supplier_by_id(db, shop_id, supplier_id)
    if not supplier:
        return None

    if req.name is not None:
        supplier.name = req.name.strip()
    if req.email is not None:
        supplier.email = req.email.lower().strip()
    if req.phone is not None:
        supplier.phone = req.phone.strip() if req.phone else None
    if req.supplier_type is not None:
        supplier.supplier_type = req.supplier_type
    if req.tax_pin is not None:
        supplier.tax_pin = req.tax_pin.upper().strip() if req.tax_pin else None
    if req.requires_etims is not None:
        supplier.requires_etims = req.requires_etims
    if req.payment_terms is not None:
        supplier.payment_terms = req.payment_terms

    await db.commit()
    await db.refresh(supplier)
    return supplier


async def delete_supplier(db: AsyncSession, shop_id: uuid.UUID, supplier_id: uuid.UUID) -> bool:
    supplier = await get_supplier_by_id(db, shop_id, supplier_id)
    if not supplier:
        return False
    await db.delete(supplier)
    await db.commit()
    return True


async def get_supplier_statement(
    db: AsyncSession,
    shop_id: uuid.UUID,
    supplier_id: uuid.UUID,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
):
    from app.models.document import Document
    from app.models.identity import Shop

    q_sup = select(Supplier).where(and_(Supplier.id == supplier_id, Supplier.shop_id == shop_id))
    sup_res = await db.execute(q_sup)
    supplier = sup_res.scalar_one_or_none()
    if not supplier:
        return None

    q_shop = select(Shop).where(Shop.id == shop_id)
    shop_res = await db.execute(q_shop)
    shop = shop_res.scalar_one_or_none()

    start = start_date or datetime(utcnow().year, 1, 1)
    end = end_date or utcnow()

    q_docs = (
        select(Document)
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.supplier_id == supplier_id,
                Document.issue_date >= start,
                Document.issue_date <= end,
            )
        )
        .order_by(Document.issue_date.asc())
    )
    docs_res = await db.execute(q_docs)
    docs = docs_res.scalars().all()

    running_balance = Decimal("0")
    total_debits = Decimal("0")
    total_credits = Decimal("0")
    lines = []

    for doc in docs:
        amount = Decimal(str(doc.grand_total or "0"))
        debit = Decimal("0")
        credit = Decimal("0")

        if doc.type in ["INVOICE", "GOODS_RECEIVED_NOTE", "SERVICE_COMPLETION_NOTE", "PO", "LPO", "LSO"]:
            if doc.status != "CANCELLED":
                credit = amount
                running_balance += amount
                total_credits += amount
        elif doc.type in ["PAYMENT_VOUCHER", "RECEIPT"]:
            if doc.status != "CANCELLED":
                debit = amount
                running_balance -= amount
                total_debits += amount
        elif doc.type == "DEBIT_NOTE":
            if doc.status != "CANCELLED":
                debit = amount
                running_balance -= amount
                total_debits += amount

        lines.append({
            "id": str(doc.id),
            "date": doc.issue_date.isoformat() if doc.issue_date else "",
            "documentNumber": doc.doc_number,
            "documentType": doc.type,
            "description": doc.notes or f"{doc.type} #{doc.doc_number}",
            "debit": debit,
            "credit": credit,
            "runningBalance": running_balance,
            "status": doc.status,
        })

    return {
        "success": True,
        "data": {
            "supplierName": supplier.name,
            "supplierEmail": supplier.email or "",
            "supplierPhone": supplier.phone or "",
            "taxPin": supplier.tax_pin or "",
            "currency": (shop.currency if shop else "KES") or "KES",
            "shopName": shop.name if shop else "",
            "periodLabel": f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
            "lines": lines,
            "totalDebits": total_debits,
            "totalCredits": total_credits,
            "closingBalance": running_balance,
        }
    }


