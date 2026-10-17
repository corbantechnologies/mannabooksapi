import logging
import uuid
import secrets
from decimal import Decimal
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, asc
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
from app.core.datetime_utils import to_naive_utc
from app.core.datetime_utils import utcnow
from app.modules.documents import loyalty, workflow
from app.modules.documents.workflow import WorkflowError



logger = logging.getLogger("mannabooks.documents")

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


DOC_TYPE_MAPPING = {
    "QUOTE": "QUOTATION",
    "QUOTATION": "QUOTATION",
    "ESTIMATE": "QUOTATION",
    "PROFORMA": "INVOICE",
    "PROFORMA_INVOICE": "INVOICE",
    "INV": "INVOICE",
    "INVOICE": "INVOICE",
    "RECEIPT": "RECEIPT",
    "REC": "RECEIPT",
    "LPO": "LPO",
    "PO": "PO",
    "PURCHASE_ORDER": "PO",
    "LSO": "LSO",
    "DELIVERY_NOTE": "DELIVERY_NOTE",
    "DN": "DELIVERY_NOTE",
    "CREDIT_NOTE": "CREDIT_NOTE",
    "CN": "CREDIT_NOTE",
    "DEBIT_NOTE": "DEBIT_NOTE",
    "GOODS_RECEIVED_NOTE": "GOODS_RECEIVED_NOTE",
    "GRN": "GOODS_RECEIVED_NOTE",
    "SERVICE_COMPLETION_NOTE": "SERVICE_COMPLETION_NOTE",
    "PAYMENT_VOUCHER": "PAYMENT_VOUCHER",
    "PV": "PAYMENT_VOUCHER",
    "PAYROLL_VOUCHER": "PAYROLL_VOUCHER",
}

DOC_STATUS_MAPPING = {
    "SENT": "ISSUED",
    "OPEN": "ISSUED",
    "PENDING": "ISSUED",
    "APPROVED": "ISSUED",
}


def normalize_doc_type(doc_type: Optional[str]) -> str:
    if not doc_type:
        return "INVOICE"
    t = str(doc_type).strip().upper()
    return DOC_TYPE_MAPPING.get(t, t)


def normalize_doc_status(status: Optional[str]) -> str:
    if not status:
        return "DRAFT"
    s = str(status).strip().upper()
    return DOC_STATUS_MAPPING.get(s, s)


async def _sync_gl(db: AsyncSession, shop_id: uuid.UUID, doc: Document) -> None:
    """Keep the ledger in step with this document. Closed periods block the change (409)."""
    from app.modules.accounts.posting import PeriodLockedError, sync_document_gl

    try:
        async with db.begin_nested():
            await sync_document_gl(db, shop_id, doc)
    except PeriodLockedError:
        raise
    except Exception:
        # Never lose the business change because of a posting bug; the integrity check will flag it.
        logger.exception("GL sync failed for document %s", doc.id)


async def generate_doc_number(db: AsyncSession, shop_id: uuid.UUID, doc_type: str) -> str:
    """
    Generates professional randomized document numbers in the format:
    {shop_code}-{DOC}-{random_6_chr} (e.g. OI1BJ-QT-X7B2D9, OI1BJ-INV-9M4K2A).
    """
    canonical_type = normalize_doc_type(doc_type)
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
        "DEBIT_NOTE": "DBN",
        "GOODS_RECEIVED_NOTE": "GRN",
        "PAYMENT_VOUCHER": "PV",
        "PAYROLL_VOUCHER": "PRV",
    }
    prefix = prefix_map.get(canonical_type, prefix_map.get(doc_type, "DOC"))

    from app.models.identity import Shop
    q_shop = select(Shop.code, Shop.slug).where(Shop.id == shop_id)
    res_shop = await db.execute(q_shop)
    shop_row = res_shop.first()

    shop_code = None
    if shop_row:
        shop_code = shop_row[0] or (shop_row[1][:5].upper() if shop_row[1] else None)
    if not shop_code:
        shop_code = "MBK"
    shop_code = str(shop_code).strip().upper()

    chars = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    for _ in range(20):
        rand_suffix = "".join(secrets.choice(chars) for _ in range(6))
        candidate = f"{shop_code}-{prefix}-{rand_suffix}"
        q_exists = select(Document.id).where(and_(Document.shop_id == shop_id, Document.doc_number == candidate))
        exists = (await db.execute(q_exists)).scalar_one_or_none()
        if not exists:
            return candidate

    return f"{shop_code}-{prefix}-{secrets.token_hex(3).upper()}"


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
    overdue, expired = await workflow.sweep_overdue_and_expired(db, shop_id)
    if overdue or expired:
        await db.commit()
    q = select(Document).where(Document.shop_id == shop_id).options(
        selectinload(Document.items),
        selectinload(Document.client),
        selectinload(Document.supplier),
    )
    if doc_type:
        q = q.where(Document.type == normalize_doc_type(doc_type))
    if doc_status:
        q = q.where(Document.status == normalize_doc_status(doc_status))
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


