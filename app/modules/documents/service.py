import uuid
from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func
from sqlalchemy.orm import selectinload
from app.models.document import Document, DocumentItem
from app.models.item import Product
from app.models.inventory import StockLedger
from app.models.etims import EtimsTransmission
from app.modules.documents.schemas import (
    DocumentCreateRequest,
    CreditNoteCreateRequest,
    DocumentItemInput,
)
from datetime import datetime


def compute_item_tax_and_total(quantity: Decimal, unit_price: Decimal, tax_type: str) -> Tuple[Decimal, Decimal]:
    """Calculates tax amount and item total based on standard statutory rules."""
    line_sub = quantity * unit_price
    if tax_type == "V_16":
        tax = (line_sub * Decimal("0.16")).quantize(Decimal("0.01"))
        total = line_sub + tax
    else:
        tax = Decimal("0.00")
        total = line_sub
    return tax, total


async def generate_doc_number(db: AsyncSession, shop_id: uuid.UUID, doc_type: str) -> str:
    """Generates consecutive prefix-based document numbers (e.g. INV-0001, CN-0001)."""
    prefix_map = {
        "INVOICE": "INV",
        "CREDIT_NOTE": "CN",
        "RECEIPT": "RCT",
        "QUOTE": "QT",
        "LPO": "LPO",
        "DELIVERY_NOTE": "DN",
        "PAYMENT_VOUCHER": "PV",
    }
    prefix = prefix_map.get(doc_type, "DOC")
    q = select(func.count(Document.id)).where(and_(Document.shop_id == shop_id, Document.type == doc_type))
    res = await db.execute(q)
    count = (res.scalar() or 0) + 1
    return f"{prefix}-{count:04d}"


async def list_documents(
    db: AsyncSession,
    shop_id: uuid.UUID,
    doc_type: Optional[str] = None,
    doc_status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Document]:
    q = select(Document).where(Document.shop_id == shop_id).options(selectinload(Document.items))
    if doc_type:
        q = q.where(Document.type == doc_type)
    if doc_status:
        q = q.where(Document.status == doc_status)
    q = q.order_by(desc(Document.created_at)).offset(offset).limit(limit)
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_document_by_id(db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID) -> Optional[Document]:
    q = select(Document).where(and_(Document.id == document_id, Document.shop_id == shop_id)).options(selectinload(Document.items))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def create_document(db: AsyncSession, shop_id: uuid.UUID, req: DocumentCreateRequest) -> Document:
    doc_num = req.doc_number.strip() if req.doc_number else await generate_doc_number(db, shop_id, req.type)

    total_sub = Decimal("0.00")
    total_tax = Decimal("0.00")
    items_to_add: List[DocumentItem] = []

    for item_input in req.items:
        tax, total = compute_item_tax_and_total(item_input.quantity, item_input.unit_price, item_input.tax_type)
        total_sub += (item_input.quantity * item_input.unit_price)
        total_tax += tax

        items_to_add.append(
            DocumentItem(
                product_id=item_input.product_id,
                description=item_input.description.strip(),
                notes=item_input.notes.strip() if item_input.notes else None,
                quantity=item_input.quantity,
                unit_price=item_input.unit_price,
                tax_type=item_input.tax_type,
                tax_amount=tax,
                item_total=total,
            )
        )

    grand_total = total_sub + total_tax

    doc = Document(
        shop_id=shop_id,
        client_id=req.client_id,
        supplier_id=req.supplier_id,
        type=req.type,
        doc_number=doc_num,
        status=req.status,
        requires_etims=req.requires_etims,
        notes=req.notes,
        terms_and_conditions=req.terms_and_conditions,
        currency=req.currency or "KES",
        sub_total=total_sub,
        tax_amount=total_tax,
        grand_total=grand_total,
        issue_date=req.issue_date or datetime.utcnow(),
        due_date=req.due_date,
        location_id=req.location_id,
        payment_channel=req.payment_channel,
        payment_reference=req.payment_reference,
        items=items_to_add,
    )

    db.add(doc)
    await db.commit()
    return await get_document_by_id(db, shop_id, doc.id)


