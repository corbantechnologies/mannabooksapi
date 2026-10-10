import uuid
import secrets
from decimal import Decimal
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, or_, desc, func
from sqlalchemy.orm import selectinload
from app.models.document import Document, DocumentItem, DocumentToken, DocumentNote
from app.models.payment import DocumentPayment
from app.models.item import Product
from app.models.inventory import StockLedger
from app.models.etims import EtimsTransmission
from app.modules.documents.schemas import (
    DocumentCreateRequest,
    CreditNoteCreateRequest,
    DocumentItemInput,
    DocumentPatchRequest,
)
from datetime import datetime, timedelta


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
        "QUOTATION": "QT",
        "LPO": "LPO",
        "PO": "PO",
        "LSO": "LSO",
        "DELIVERY_NOTE": "DN",
        "DEBIT_NOTE": "DN",
        "GOODS_RECEIVED_NOTE": "GRN",
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
    client_id: Optional[uuid.UUID] = None,
    supplier_id: Optional[uuid.UUID] = None,
    limit: int = 100,
    offset: int = 0,
):
    q = select(Document).where(Document.shop_id == shop_id).options(
        selectinload(Document.items),
        selectinload(Document.client),
        selectinload(Document.supplier),
    )
    if doc_type:
        q = q.where(Document.type == doc_type)
    if doc_status:
        q = q.where(Document.status == doc_status)
    if client_id:
        q = q.where(Document.client_id == client_id)
    if supplier_id:
        q = q.where(Document.supplier_id == supplier_id)
    q = q.order_by(desc(Document.created_at)).offset(offset).limit(limit)
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_document_by_id(db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID) -> Optional[Document]:
    q = select(Document).where(and_(Document.id == document_id, Document.shop_id == shop_id)).options(
        selectinload(Document.items),
        selectinload(Document.client),
        selectinload(Document.supplier),
        selectinload(Document.token_record),
    )
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
    await db.flush()

    tok = DocumentToken(document_id=doc.id, token=secrets.token_hex(32))
    db.add(tok)

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


async def update_document(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, req: DocumentCreateRequest
) -> Optional[Document]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None

    if req.client_id is not None:
        doc.client_id = req.client_id
    if req.supplier_id is not None:
        doc.supplier_id = req.supplier_id
    if req.type:
        doc.type = req.type
    if req.notes is not None:
        doc.notes = req.notes
    if req.terms_and_conditions is not None:
        doc.terms_and_conditions = req.terms_and_conditions
    if req.currency:
        doc.currency = req.currency
    if req.due_date is not None:
        doc.due_date = req.due_date
    if req.location_id is not None:
        doc.location_id = req.location_id
    if req.payment_channel is not None:
        doc.payment_channel = req.payment_channel
    if req.payment_reference is not None:
        doc.payment_reference = req.payment_reference
    doc.requires_etims = req.requires_etims

    if req.items:
        for old_item in (doc.items or []):
            await db.delete(old_item)
        await db.flush()

        total_sub = Decimal("0.00")
        total_tax = Decimal("0.00")
        new_items = []
        for it in req.items:
            tax, total = compute_item_tax_and_total(it.quantity, it.unit_price, it.tax_type)
            total_sub += (it.quantity * it.unit_price)
            total_tax += tax
            new_items.append(
                DocumentItem(
                    document_id=doc.id,
                    product_id=it.product_id,
                    description=it.description.strip(),
                    notes=it.notes.strip() if it.notes else None,
                    quantity=it.quantity,
                    unit_price=it.unit_price,
                    tax_type=it.tax_type,
                    tax_amount=tax,
                    item_total=total,
                )
            )
        doc.items = new_items
        doc.sub_total = total_sub
        doc.tax_amount = total_tax
        doc.grand_total = total_sub + total_tax

    await db.commit()
    return await get_document_by_id(db, shop_id, document_id)