async def create_document(
    db: AsyncSession, shop_id: uuid.UUID, req: DocumentCreateRequest, user_id: Optional[uuid.UUID] = None
) -> Document:
    canonical_type = normalize_doc_type(req.type)
    requested_status = normalize_doc_status(req.status)
    # An invoice created as paid is issued and settled with a recorded payment, so the ledger,
    # balance and payment history agree.
    settle_on_create = canonical_type in workflow.SETTLED_TYPES and requested_status == "PAID"
    canonical_status = "ISSUED" if settle_on_create else workflow.initial_status(canonical_type, requested_status)
    doc_num = req.doc_number.strip() if req.doc_number else await generate_doc_number(db, shop_id, canonical_type)

    items_to_add: List[DocumentItem] = []
    lines = []

    for item_input in req.items:
        tax, total = compute_item_tax_and_total(item_input.quantity, item_input.unit_price, item_input.tax_type)
        lines.append((item_input.quantity * item_input.unit_price, tax, item_input.tax_type))

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

    priced = await loyalty.price_document(
        db, shop_id, canonical_type, req.client_id, lines, req.loyalty_points_redeemed, req.apply_tier_discount
    )
    grand_total = priced.grand_total

    doc = Document(
        shop_id=shop_id,
        client_id=req.client_id,
        supplier_id=req.supplier_id,
        type=canonical_type,
        doc_number=doc_num,
        status=canonical_status,
        requires_etims=req.requires_etims,
        notes=req.notes,
        terms_and_conditions=req.terms_and_conditions,
        currency=req.currency or "KES",
        sub_total=priced.sub_total,
        tax_amount=priced.tax_amount,
        grand_total=grand_total,
        loyalty_points_redeemed=priced.points_redeemed,
        loyalty_discount_amount=priced.discount,
        issue_date=to_naive_utc(req.issue_date) or utcnow(),
        due_date=to_naive_utc(req.due_date),
        location_id=req.location_id,
        payment_channel=req.payment_channel,
        payment_reference=req.payment_reference,
        items=items_to_add,
    )

    db.add(doc)
    await db.flush()

    tok = DocumentToken(document_id=doc.id, token=secrets.token_hex(32))
    db.add(tok)

    # Automatically post double-entry GL journal entry if GL is active
    await _sync_gl(db, shop_id, doc)
    await workflow.sync_document_stock(db, doc, user_id)
    await loyalty.sync_document_loyalty(db, doc, user_id)
    if settle_on_create and grand_total > 0:
        from app.modules.accounts.posting import sync_payment_gl

        payment = DocumentPayment(
            document_id=doc.id,
            shop_id=shop_id,
            amount=grand_total,
            payment_date=doc.issue_date,
            payment_channel=(req.payment_channel or "CASH").upper(),
            payment_reference=(req.payment_reference or "").strip() or None,
            notes="Paid in full when the invoice was created",
            recorded_by_user_id=user_id,
        )
        db.add(payment)
        await db.flush()
        await sync_payment_gl(db, shop_id, payment, doc)
    await workflow.refresh_settlement(db, doc)

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
    if parent.status not in workflow.OPEN_STATUSES + ("PAID",):
        raise WorkflowError(
            f"A credit note needs an issued invoice; {parent.doc_number} is {parent.status.replace('_', ' ').lower()}."
        )
    parent_figures = await workflow.figures(db, parent)

    cn_number = await generate_doc_number(db, shop_id, "CREDIT_NOTE")
    credit_doc_id = uuid.uuid4()

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
                        source_document_id=credit_doc_id,
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
                    source_document_id=credit_doc_id,
                    created_by_id=user_id,
                )
            )

    grand_total = total_sub + total_tax
    cn_discount = Decimal("0.00")
    if not req.items and Decimal(str(parent.loyalty_discount_amount or 0)) > 0:
        # Full credit of a discounted invoice: credit what was charged, discount included.
        total_sub, total_tax, grand_total = parent.sub_total, parent.tax_amount, parent.grand_total
        cn_discount = parent.loyalty_discount_amount
    if grand_total <= 0:
        raise WorkflowError("A credit note needs a positive amount.")
    if grand_total > parent_figures.credit_capacity:
        raise WorkflowError(
            f"The credit note ({grand_total:,.2f}) is more than can still be credited on "
            f"{parent.doc_number} ({parent_figures.credit_capacity:,.2f})."
        )
    if parent_figures.balance <= 0:
        cn_status = "PAID"  # nothing owed: the credit is a refund to the client
    elif grand_total <= parent_figures.balance:
        cn_status = "ISSUED"  # reduces what the client owes
    else:
        raise WorkflowError(
            f"{parent.doc_number} still has {parent_figures.balance:,.2f} unpaid. Credit up to that amount now, "
            "or record the payment first and then credit the rest as a refund."
        )

    # Reason metadata in commercial terms
    reason_desc = {
        "01": "Return of Goods / Reversal",
        "02": "Invoicing Rate / Quantity Error",
        "03": "Post-Sale Price Adjustment / Commercial Discount",
    }.get(req.reason_code, "Commercial Credit Adjustment")

    credit_doc = Document(
        id=credit_doc_id,
        shop_id=shop_id,
        client_id=parent.client_id,
        type="CREDIT_NOTE",
        doc_number=cn_number,
        status=cn_status,
        parent_document_id=parent.id,
        requires_etims=parent.requires_etims,
        notes=f"KRA Reason [{req.reason_code}] {reason_desc}: {req.reason_notes}",
        terms_and_conditions=f'{{"reasonCode":"{req.reason_code}","parentDocNumber":"{parent.doc_number}"}}',
        currency=parent.currency or "KES",
        sub_total=total_sub,
        tax_amount=total_tax,
        grand_total=grand_total,
        loyalty_discount_amount=cn_discount,
        issue_date=utcnow(),
        items=cn_items,
    )

    db.add(credit_doc)
    await db.flush()
    db.add(DocumentToken(document_id=credit_doc.id, token=secrets.token_hex(32)))

    await _sync_gl(db, shop_id, credit_doc)
    await workflow.refresh_settlement(db, parent)

    await db.commit()
    return await get_document_by_id(db, shop_id, credit_doc.id)


