import uuid
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.bill import VendorBill, VendorBillItem
from app.models.document import Document, DocumentItem
from app.models.supplier import Supplier
from app.models.account import ChartOfAccount
from app.models.identity import Shop
from app.core.datetime_utils import to_naive_utc
from app.core.datetime_utils import utcnow

router = APIRouter(prefix="/bills", tags=["Vendor Bills & 3-Way Matching"], dependencies=[Depends(verify_internal_api_key)])


class BillItemInput(BaseModel):
    account_id: uuid.UUID
    description: str
    quantity: Decimal = Decimal("1.00")
    unit_price: Decimal
    tax_rate: Decimal = Decimal("0.00")
    po_item_id: Optional[uuid.UUID] = None
    po_unit_price: Optional[Decimal] = None
    grn_quantity: Optional[Decimal] = None


class CreateBillRequest(BaseModel):
    supplier_id: uuid.UUID
    reference: Optional[str] = None
    bill_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    wht_rate: Decimal = Decimal("0.00")
    notes: Optional[str] = None
    source_po_id: Optional[uuid.UUID] = None
    source_grn_id: Optional[uuid.UUID] = None
    variance_notes: Optional[str] = None
    allow_variance_override: bool = False
    attachment_url: Optional[str] = None
    attachment_name: Optional[str] = None
    attachment_size: Optional[int] = None
    items: List[BillItemInput]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs):
        super().__pydantic_init_subclass__(**kwargs)

    def get_bill_date(self) -> Optional[datetime]:
        return to_naive_utc(self.bill_date)

    def get_due_date(self) -> Optional[datetime]:
        return to_naive_utc(self.due_date)


class PayBillRequest(BaseModel):
    payment_amount: Decimal
    payment_channel: str
    payment_reference: str
    payment_date: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("payment_date")
    def validate_payment_date(cls, v):
        return to_naive_utc(v)


def format_bill_dict(b: VendorBill) -> Dict[str, Any]:
    return {
        "id": str(b.id),
        "billNumber": b.bill_number,
        "reference": b.reference,
        "billDate": b.bill_date.isoformat() if b.bill_date else None,
        "dueDate": b.due_date.isoformat() if b.due_date else None,
        "subTotal": str(b.sub_total),
        "taxAmount": str(b.tax_amount),
        "whtRate": str(b.wht_rate),
        "whtAmount": str(b.wht_amount),
        "totalAmount": str(b.total_amount),
        "netPayable": str(b.net_payable),
        "amountPaid": str(b.amount_paid),
        "status": b.status,
        "paymentChannel": b.payment_channel,
        "paymentReference": b.payment_reference,
        "paidAt": b.paid_at.isoformat() if b.paid_at else None,
        "attachmentUrl": b.attachment_url,
        "attachmentName": b.attachment_name,
        "attachmentSize": b.attachment_size,
        "sourcePoId": str(b.source_po_id) if b.source_po_id else None,
        "sourceGrnId": str(b.source_grn_id) if b.source_grn_id else None,
        "matchingStatus": b.matching_status or "UNMATCHED",
        "priceVarianceAmount": str(b.price_variance_amount or "0.00"),
        "quantityVarianceCount": float(b.quantity_variance_count or 0),
        "varianceNotes": b.variance_notes,
        "notes": b.notes,
        "sourcePo": {
            "id": str(b.source_po.id),
            "docNumber": b.source_po.doc_number,
            "issueDate": b.source_po.issue_date.isoformat() if b.source_po.issue_date else None,
            "grandTotal": str(b.source_po.grand_total),
        } if b.source_po else None,
        "sourceGrn": {
            "id": str(b.source_grn.id),
            "docNumber": b.source_grn.doc_number,
            "issueDate": b.source_grn.issue_date.isoformat() if b.source_grn.issue_date else None,
        } if b.source_grn else None,
        "supplier": {
            "id": str(b.supplier.id),
            "name": b.supplier.name,
            "email": b.supplier.email,
            "phone": b.supplier.phone,
            "taxPin": b.supplier.tax_pin,
        } if b.supplier else None,
        "items": [
            {
                "id": str(it.id),
                "accountId": str(it.account_id),
                "description": it.description,
                "quantity": str(it.quantity),
                "unitPrice": str(it.unit_price),
                "taxRate": str(it.tax_rate),
                "totalAmount": str(it.total_amount),
                "poItemId": str(it.po_item_id) if it.po_item_id else None,
                "poUnitPrice": str(it.po_unit_price) if it.po_unit_price is not None else None,
                "grnQuantity": str(it.grn_quantity) if it.grn_quantity is not None else None,
            }
            for it in b.items
        ],
    }