async def delete_document(db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return False
    await db.delete(doc)
    await db.commit()
    return True


async def patch_document(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, req: DocumentPatchRequest
) -> Optional[Document]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None
    if req.status is not None:
        doc.status = req.status
    if req.kra_cu_invoice_number is not None:
        doc.kra_cu_invoice_number = req.kra_cu_invoice_number.strip() or None
    if req.payment_channel is not None:
        doc.payment_channel = req.payment_channel.strip() or None
    if req.payment_reference is not None:
        doc.payment_reference = req.payment_reference.strip() or None
    if req.attachment_url is not None:
        doc.attachment_url = req.attachment_url.strip() or None
    if req.attachment_name is not None:
        doc.attachment_name = req.attachment_name.strip() or None
    if req.attachment_size is not None:
        doc.attachment_size = req.attachment_size
    if req.notes is not None:
        doc.notes = req.notes
    if req.terms_and_conditions is not None:
        doc.terms_and_conditions = req.terms_and_conditions
    if req.due_date is not None:
        doc.due_date = req.due_date
    if req.is_recurring is not None:
        doc.is_recurring = req.is_recurring
    if req.recurring_interval is not None:
        doc.recurring_interval = req.recurring_interval
    if req.next_recurring_date is not None:
        doc.next_recurring_date = req.next_recurring_date
    await db.commit()
    return await get_document_by_id(db, shop_id, document_id)


async def convert_document(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, target_type: str
) -> dict:
    source_doc = await get_document_by_id(db, shop_id, document_id)
    if not source_doc:
        raise ValueError("Source document not found")

    new_doc_number = await generate_doc_number(db, shop_id, target_type)

    new_doc = Document(
        shop_id=shop_id,
        client_id=source_doc.client_id,
        supplier_id=source_doc.supplier_id,
        type=target_type,
        doc_number=new_doc_number,
        status="ISSUED" if target_type in ("INVOICE", "RECEIPT") else "DRAFT",
        parent_document_id=source_doc.id,
        requires_etims=source_doc.requires_etims,
        notes=source_doc.notes,
        terms_and_conditions=source_doc.terms_and_conditions,
        currency=source_doc.currency,
        sub_total=source_doc.sub_total,
        tax_amount=source_doc.tax_amount,
        grand_total=source_doc.grand_total,
        issue_date=datetime.utcnow(),
        due_date=source_doc.due_date,
        location_id=source_doc.location_id,
        items=[
            DocumentItem(
                product_id=it.product_id,
                description=it.description,
                notes=it.notes,
                quantity=it.quantity,
                unit_price=it.unit_price,
                tax_type=it.tax_type,
                tax_amount=it.tax_amount,
                item_total=it.item_total,
            )
            for it in source_doc.items
        ],
    )
    db.add(new_doc)
    await db.flush()

    tok = DocumentToken(document_id=new_doc.id, token=secrets.token_hex(32))
    db.add(tok)

    if source_doc.type == "QUOTATION" and target_type == "INVOICE":
        source_doc.status = "ISSUED"
    elif source_doc.type == "INVOICE" and target_type == "RECEIPT":
        source_doc.status = "PAID"

    await db.commit()
    return {
        "success": True,
        "new_document_id": str(new_doc.id),
        "doc_number": new_doc.doc_number,
        "serial": new_doc.doc_number,
    }


async def get_document_full_detail(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID
) -> Optional[dict]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None

    # Payments
    q_payments = (
        select(DocumentPayment)
        .where(and_(DocumentPayment.document_id == document_id, DocumentPayment.shop_id == shop_id))
        .options(selectinload(DocumentPayment.recorded_by))
        .order_by(desc(DocumentPayment.payment_date))
    )
    payments_res = (await db.execute(q_payments)).scalars().all()

    # Notes
    q_notes = (
        select(DocumentNote)
        .where(and_(DocumentNote.document_id == document_id, DocumentNote.shop_id == shop_id))
        .options(selectinload(DocumentNote.user))
        .order_by(desc(DocumentNote.created_at))
    )
    notes_res = (await db.execute(q_notes)).scalars().all()

    # Parent doc
    parent_doc = None
    if doc.parent_document_id:
        q_parent = select(Document).where(and_(Document.id == doc.parent_document_id, Document.shop_id == shop_id))
        p = (await db.execute(q_parent)).scalar_one_or_none()
        if p:
            parent_doc = {
                "id": str(p.id),
                "docNumber": p.doc_number,
                "type": p.type,
                "status": p.status,
                "grandTotal": str(p.grand_total or "0.00"),
                "issueDate": p.issue_date.isoformat() if p.issue_date else None,
            }

    # Child docs
    q_children = (
        select(Document)
        .where(and_(Document.parent_document_id == doc.id, Document.shop_id == shop_id))
        .order_by(Document.created_at.asc())
    )
    children_res = (await db.execute(q_children)).scalars().all()
    child_docs = [
        {
            "id": str(c.id),
            "docNumber": c.doc_number,
            "type": c.type,
            "status": c.status,
        }
        for c in children_res
    ]

    portal_token = doc.token_record.token if doc.token_record else None

    return {
        "id": str(doc.id),
        "shopId": str(doc.shop_id),
        "docNumber": doc.doc_number,
        "type": doc.type,
        "status": doc.status,
        "kraCuInvoiceNumber": doc.kra_cu_invoice_number,
        "parentDocumentId": str(doc.parent_document_id) if doc.parent_document_id else None,
        "requiresEtims": doc.requires_etims,
        "notes": doc.notes,
        "termsAndConditions": doc.terms_and_conditions,
        "paymentChannel": doc.payment_channel,
        "paymentReference": doc.payment_reference,
        "attachmentUrl": doc.attachment_url,
        "attachmentName": doc.attachment_name,
        "attachmentSize": doc.attachment_size,
        "emailDeliveryStatus": doc.email_delivery_status,
        "lastEmailOpenedAt": doc.last_email_opened_at.isoformat() if doc.last_email_opened_at else None,
        "currency": doc.currency or "KES",
        "subTotal": str(doc.sub_total or "0.00"),
        "taxAmount": str(doc.tax_amount or "0.00"),
        "grandTotal": str(doc.grand_total or "0.00"),
        "issueDate": doc.issue_date.isoformat() if doc.issue_date else None,
        "dueDate": doc.due_date.isoformat() if doc.due_date else None,
        "isRecurring": doc.is_recurring,
        "recurringInterval": doc.recurring_interval,
        "nextRecurringDate": doc.next_recurring_date.isoformat() if doc.next_recurring_date else None,
        "portalToken": portal_token,
        "client": {
            "id": str(doc.client.id),
            "name": doc.client.name,
            "email": doc.client.email,
            "phone": getattr(doc.client, "phone", None),
            "taxPin": getattr(doc.client, "tax_pin", None),
        } if doc.client else None,
        "supplier": {
            "id": str(doc.supplier.id),
            "name": doc.supplier.name,
            "email": doc.supplier.email,
            "phone": getattr(doc.supplier, "phone", None),
            "taxPin": getattr(doc.supplier, "tax_pin", None),
        } if doc.supplier else None,
        "items": [
            {
                "id": str(it.id),
                "productId": str(it.product_id) if it.product_id else None,
                "description": it.description,
                "notes": it.notes,
                "quantity": str(it.quantity),
                "unitPrice": str(it.unit_price),
                "taxType": it.tax_type,
                "taxAmount": str(it.tax_amount),
                "itemTotal": str(it.item_total),
            }
            for it in (doc.items or [])
        ],
        "payments": [
            {
                "id": str(p.id),
                "amount": str(p.amount),
                "paymentDate": p.payment_date.isoformat() if p.payment_date else None,
                "paymentChannel": p.payment_channel,
                "paymentReference": p.payment_reference,
                "notes": p.notes,
                "recordedBy": {"name": p.recorded_by.name} if getattr(p, "recorded_by", None) else None,
            }
            for p in payments_res
        ],
        "notesList": [
            {
                "id": str(n.id),
                "note": n.note,
                "createdAt": n.created_at.isoformat() if n.created_at else None,
                "user": {"name": n.user.name, "email": n.user.email} if getattr(n, "user", None) else None,
            }
            for n in notes_res
        ],
        "parentDocument": parent_doc,
        "childDocuments": child_docs,
    }


async def add_document_note(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, user_id: uuid.UUID, note: str
) -> DocumentNote:
    note_obj = DocumentNote(
        shop_id=shop_id,
        document_id=document_id,
        user_id=user_id,
        note=note.strip(),
    )
    db.add(note_obj)
    await db.commit()
    return note_obj


async def delete_document_note(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, note_id: uuid.UUID
) -> bool:
    q = select(DocumentNote).where(
        and_(
            DocumentNote.id == note_id,
            DocumentNote.document_id == document_id,
            DocumentNote.shop_id == shop_id,
        )
    )
    res = await db.execute(q)
    note = res.scalar_one_or_none()
    if not note:
        return False
    await db.delete(note)
    await db.commit()
    return True


async def bulk_update_document_status(
    db: AsyncSession, shop_id: uuid.UUID, document_ids: List[uuid.UUID], new_status: str
) -> int:
    q = (
        update(Document)
        .where(and_(Document.shop_id == shop_id, Document.id.in_(document_ids)))
        .values(status=new_status)
    )
    res = await db.execute(q)
    await db.commit()
    return res.rowcount


async def generate_recurring_cycle(db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID) -> dict:
    q = select(Document).where(and_(Document.id == document_id, Document.shop_id == shop_id)).options(
        selectinload(Document.items)
    )
    res = await db.execute(q)
    source_doc = res.scalar_one_or_none()
    if not source_doc:
        raise ValueError("Source recurring invoice template could not be found.")

    new_doc_number = await generate_doc_number(db, shop_id, "INVOICE")
    now = datetime.utcnow()
    due = now + timedelta(days=30)

    new_doc = Document(
        id=uuid.uuid4(),
        shop_id=shop_id,
        client_id=source_doc.client_id,
        supplier_id=source_doc.supplier_id,
        type="INVOICE",
        doc_number=new_doc_number,
        status="ISSUED",
        requires_etims=source_doc.requires_etims,
        notes=f"Generated automatically from recurring series template ({source_doc.doc_number})",
        terms_and_conditions=source_doc.terms_and_conditions,
        currency=source_doc.currency,
        exchange_rate=source_doc.exchange_rate,
        sub_total=source_doc.sub_total,
        tax_amount=source_doc.tax_amount,
        grand_total=source_doc.grand_total,
        issue_date=now,
        due_date=due,
        location_id=source_doc.location_id,
        is_recurring=False,
        items=[
            DocumentItem(
                product_id=it.product_id,
                description=it.description,
                notes=it.notes,
                quantity=it.quantity,
                unit_price=it.unit_price,
                tax_type=it.tax_type,
                tax_amount=it.tax_amount,
                item_total=it.item_total,
            )
            for it in source_doc.items
        ],
    )
    db.add(new_doc)
    await db.flush()

    tok = DocumentToken(document_id=new_doc.id, token=secrets.token_hex(32))
    db.add(tok)

    # Advance parent next recurring date
    interval = source_doc.recurring_interval or "MONTHLY"
    curr_next = source_doc.next_recurring_date or now
    if interval == "WEEKLY":
        source_doc.next_recurring_date = curr_next + timedelta(days=7)
    elif interval == "MONTHLY":
        source_doc.next_recurring_date = curr_next + timedelta(days=30)
    elif interval == "QUARTERLY":
        source_doc.next_recurring_date = curr_next + timedelta(days=90)
    elif interval == "YEARLY":
        source_doc.next_recurring_date = curr_next + timedelta(days=365)

    await db.commit()
    return {"success": True, "newDocId": str(new_doc.id), "doc_number": new_doc.doc_number}