async def perform_document_action(
    db: AsyncSession,
    shop_id: uuid.UUID,
    document_id: uuid.UUID,
    action: str,
    user_id: Optional[uuid.UUID] = None,
    reason: Optional[str] = None,
) -> Optional[Document]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None
    await workflow.apply_action(db, doc, action, user_id=user_id, reason=reason)
    await db.commit()
    return await get_document_by_id(db, shop_id, document_id)


async def update_document_status(
    db: AsyncSession,
    shop_id: uuid.UUID,
    document_id: uuid.UUID,
    new_status: str,
    user_id: Optional[uuid.UUID] = None,
    reason: Optional[str] = None,
) -> Optional[Document]:
    """Older endpoint: a target status is mapped to the workflow action that reaches it."""
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None
    action = workflow.action_for_status(doc, normalize_doc_status(new_status))
    if not action:
        return doc
    await workflow.apply_action(db, doc, action, user_id=user_id, reason=reason or "Status changed")
    await db.commit()
    return await get_document_by_id(db, shop_id, document_id)


async def update_document(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, req: DocumentCreateRequest
) -> Optional[Document]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None
    workflow.ensure_draft(doc, "edited")

    if req.client_id is not None:
        doc.client_id = req.client_id
    if req.supplier_id is not None:
        doc.supplier_id = req.supplier_id
    if req.type:
        doc.type = normalize_doc_type(req.type)
    if req.notes is not None:
        doc.notes = req.notes
    if req.terms_and_conditions is not None:
        doc.terms_and_conditions = req.terms_and_conditions
    if req.currency:
        doc.currency = req.currency
    if req.due_date is not None:
        doc.due_date = to_naive_utc(req.due_date)
    if req.issue_date is not None:
        doc.issue_date = to_naive_utc(req.issue_date)
    if req.location_id is not None:
        doc.location_id = req.location_id
    if req.payment_channel is not None:
        doc.payment_channel = req.payment_channel
    if req.payment_reference is not None:
        doc.payment_reference = req.payment_reference
    doc.requires_etims = req.requires_etims

    if req.items:
        new_items = []
        lines = []
        for it in req.items:
            tax, total = compute_item_tax_and_total(it.quantity, it.unit_price, it.tax_type)
            lines.append((it.quantity * it.unit_price, tax, it.tax_type))
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
        priced = await loyalty.price_document(
            db, shop_id, doc.type, doc.client_id, lines, req.loyalty_points_redeemed, req.apply_tier_discount
        )
        for old_item in (doc.items or []):
            await db.delete(old_item)
        await db.flush()
        doc.items = new_items
        doc.sub_total = priced.sub_total
        doc.tax_amount = priced.tax_amount
        doc.grand_total = priced.grand_total
        doc.loyalty_points_redeemed = priced.points_redeemed
        doc.loyalty_discount_amount = priced.discount

    await db.flush()
    await _sync_gl(db, shop_id, doc)
    await db.commit()
    return await get_document_by_id(db, shop_id, document_id)


