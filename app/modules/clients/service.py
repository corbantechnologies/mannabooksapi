import uuid
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc
from app.models.client import Client
from app.modules.clients.schemas import ClientCreateRequest, ClientUpdateRequest


async def list_clients(
    db: AsyncSession,
    shop_id: uuid.UUID,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Client]:
    q = select(Client).where(Client.shop_id == shop_id)
    if search:
        s = f"%{search.strip()}%"
        q = q.where(or_(Client.name.ilike(s), Client.email.ilike(s), Client.tax_pin.ilike(s)))
    q = q.order_by(desc(Client.created_at)).offset(offset).limit(limit)
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_client_by_id(db: AsyncSession, shop_id: uuid.UUID, client_id: uuid.UUID) -> Optional[Client]:
    q = select(Client).where(and_(Client.id == client_id, Client.shop_id == shop_id))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def create_client(db: AsyncSession, shop_id: uuid.UUID, req: ClientCreateRequest) -> Client:
    client = Client(
        shop_id=shop_id,
        name=req.name.strip(),
        email=req.email.lower().strip() if req.email else "",
        phone=req.phone.strip() if req.phone else None,
        client_type=req.client_type,
        tax_pin=req.tax_pin.upper().strip() if req.tax_pin else None,
        requires_etims=req.requires_etims or bool(req.tax_pin),
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


async def update_client(
    db: AsyncSession, shop_id: uuid.UUID, client_id: uuid.UUID, req: ClientUpdateRequest
) -> Optional[Client]:
    client = await get_client_by_id(db, shop_id, client_id)
    if not client:
        return None

    if req.name is not None:
        client.name = req.name.strip()
    if req.email is not None:
        client.email = req.email.lower().strip() if req.email else ""
    if req.phone is not None:
        client.phone = req.phone.strip() if req.phone else None
    if req.client_type is not None:
        client.client_type = req.client_type
    if req.tax_pin is not None:
        client.tax_pin = req.tax_pin.upper().strip() if req.tax_pin else None
    if req.requires_etims is not None:
        client.requires_etims = req.requires_etims

    await db.commit()
    await db.refresh(client)
    return client


async def get_client_statement(
    db: AsyncSession,
    shop_id: uuid.UUID,
    client_id: uuid.UUID,
    start_date=None,
    end_date=None,
) -> dict:
    """
    Customer statement of account (what the customer owes):
      invoices and debit notes          -> debit
      credit notes and payments         -> credit
    Invoices issued already PAID without recorded payments get a matching settlement line.
    Cash-sale receipts are not on account and are left out.
    """
    from datetime import datetime as _dt
    from decimal import Decimal as _D
    from sqlalchemy import func as _func
    from app.core.datetime_utils import utcnow
    from app.models.document import Document
    from app.models.identity import Shop
    from app.models.payment import DocumentPayment

    client = await db.get(Client, client_id)
    if not client or client.shop_id != shop_id:
        return {"success": False, "error": "Client not found"}
    shop = await db.get(Shop, shop_id)

    end = end_date or utcnow()
    start = start_date or _dt(end.year, 1, 1)
    if end.hour == 0 and end.minute == 0:
        end = end.replace(hour=23, minute=59, second=59)

    docs = (
        await db.execute(
            select(Document).where(
                Document.shop_id == shop_id,
                Document.client_id == client_id,
                Document.type.in_(["INVOICE", "CREDIT_NOTE", "DEBIT_NOTE"]),
                Document.status.not_in(["DRAFT", "CANCELLED"]),
                Document.issue_date <= end,
            )
        )
    ).scalars().all()
    doc_ids = [d.id for d in docs]
    payments = (
        await db.execute(
            select(DocumentPayment).where(DocumentPayment.document_id.in_(doc_ids), DocumentPayment.payment_date <= end)
        )
    ).scalars().all() if doc_ids else []
    any_payment = set(
        (await db.execute(select(DocumentPayment.document_id).where(DocumentPayment.document_id.in_(doc_ids)).distinct())).scalars().all()
    ) if doc_ids else set()

    events = []  # (date, sort, line)
    for d in docs:
        amt = _D(str(d.grand_total or 0))
        label = d.doc_number
        if d.type in ("INVOICE", "DEBIT_NOTE"):
            events.append((d.issue_date, 0, {"docId": str(d.id), "docType": d.type, "reference": label,
                                              "description": d.notes or f"{d.type.replace('_', ' ').title()} {label}",
                                              "debit": amt, "credit": _D("0"), "status": d.status}))
            if d.type == "INVOICE" and d.status == "PAID" and d.id not in any_payment:
                credited = sum((_D(str(c.grand_total or 0)) for c in docs
                                if c.type == "CREDIT_NOTE" and c.parent_document_id == d.id and c.status == "ISSUED"), _D("0"))
                if amt - credited > 0:  # older invoices marked paid without a payment record
                    events.append((d.issue_date, 1, {"docId": str(d.id), "docType": "PAYMENT", "reference": label,
                                                      "description": f"Settled at issue: {label}",
                                                      "debit": _D("0"), "credit": amt - credited, "status": "PAID"}))
        else:  # CREDIT_NOTE
            events.append((d.issue_date, 0, {"docId": str(d.id), "docType": d.type, "reference": label,
                                              "description": d.notes or f"Credit note {label}",
                                              "debit": _D("0"), "credit": amt, "status": d.status}))
            if d.status == "PAID":  # refunded in cash: the credit was paid out, so nothing is left on account
                events.append((d.issue_date, 1, {"docId": str(d.id), "docType": "REFUND", "reference": label,
                                                  "description": f"Refund paid for {label}",
                                                  "debit": amt, "credit": _D("0"), "status": "PAID"}))
    by_id = {d.id: d for d in docs}
    for p in payments:
        doc = by_id.get(p.document_id)
        events.append((p.payment_date, 1, {"docId": str(p.document_id), "docType": "PAYMENT",
                                            "reference": p.payment_reference or (doc.doc_number if doc else ""),
                                            "description": f"Payment ({p.payment_channel}) for {doc.doc_number if doc else ''}".strip(),
                                            "debit": _D("0"), "credit": _D(str(p.amount)), "status": "RECEIVED"}))
    events.sort(key=lambda e: (e[0], e[1]))

    opening = _D("0")
    lines, total_d, total_c = [], _D("0"), _D("0")
    for when, _, line in events:
        if when < start:
            opening += line["debit"] - line["credit"]
    running = opening
    for when, _, line in events:
        if when < start:
            continue
        running += line["debit"] - line["credit"]
        total_d += line["debit"]
        total_c += line["credit"]
        lines.append({**line, "date": when.isoformat(), "debit": float(line["debit"]), "credit": float(line["credit"]),
                      "runningBalance": float(running)})

    return {
        "success": True,
        "data": {
            "clientName": client.name,
            "clientEmail": client.email or "",
            "clientPhone": client.phone or "",
            "taxPin": client.tax_pin or "",
            "currency": (shop.currency if shop else "KES") or "KES",
            "shopName": shop.name if shop else "",
            "periodLabel": f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
            "openingBalance": float(opening),
            "lines": lines,
            "totalDebits": float(total_d),
            "totalCredits": float(total_c),
            "closingBalance": float(running),
        },
    }