@router.get("")
async def list_bills(
    shop_id: uuid.UUID = Query(...),
    status: Optional[str] = Query(None),
    supplier_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    conditions = [VendorBill.shop_id == shop_id]
    if status and status != "ALL":
        conditions.append(VendorBill.status == status)
    if supplier_id:
        conditions.append(VendorBill.supplier_id == supplier_id)

    stmt = (
        select(VendorBill)
        .where(and_(*conditions))
        .options(
            selectinload(VendorBill.supplier),
            selectinload(VendorBill.items),
            selectinload(VendorBill.source_po),
            selectinload(VendorBill.source_grn),
        )
        .order_by(desc(VendorBill.bill_date), desc(VendorBill.created_at))
    )
    res = await db.execute(stmt)
    bills = res.scalars().all()
    return {"success": True, "bills": [format_bill_dict(b) for b in bills]}


@router.get("/approved-pos-grns")
async def get_approved_pos_and_grns(
    shop_id: uuid.UUID = Query(...),
    supplier_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    po_conds = [Document.shop_id == shop_id, Document.type.in_(["PO", "LPO", "LSO"])]
    if supplier_id:
        po_conds.append(Document.supplier_id == supplier_id)

    q_pos = (
        select(Document)
        .where(and_(*po_conds))
        .options(selectinload(Document.items), selectinload(Document.supplier))
        .order_by(desc(Document.issue_date))
    )
    pos_res = await db.execute(q_pos)
    pos = pos_res.scalars().all()

    grn_conds = [
        Document.shop_id == shop_id,
        Document.type.in_(["GOODS_RECEIVED_NOTE", "SERVICE_COMPLETION_NOTE"]),
    ]
    if supplier_id:
        grn_conds.append(Document.supplier_id == supplier_id)

    q_grns = (
        select(Document)
        .where(and_(*grn_conds))
        .options(selectinload(Document.items), selectinload(Document.supplier))
        .order_by(desc(Document.issue_date))
    )
    grns_res = await db.execute(q_grns)
    grns = grns_res.scalars().all()

    def doc_to_dict(d: Document):
        return {
            "id": str(d.id),
            "docNumber": d.doc_number,
            "type": d.type,
            "status": d.status,
            "issueDate": d.issue_date.isoformat() if d.issue_date else None,
            "grandTotal": str(d.grand_total or "0.00"),
            "supplier": {
                "id": str(d.supplier.id),
                "name": d.supplier.name,
            } if d.supplier else None,
            "items": [
                {
                    "id": str(it.id),
                    "description": it.description,
                    "quantity": str(it.quantity),
                    "unitPrice": str(it.unit_price),
                    "totalAmount": str(it.total_amount),
                }
                for it in d.items
            ],
        }

    return {
        "success": True,
        "pos": [doc_to_dict(p) for p in pos],
        "grns": [doc_to_dict(g) for g in grns],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_bill(
    req: CreateBillRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    # Verify supplier exists
    supp = await db.get(Supplier, req.supplier_id)
    if not supp or supp.shop_id != shop_id:
        raise HTTPException(status_code=400, detail="Supplier not found in workspace")

    bill_num = f"BILL-{utcnow().strftime('%y%m')}-{secrets.token_hex(3).upper()}"

    sub_total = Decimal("0.00")
    tax_total = Decimal("0.00")
    bill_items: List[VendorBillItem] = []

    price_variance_sum = Decimal("0.00")
    quantity_variance_count = Decimal("0.00")

    for item in req.items:
        qty = Decimal(str(item.quantity))
        price = Decimal(str(item.unit_price))
        rate = Decimal(str(item.tax_rate))
        line_sub = (qty * price).quantize(Decimal("0.01"))
        line_tax = (line_sub * (rate / Decimal("100.00"))).quantize(Decimal("0.01"))
        line_total = line_sub + line_tax

        sub_total += line_sub
        tax_total += line_tax

        if item.po_unit_price is not None:
            po_price = Decimal(str(item.po_unit_price))
            diff = abs(price - po_price) * qty
            price_variance_sum += diff
        if item.grn_quantity is not None:
            grn_q = Decimal(str(item.grn_quantity))
            if grn_q != qty:
                quantity_variance_count += abs(qty - grn_q)

        bi = VendorBillItem(
            account_id=item.account_id,
            description=item.description,
            quantity=qty,
            unit_price=price,
            tax_rate=rate,
            total_amount=line_total,
            po_item_id=item.po_item_id,
            po_unit_price=Decimal(str(item.po_unit_price)) if item.po_unit_price is not None else None,
            grn_quantity=Decimal(str(item.grn_quantity)) if item.grn_quantity is not None else None,
        )
        bill_items.append(bi)

    total_amount = sub_total + tax_total
    wht_rate = Decimal(str(req.wht_rate))
    wht_amount = (sub_total * (wht_rate / Decimal("100.00"))).quantize(Decimal("0.01"))
    net_payable = total_amount - wht_amount

    matching_status = "PERFECT_MATCH"
    if price_variance_sum > Decimal("0.00") and quantity_variance_count > Decimal("0.00"):
        matching_status = "PRICE_AND_QTY_VARIANCE"
    elif price_variance_sum > Decimal("0.00"):
        matching_status = "PRICE_VARIANCE"
    elif quantity_variance_count > Decimal("0.00"):
        matching_status = "QUANTITY_VARIANCE"
    elif not req.source_po_id and not req.source_grn_id:
        matching_status = "UNMATCHED"

    if req.allow_variance_override:
        matching_status = "MANUAL_OVERRIDE"

    bill = VendorBill(
        shop_id=shop_id,
        supplier_id=req.supplier_id,
        bill_number=bill_num,
        reference=req.reference,
        bill_date=req.get_bill_date() or utcnow(),
        due_date=req.get_due_date(),
        sub_total=sub_total,
        tax_amount=tax_total,
        wht_rate=wht_rate,
        wht_amount=wht_amount,
        total_amount=total_amount,
        net_payable=net_payable,
        amount_paid=Decimal("0.00"),
        status="DRAFT",
        attachment_url=req.attachment_url,
        attachment_name=req.attachment_name,
        attachment_size=req.attachment_size,
        source_po_id=req.source_po_id,
        source_grn_id=req.source_grn_id,
        matching_status=matching_status,
        price_variance_amount=price_variance_sum,
        quantity_variance_count=quantity_variance_count,
        variance_notes=req.variance_notes,
        notes=req.notes,
        items=bill_items,
    )
    db.add(bill)
    await db.commit()
    await db.refresh(bill)

    return {"success": True, "bill": format_bill_dict(bill)}


@router.post("/{bill_id}/pay")
async def pay_bill(
    bill_id: uuid.UUID,
    req: PayBillRequest,
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    bill = await db.get(VendorBill, bill_id)
    if not bill or bill.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Vendor bill not found")

    new_paid = bill.amount_paid + req.payment_amount
    bill.amount_paid = new_paid
    bill.payment_channel = req.payment_channel
    bill.payment_reference = req.payment_reference
    bill.paid_at = to_naive_utc(req.payment_date) or utcnow()

    if new_paid >= bill.net_payable:
        bill.status = "PAID"
    elif new_paid > Decimal("0.00"):
        bill.status = "PARTIALLY_PAID"

    if req.notes:
        bill.notes = f"{bill.notes or ''}\n{req.notes}".strip()

    await db.commit()
    await db.refresh(bill)
    return {"success": True, "bill": format_bill_dict(bill)}


@router.delete("/{bill_id}")
async def delete_bill(
    bill_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    shop_slug: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    bill = await db.get(VendorBill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Vendor bill not found")
    
    if bill.status == "PAID":
        raise HTTPException(status_code=400, detail="Cannot delete a settled bill.")
    
    await db.delete(bill)
    await db.commit()
    return {"success": True, "message": "Bill deleted successfully"}


@router.post("/{bill_id}/approve")
async def approve_bill(
    bill_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    shop_slug: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    bill = await db.get(VendorBill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Vendor bill not found")
    bill.status = "APPROVED"
    await db.commit()
    await db.refresh(bill)
    return {"success": True, "bill": format_bill_dict(bill)}


@router.post("/{bill_id}/cancel")
async def cancel_bill(
    bill_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    shop_slug: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    bill = await db.get(VendorBill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Vendor bill not found")
    bill.status = "CANCELLED"
    await db.commit()
    await db.refresh(bill)
    return {"success": True, "bill": format_bill_dict(bill)}


@router.patch("/{bill_id}/attachment")
async def update_bill_attachment(
    bill_id: uuid.UUID,
    req: dict,
    shop_id: Optional[uuid.UUID] = Query(None),
    shop_slug: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    bill = await db.get(VendorBill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Vendor bill not found")
    
    if "attachment_url" in req or "attachmentUrl" in req:
        bill.attachment_url = req.get("attachment_url") or req.get("attachmentUrl")
    if "attachment_name" in req or "attachmentName" in req:
        bill.attachment_name = req.get("attachment_name") or req.get("attachmentName")
    if "attachment_size" in req or "attachmentSize" in req:
        bill.attachment_size = req.get("attachment_size") or req.get("attachmentSize")
        
    await db.commit()
    await db.refresh(bill)
    return {"success": True, "bill": format_bill_dict(bill)}