async def delete_document(db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return False
    workflow.ensure_draft(doc, "deleted")
    from app.modules.accounts.posting import remove_document_gl

    await remove_document_gl(db, shop_id, doc.id)
    await db.delete(doc)
    await db.commit()
    return True


async def patch_document(
    db: AsyncSession,
    shop_id: uuid.UUID,
    document_id: uuid.UUID,
    req: DocumentPatchRequest,
    user_id: Optional[uuid.UUID] = None,
) -> Optional[Document]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None
    if req.terms_and_conditions is not None and req.terms_and_conditions != doc.terms_and_conditions:
        workflow.ensure_draft(doc, "edited")
    if req.due_date is not None and to_naive_utc(req.due_date) != to_naive_utc(doc.due_date):
        allowed = (
            doc.status == "DRAFT"
            or (doc.type in workflow.SETTLED_TYPES and doc.status in workflow.OPEN_STATUSES)
            or (doc.type == "QUOTATION" and doc.status in ("ISSUED", "EXPIRED"))
        )
        if not allowed:
            raise WorkflowError(f"The due date can't change once the {doc.type.lower()} is {doc.status.lower()}.")
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
        doc.due_date = to_naive_utc(req.due_date)
        if doc.type == "QUOTATION" and doc.status == "EXPIRED" and doc.due_date and doc.due_date > utcnow():
            doc.status = "ISSUED"  # validity extended
        await workflow.refresh_settlement(db, doc)
    if req.is_recurring is not None:
        doc.is_recurring = req.is_recurring
    if req.recurring_interval is not None:
        doc.recurring_interval = req.recurring_interval
    if req.next_recurring_date is not None:
        doc.next_recurring_date = req.next_recurring_date
    await db.flush()
    if req.status is not None:
        action = workflow.action_for_status(doc, normalize_doc_status(req.status))
        if action:
            await workflow.apply_action(db, doc, action, user_id=user_id, reason="Status changed")
    await _sync_gl(db, shop_id, doc)
    await db.commit()
    return await get_document_by_id(db, shop_id, document_id)


async def convert_document(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID, target_type: str
) -> dict:
    source_doc = await get_document_by_id(db, shop_id, document_id)
    if not source_doc:
        raise ValueError("Source document not found")
    target_type = normalize_doc_type(target_type)
    workflow.check_conversion(source_doc, target_type)
    if source_doc.type == "QUOTATION" and target_type == "INVOICE":
        existing = (
            await db.execute(
                select(Document.id).where(
                    Document.parent_document_id == source_doc.id,
                    Document.type == "INVOICE",
                    Document.status != "CANCELLED",
                )
            )
        ).first()
        if existing:
            raise WorkflowError("This quotation was already converted to an invoice.")

    new_doc_number = await generate_doc_number(db, shop_id, target_type)
    new_status = {"INVOICE": "ISSUED", "RECEIPT": "PAID"}.get(target_type, "DRAFT")

    new_doc = Document(
        shop_id=shop_id,
        client_id=source_doc.client_id,
        supplier_id=source_doc.supplier_id,
        type=target_type,
        doc_number=new_doc_number,
        status=new_status,
        parent_document_id=source_doc.id,
        requires_etims=source_doc.requires_etims,
        notes=source_doc.notes,
        terms_and_conditions=source_doc.terms_and_conditions,
        currency=source_doc.currency,
        sub_total=source_doc.sub_total,
        tax_amount=source_doc.tax_amount,
        grand_total=source_doc.grand_total,
        loyalty_discount_amount=source_doc.loyalty_discount_amount,
        issue_date=utcnow(),
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
        source_doc.status = "CONFIRMED"
        source_doc.client_portal_response = source_doc.client_portal_response or "ACCEPTED"

    await _sync_gl(db, shop_id, new_doc)
    await workflow.sync_document_stock(db, new_doc)
    await workflow.refresh_settlement(db, new_doc)
    await db.commit()
    return {
        "success": True,
        "new_document_id": str(new_doc.id),
        "doc_number": new_doc.doc_number,
        "serial": new_doc.doc_number,
        "newDocumentId": str(new_doc.id),
        "docNumber": new_doc.doc_number,
        "document": {
            "id": str(new_doc.id),
            "docNumber": new_doc.doc_number,
            "type": new_doc.type,
            "status": new_doc.status,
            "grandTotal": str(new_doc.grand_total),
        },
    }


async def get_document_full_detail(
    db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID
) -> Optional[dict]:
    doc = await get_document_by_id(db, shop_id, document_id)
    if not doc:
        return None
    changed = await workflow.refresh_settlement(db, doc)
    if doc.type == "QUOTATION" and doc.status == "ISSUED" and doc.due_date and to_naive_utc(doc.due_date) < utcnow():
        doc.status = "EXPIRED"
        changed = True
    if changed:
        await db.commit()
        doc = await get_document_by_id(db, shop_id, document_id)

    # Payments
    q_payments = (
        select(DocumentPayment)
        .where(and_(DocumentPayment.document_id == document_id, DocumentPayment.shop_id == shop_id))
        .options(selectinload(DocumentPayment.recorded_by))
        .order_by(desc(DocumentPayment.payment_date))
    )
    payments_res = (await db.execute(q_payments)).scalars().all()

    # Payment Methods for invoice remittance instructions
    from app.models.payment import PaymentMethod, serialize_payment_method
    q_pms = (
        select(PaymentMethod)
        .where(PaymentMethod.shop_id == shop_id)
        .order_by(desc(PaymentMethod.is_default), asc(PaymentMethod.created_at))
    )
    pms_res = (await db.execute(q_pms)).scalars().all()
    serialized_pms = [serialize_payment_method(pm) for pm in pms_res]

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
    children_res = list((await db.execute(q_children)).scalars().all())

    # Self-healing: If a quotation was accepted or confirmed by the client but child invoice is missing, generate it now
    if (
        doc.type == "QUOTATION"
        and (doc.status == "CONFIRMED" or doc.client_portal_response == "ACCEPTED")
        and not any(c.type == "INVOICE" for c in children_res)
    ):
        try:
            await convert_document(db, shop_id=shop_id, document_id=doc.id, target_type="INVOICE")
            doc.status = "CONFIRMED"
            await db.commit()
            q_children_re = (
                select(Document)
                .where(and_(Document.parent_document_id == doc.id, Document.shop_id == shop_id))
                .order_by(Document.created_at.asc())
            )
            children_res = list((await db.execute(q_children_re)).scalars().all())
        except Exception:
            pass

    child_docs = [
        {
            "id": str(c.id),
            "docNumber": c.doc_number,
            "type": c.type,
            "status": c.status,
            "grandTotal": str(c.grand_total or "0.00"),
            "issueDate": c.issue_date.isoformat() if c.issue_date else None,
        }
        for c in children_res
    ]

    portal_token = doc.token_record.token if doc.token_record else None
    flow = await workflow.available_actions(db, doc)

    return {
        "id": str(doc.id),
        "shopId": str(doc.shop_id),
        "docNumber": doc.doc_number,
        "type": doc.type,
        "status": doc.status,
        "clientPortalResponse": doc.client_portal_response or ("ACCEPTED" if doc.status == "CONFIRMED" else None),
        "client_portal_response": doc.client_portal_response or ("ACCEPTED" if doc.status == "CONFIRMED" else None),
        "clientAmendmentNotes": doc.client_amendment_notes,
        "isAccepted": doc.client_portal_response == "ACCEPTED" or doc.status in ("CONFIRMED", "ISSUED", "PAID"),
        "isDecided": (doc.client_portal_response in ("ACCEPTED", "DECLINED")) or doc.status in ("CONFIRMED", "CANCELLED"),
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
        "loyaltyPointsEarned": doc.loyalty_points_earned or 0,
        "loyaltyPointsRedeemed": doc.loyalty_points_redeemed or 0,
        "loyaltyDiscountAmount": str(doc.loyalty_discount_amount or "0.00"),
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
        "paymentMethods": serialized_pms,
        "workflow": flow,
        "amountPaid": flow["amountPaid"],
        "balanceDue": flow["balanceDue"],
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
    db: AsyncSession,
    shop_id: uuid.UUID,
    document_ids: List[uuid.UUID],
    new_status: str,
    user_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """Apply the same status to several documents; ones whose workflow doesn't allow it are skipped."""
    target = normalize_doc_status(new_status)
    updated, skipped = 0, []
    for doc_id in document_ids:
        doc = await get_document_by_id(db, shop_id, doc_id)
        if not doc:
            continue
        try:
            async with db.begin_nested():
                action = workflow.action_for_status(doc, target)
                if action:
                    await workflow.apply_action(db, doc, action, user_id=user_id, reason="Bulk status change")
                    updated += 1
        except WorkflowError as exc:
            skipped.append({"id": str(doc.id), "docNumber": doc.doc_number, "reason": str(exc)})
    await db.commit()
    return {"updated": updated, "skipped": skipped}


async def generate_recurring_cycle(db: AsyncSession, shop_id: uuid.UUID, document_id: uuid.UUID) -> dict:
    q = select(Document).where(and_(Document.id == document_id, Document.shop_id == shop_id)).options(
        selectinload(Document.items)
    )
    res = await db.execute(q)
    source_doc = res.scalar_one_or_none()
    if not source_doc:
        raise ValueError("Source recurring invoice template could not be found.")

    new_doc_number = await generate_doc_number(db, shop_id, "INVOICE")
    now = utcnow()
    due = now + timedelta(days=30)
    list_sub, list_tax, list_total = loyalty.undiscounted(
        (it.quantity * it.unit_price, it.tax_amount) for it in source_doc.items
    )

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
        # List prices: a loyalty discount on the template was a one-off.
        sub_total=list_sub,
        tax_amount=list_tax,
        grand_total=list_total,
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

    await _sync_gl(db, shop_id, new_doc)
    await workflow.sync_document_stock(db, new_doc)
    await db.commit()
    return {"success": True, "newDocId": str(new_doc.id), "doc_number": new_doc.doc_number}