async def issue_credit_note(
    db: AsyncSession,
    shop_id: uuid.UUID,
    parent_doc_id: uuid.UUID,
    req: CreditNoteCreateRequest,
    user_id: Optional[uuid.UUID] = None,
) -> Document:
    """Issue a statutory KRA Credit Note referencing the parent invoice with smart inventory restocking."""
    parent = await get_document_by_id(db, shop_id, parent_doc_id)
    if not parent:
        raise ValueError("Parent invoice not found in this workspace")

    if parent.type != "INVOICE":
        raise ValueError(f"Credit note must link to an INVOICE, but parent is {parent.type}")

    cn_number = await generate_doc_number(db, shop_id, "CREDIT_NOTE")

    items_to_credit = []
    if req.items and len(req.items) > 0:
        for it in req.items:
            tax, total = compute_item_tax_and_total(it.quantity, it.unit_price, it.tax_type)
            items_to_credit.append((it, tax, total))
    else:
        # Full parent credit
        for p_item in parent.items:
            items_to_credit.append((p_item, p_item.tax_amount, p_item.item_total))

    total_sub = Decimal("0.00")
    total_tax = Decimal("0.00")
    cn_items: List[DocumentItem] = []

    for it, tax, total in items_to_credit:
        qty = it.quantity
        price = it.unit_price
        total_sub += (qty * price)
        total_tax += tax

        cn_items.append(
            DocumentItem(
                product_id=getattr(it, "product_id", None),
                description=f"CR: {it.description}",
                quantity=qty,
                unit_price=price,
                tax_type=getattr(it, "tax_type", "V_16"),
                tax_amount=tax,
                item_total=total,
            )
        )

        # Smart Restocking Logic
        condition = getattr(it, "condition", "RESALABLE")
        prod_id = getattr(it, "product_id", None)

        if req.restock_inventory and prod_id and condition == "RESALABLE":
            q_prod = select(Product).where(and_(Product.id == prod_id, Product.shop_id == shop_id)).with_for_update()
            res_prod = await db.execute(q_prod)
            prod = res_prod.scalar_one_or_none()
            if prod and prod.track_stock:
                prod.stock_quantity += qty
                db.add(
                    StockLedger(
                        shop_id=shop_id,
                        product_id=prod_id,
                        movement_type="RETURN",
                        quantity=qty,
                        unit_cost=prod.cost_price,
                        running_balance=prod.stock_quantity,
                        notes=f"Restocked via Credit Note {cn_number} (Reason: {req.reason_code})",
                        created_by_id=user_id,
                    )
                )
        elif req.restock_inventory and prod_id and condition == "DAMAGED":
            # Log audit trail of damaged write-off without inflating sellable inventory
            db.add(
                StockLedger(
                    shop_id=shop_id,
                    product_id=prod_id,
                    movement_type="ADJUSTMENT_OUT",
                    adjustment_reason="DAMAGED",
                    quantity=qty,
                    notes=f"Damaged scrap returned via Credit Note {cn_number}",
                    created_by_id=user_id,
                )
            )

    grand_total = total_sub + total_tax

    # Reason metadata in commercial terms
    reason_desc = {
        "01": "Return of Goods / Reversal",
        "02": "Invoicing Rate / Quantity Error",
        "03": "Post-Sale Price Adjustment / Commercial Discount",
    }.get(req.reason_code, "Commercial Credit Adjustment")

    credit_doc = Document(
        shop_id=shop_id,
        client_id=parent.client_id,
        type="CREDIT_NOTE",
        doc_number=cn_number,
        status="ISSUED",
        parent_document_id=parent.id,
        requires_etims=parent.requires_etims,
        notes=f"KRA Reason [{req.reason_code}] {reason_desc}: {req.reason_notes}",
        terms_and_conditions=f'{{"reasonCode":"{req.reason_code}","parentDocNumber":"{parent.doc_number}"}}',
        currency=parent.currency or "KES",
        sub_total=total_sub,
        tax_amount=total_tax,
        grand_total=grand_total,
        issue_date=datetime.utcnow(),
        items=cn_items,
    )

    db.add(credit_doc)
    await db.commit()
    return await get_document_by_id(db, shop_id, credit_doc.id)


async def update_document_status(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, new_status: str
) -> Optional[Document]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None
    doc.status = new_status
    await db.commit()
    return await get_document_by_id(db, shop_id, document_id)
