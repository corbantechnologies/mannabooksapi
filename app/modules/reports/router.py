import uuid
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.modules.reports.schemas import (
    PLStatementResponse,
    BalanceSheetResponse,
    TrialBalanceResponse,
    VatReturnSummaryResponse,
    StockLedgerEntryResponse,
    StockValuationRowResponse,
)
from app.modules.reports.service import (
    get_pl_statement,
    get_balance_sheet,
    get_trial_balance,
    get_vat_return_summary,
    get_stock_ledger_report,
    get_stock_valuation_report,
)

router = APIRouter(prefix="/reports", tags=["Financial Reports & Statutory Returns"], dependencies=[Depends(verify_internal_api_key)])


@router.get("/profit-loss", response_model=PLStatementResponse)
async def get_pl_statement_endpoint(
    shop_id: uuid.UUID = Query(...),
    period: str = Query("THIS_MONTH"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_pl_statement(
            db, shop_id=shop_id, period=period, start_date=start_date, end_date=end_date
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Profit & Loss statement: {str(e)}",
        )


@router.get("/balance-sheet", response_model=BalanceSheetResponse)
async def get_balance_sheet_endpoint(
    shop_id: uuid.UUID = Query(...),
    as_of_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_balance_sheet(db, shop_id=shop_id, as_of_date=as_of_date)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Balance Sheet: {str(e)}",
        )


@router.get("/trial-balance", response_model=TrialBalanceResponse)
async def get_trial_balance_endpoint(
    shop_id: uuid.UUID = Query(...),
    period_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_trial_balance(db, shop_id=shop_id, period_id=period_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Trial Balance: {str(e)}",
        )


@router.get("/vat-return", response_model=VatReturnSummaryResponse)
async def get_vat_return_endpoint(
    shop_id: uuid.UUID = Query(...),
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_vat_return_summary(
            db, shop_id=shop_id, start_date=start_date, end_date=end_date
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate VAT Return summary: {str(e)}",
        )


@router.get("/stock-ledger", response_model=List[StockLedgerEntryResponse])
async def get_stock_ledger_endpoint(
    shop_id: uuid.UUID = Query(...),
    product_id: Optional[uuid.UUID] = Query(None),
    location_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_stock_ledger_report(
            db,
            shop_id=shop_id,
            product_id=product_id,
            location_id=location_id,
            limit=limit,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Stock Ledger: {str(e)}",
        )


@router.get("/stock-valuation", response_model=List[StockValuationRowResponse])
async def get_stock_valuation_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_stock_valuation_report(db, shop_id=shop_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Stock Valuation: {str(e)}",
        )


@router.get("/payables-aging")
async def get_payables_aging_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, and_
    from sqlalchemy.orm import selectinload
    from app.models.supplier import Supplier
    from app.models.document import Document
    from app.models.bill import VendorBill

    stmt = select(Supplier).where(Supplier.shop_id == shop_id).order_by(Supplier.name)
    res = await db.execute(stmt)
    suppliers = res.scalars().all()

    now = datetime.utcnow()
    aging_data = []

    for supp in suppliers:
        # Fetch unpaid documents
        q_docs = select(Document).where(
            and_(
                Document.shop_id == shop_id,
                Document.supplier_id == supp.id,
                Document.status.notin_(["PAID", "CANCELLED"]),
            )
        )
        res_docs = await db.execute(q_docs)
        docs = res_docs.scalars().all()

        # Fetch unpaid vendor bills
        q_bills = select(VendorBill).where(
            and_(
                VendorBill.shop_id == shop_id,
                VendorBill.supplier_id == supp.id,
                VendorBill.status.notin_(["PAID", "CANCELLED"]),
            )
        )
        res_bills = await db.execute(q_bills)
        bills = res_bills.scalars().all()

        current = Decimal("0.00")
        days31to60 = Decimal("0.00")
        days61to90 = Decimal("0.00")
        days90Plus = Decimal("0.00")
        total = Decimal("0.00")

        doc_items = []
        for doc in docs:
            issue_time = doc.issue_date or now
            diff_days = max(0, (now - issue_time).days)
            amt = doc.grand_total or Decimal("0.00")

            if diff_days <= 30:
                current += amt
            elif diff_days <= 60:
                days31to60 += amt
            elif diff_days <= 90:
                days61to90 += amt
            else:
                days90Plus += amt
            total += amt

            doc_items.append({
                "id": str(doc.id),
                "docNumber": doc.doc_number,
                "type": doc.type,
                "issueDate": doc.issue_date.isoformat() if doc.issue_date else "",
                "dueDate": doc.due_date.isoformat() if doc.due_date else None,
                "grandTotal": str(doc.grand_total or "0.00"),
                "status": doc.status,
                "daysOverdue": diff_days,
            })

        for b in bills:
            b_time = b.bill_date or now
            diff_days = max(0, (now - b_time).days)
            balance = max(Decimal("0.00"), b.net_payable - b.amount_paid)

            if diff_days <= 30:
                current += balance
            elif diff_days <= 60:
                days31to60 += balance
            elif diff_days <= 90:
                days61to90 += balance
            else:
                days90Plus += balance
            total += balance

            doc_items.append({
                "id": str(b.id),
                "docNumber": b.bill_number,
                "type": "VENDOR_BILL",
                "issueDate": b.bill_date.isoformat() if b.bill_date else "",
                "dueDate": b.due_date.isoformat() if b.due_date else None,
                "grandTotal": str(b.net_payable),
                "status": b.status,
                "daysOverdue": diff_days,
            })

        aging_data.append({
            "supplierId": str(supp.id),
            "supplierName": supp.name,
            "email": supp.email,
            "phone": supp.phone,
            "taxPin": supp.tax_pin,
            "paymentTerms": supp.payment_terms,
            "currentAmount": float(current),
            "days31to60": float(days31to60),
            "days61to90": float(days61to90),
            "days90Plus": float(days90Plus),
            "totalPayable": float(total),
            "documentsCount": len(doc_items),
            "documents": doc_items,
        })

    return {"success": True, "agingData": aging_data}

