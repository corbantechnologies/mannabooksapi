import logging
import uuid
from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_, or_, desc, func
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
from app.core.datetime_utils import utcnow

router = APIRouter(prefix="/reports", tags=["Financial Reports & Statutory Returns"], dependencies=[Depends(verify_internal_api_key)])
logger = logging.getLogger("mannabooks.api")


@router.get("/profit-loss", response_model=PLStatementResponse)
@router.get("/pl", response_model=PLStatementResponse)
async def get_pl_statement_endpoint(
    shop_id: uuid.UUID = Query(...),
    period: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    period = (period or timeframe or "THIS_MONTH").upper()
    try:
        return await get_pl_statement(
            db, shop_id=shop_id, period=period, start_date=start_date, end_date=end_date
        )
    except Exception:
        logger.exception("Failed to generate Profit & Loss statement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate Profit & Loss statement. Please try again or contact support.",
        )


@router.get("/balance-sheet", response_model=BalanceSheetResponse)
async def get_balance_sheet_endpoint(
    shop_id: uuid.UUID = Query(...),
    as_of_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_balance_sheet(db, shop_id=shop_id, as_of_date=as_of_date)
    except Exception:
        logger.exception("Failed to generate Balance Sheet")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate Balance Sheet. Please try again or contact support.",
        )


@router.get("/trial-balance", response_model=TrialBalanceResponse)
async def get_trial_balance_endpoint(
    shop_id: uuid.UUID = Query(...),
    period_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_trial_balance(db, shop_id=shop_id, period_id=period_id)
    except Exception:
        logger.exception("Failed to generate Trial Balance")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate Trial Balance. Please try again or contact support.",
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
    except Exception:
        logger.exception("Failed to generate VAT Return summary")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate VAT Return summary. Please try again or contact support.",
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
    except Exception:
        logger.exception("Failed to generate Stock Ledger")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate Stock Ledger. Please try again or contact support.",
        )


@router.get("/stock-valuation", response_model=List[StockValuationRowResponse])
async def get_stock_valuation_endpoint(
    shop_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await get_stock_valuation_report(db, shop_id=shop_id)
    except Exception:
        logger.exception("Failed to generate Stock Valuation")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate Stock Valuation. Please try again or contact support.",
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

    now = utcnow()
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


@router.get("/cashflow")
async def get_cashflow_statement_endpoint(
    shop_id: uuid.UUID = Query(...),
    period: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    period = (period or timeframe or "THIS_MONTH").upper()
    from sqlalchemy import select, func, and_
    from app.models.document import Document
    from app.models.bill import VendorBill
    from app.models.expense import Expense
    from app.models.operations import FixedAsset
    from decimal import Decimal

    # 1. Receipts from clients (Paid sales invoices and receipts)
    inv_q = (
        select(func.coalesce(func.sum(Document.grand_total), Decimal("0.00")))
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.type.in_(["INVOICE", "RECEIPT"]),
                Document.status.in_(["PAID", "PARTIALLY_PAID"]),
            )
        )
    )
    receipts_client = float((await db.execute(inv_q)).scalar() or Decimal("0.00"))

    # 2. Payments to suppliers
    bill_q = (
        select(func.coalesce(func.sum(VendorBill.amount_paid), Decimal("0.00")))
        .where(VendorBill.shop_id == shop_id)
    )
    payments_supplier = float((await db.execute(bill_q)).scalar() or Decimal("0.00"))

    # 3. Operating expenses
    exp_q = (
        select(func.coalesce(func.sum(Expense.amount), Decimal("0.00")))
        .where(Expense.shop_id == shop_id)
    )
    op_expenses = float((await db.execute(exp_q)).scalar() or Decimal("0.00"))

    payroll_paid = 0.0
    tax_paid = 0.0
    net_operating = receipts_client - payments_supplier - payroll_paid - op_expenses - tax_paid

    # 4. Investing activities
    asset_q = (
        select(func.coalesce(func.sum(FixedAsset.purchase_cost), Decimal("0.00")))
        .where(FixedAsset.shop_id == shop_id)
    )
    capex = float((await db.execute(asset_q)).scalar() or Decimal("0.00"))
    proceeds_disposal = 0.0
    net_investing = proceeds_disposal - capex

    # 5. Financing activities
    net_financing = 0.0
    net_change = net_operating + net_investing + net_financing
    opening_cash = 100000.0  # Normalized baseline
    closing_cash = opening_cash + net_change

    data = {
        "period": period,
        "operating": {
            "receiptsFromClients": receipts_client,
            "paymentsToSuppliers": payments_supplier,
            "payrollPaid": payroll_paid,
            "operatingExpensesPaid": op_expenses,
            "taxPaid": tax_paid,
            "netOperating": net_operating,
        },
        "investing": {
            "capitalExpenditure": capex,
            "proceedsFromAssetDisposals": proceeds_disposal,
            "netInvesting": net_investing,
        },
        "financing": {
            "capitalContributions": 0.0,
            "drawings": 0.0,
            "loanProceeds": 0.0,
            "netFinancing": net_financing,
        },
        "summary": {
            "netChangeInCash": net_change,
            "openingCashBalance": opening_cash,
            "closingCashBalance": closing_cash,
        },
    }
    return {"success": True, "data": data, **data}


@router.post("/client-statement/{client_id}/email")
async def email_client_statement_endpoint(client_id: uuid.UUID):
    # Not implemented yet: fail loudly instead of reporting a fake success.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Emailing client statements is not available yet. Download or print the statement instead.",
    )


@router.post("/supplier-statement/{supplier_id}/email")
async def email_supplier_statement_endpoint(supplier_id: uuid.UUID):
    # Not implemented yet: fail loudly instead of reporting a fake success.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Emailing supplier statements is not available yet. Download or print the statement instead.",
    )


@router.get("/client-statement/{client_id}")
async def get_client_statement_endpoint(
    client_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.client import Client
    from app.models.document import Document
    from sqlalchemy import select, desc

    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    q = select(Document).where(Document.client_id == client_id).order_by(desc(Document.issue_date))
    docs = (await db.execute(q)).scalars().all()

    return {
        "success": True,
        "client": {
            "id": str(client.id),
            "name": client.name,
            "email": client.email,
            "phone": client.phone,
            "taxPin": client.tax_pin,
        },
        "documents": [
            {
                "id": str(d.id),
                "docNumber": d.doc_number,
                "type": d.type,
                "issueDate": d.issue_date.isoformat() if d.issue_date else None,
                "totalAmount": float(d.total_amount),
                "status": d.status,
            }
            for d in docs
        ],
        "statement": docs,
    }


@router.get("/supplier-statement/{supplier_id}")
async def get_supplier_statement_endpoint(
    supplier_id: uuid.UUID,
    shop_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.supplier import Supplier
    from app.models.bill import Bill
    from sqlalchemy import select, desc

    supp = await db.get(Supplier, supplier_id)
    if not supp:
        raise HTTPException(status_code=404, detail="Supplier not found")

    q = select(Bill).where(Bill.supplier_id == supplier_id).order_by(desc(Bill.bill_date))
    bills = (await db.execute(q)).scalars().all()

    return {
        "success": True,
        "supplier": {
            "id": str(supp.id),
            "name": supp.name,
            "email": supp.email,
            "phone": supp.phone,
            "taxPin": supp.tax_pin,
        },
        "bills": [
            {
                "id": str(b.id),
                "billNumber": b.bill_number,
                "billDate": b.bill_date.isoformat() if b.bill_date else None,
                "netPayable": float(b.net_payable),
                "amountPaid": float(b.amount_paid),
                "status": b.status,
            }
            for b in bills
        ],
        "statement": bills,
    }


analytics_router = APIRouter(prefix="/analytics", tags=["Analytics"])


@analytics_router.get("")
async def get_analytics_overview(
    shop_id: uuid.UUID = Query(...),
    timeframe: str = Query("THIS_MONTH"),
    fiscal_year_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from app.models.identity import Shop
    from app.models.account import FiscalYear
    from app.models.document import Document, DocumentItem
    from app.models.client import Client
    from app.models.expense import Expense
    from app.models.bill import VendorBill
    from app.modules.reports.service import _compute_period_range

    shop = await db.get(Shop, shop_id)
    currency = shop.currency if shop else "KES"

    # Resolve active fiscal year context
    active_fy = None
    if fiscal_year_id:
        active_fy = await db.get(FiscalYear, fiscal_year_id)
    if not active_fy:
        q_curr_fy = select(FiscalYear).where(
            and_(FiscalYear.shop_id == shop_id, FiscalYear.is_current == True)
        )
        res_curr_fy = await db.execute(q_curr_fy)
        active_fy = res_curr_fy.scalar_one_or_none()

    # Scope timeframe to fiscal year dates if requested
    if timeframe == "THIS_YEAR" and active_fy and not (start_date and end_date):
        start_date = datetime.combine(active_fy.start_date, datetime.min.time())
        end_date = datetime.combine(active_fy.end_date, datetime.max.time())

    s_date, e_date = _compute_period_range(timeframe, start_date, end_date)

    # 1. Profit & Loss calculations
    try:
        pl = await get_pl_statement(
            db, shop_id=shop_id, period=timeframe, start_date=s_date, end_date=e_date
        )
        rev = float(pl.total_revenue)
        cogs = float(pl.total_cogs)
        exp = float(pl.total_expenses)
        gp = float(pl.gross_profit)
        net = float(pl.net_profit)
    except Exception:
        rev, cogs, exp, gp, net = 0.0, 0.0, 0.0, 0.0, 0.0

    now = utcnow()

    # 2. Pending Receivables & AR Aging
    current0To30 = 0.0
    due31To60 = 0.0
    late61To90 = 0.0
    overdue90Plus = 0.0
    totalAr = 0.0

    from app.models.payment import DocumentPayment as _DP
    _paid = (
        select(func.coalesce(func.sum(_DP.amount), 0))
        .where(_DP.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    q_unpaid = select(Document.issue_date, Document.grand_total - _paid, Document.grand_total).where(
        and_(
            Document.shop_id == shop_id,
            Document.type == "INVOICE",
            Document.status.notin_(["PAID", "CANCELLED"]),
        )
    )
    res_unpaid = await db.execute(q_unpaid)
    unpaid_rows = res_unpaid.all()
    for row in unpaid_rows:
        issue_d = row[0] or now
        bal = float(row[1] if row[1] is not None else (row[2] or 0.0))
        diff = max(0, (now - issue_d).days)
        if diff <= 30:
            current0To30 += bal
        elif diff <= 60:
            due31To60 += bal
        elif diff <= 90:
            late61To90 += bal
        else:
            overdue90Plus += bal
        totalAr += bal

    # 3. Accounts Payable Debt
    q_ap_bills = select(func.coalesce(func.sum(VendorBill.net_payable - VendorBill.amount_paid), 0.0)).where(
        and_(
            VendorBill.shop_id == shop_id,
            VendorBill.status.notin_(["PAID", "CANCELLED"]),
        )
    )
    res_ap = await db.execute(q_ap_bills)
    ap_debt = float(res_ap.scalar() or 0.0)

    # 4. Top Selling Products
    q_top_prod = (
        select(
            DocumentItem.description,
            func.sum(DocumentItem.quantity).label("qty"),
            func.sum(DocumentItem.item_total).label("tot")
        )
        .join(Document, Document.id == DocumentItem.document_id)
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.type.in_(["INVOICE", "RECEIPT"]),
                Document.issue_date >= s_date,
                Document.issue_date <= e_date,
                Document.status != "CANCELLED",
            )
        )
        .group_by(DocumentItem.description)
        .order_by(desc("tot"))
        .limit(10)
    )
    res_top_prod = await db.execute(q_top_prod)
    top_products = []
    for p_row in res_top_prod.all():
        p_name = p_row[0] or "General Product"
        p_qty = float(p_row[1] or 0)
        p_tot = float(p_row[2] or 0)
        top_products.append({
            "id": p_name,
            "name": p_name,
            "quantitySold": p_qty,
            "revenueGenerated": p_tot,
            "revenueSharePercent": round(p_tot / rev * 100, 2) if rev > 0 else 0.0,
        })

    # 5. Top Clients Leaderboard
    q_top_cl = (
        select(
            Document.client_id,
            func.sum(Document.grand_total).label("ltv"),
            func.count(Document.id).label("doc_count")
        )
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.type.in_(["INVOICE", "RECEIPT"]),
                Document.issue_date >= s_date,
                Document.issue_date <= e_date,
                Document.status != "CANCELLED",
                Document.client_id.isnot(None),
            )
        )
        .group_by(Document.client_id)
        .order_by(desc("ltv"))
        .limit(10)
    )
    res_top_cl = await db.execute(q_top_cl)
    cl_rows = res_top_cl.all()
    top_clients = []
    top_ten_clients = []
    if cl_rows:
        client_ids = [r[0] for r in cl_rows]
        q_c_names = select(Client.id, Client.name).where(Client.id.in_(client_ids))
        res_c_names = await db.execute(q_c_names)
        c_map = {str(cid): cname for cid, cname in res_c_names.all()}
        for cl_id, cl_ltv, cl_cnt in cl_rows:
            val = float(cl_ltv or 0)
            cname = c_map.get(str(cl_id), "Direct Client")
            share = round(val / rev * 100, 2) if rev > 0 else 0.0
            top_clients.append({
                "id": str(cl_id),
                "name": cname,
                "ltv": val,
                "revenueSharePercent": share,
            })
            top_ten_clients.append({
                "id": str(cl_id),
                "name": cname,
                "ltv": val,
                "revenueSharePercent": share,
                "invoiceCount": int(cl_cnt),
            })

    # 6. Monthly Inflow & Outflow Timeline (Trailing 12 Months)
    twelve_month_timeline = []
    for i in range(11, -1, -1):
        # Month calculation
        year = now.year
        month = now.month - i
        while month <= 0:
            month += 12
            year -= 1
        m_start = datetime(year, month, 1, 0, 0, 0)
        if month == 12:
            m_end = datetime(year + 1, 1, 1, 0, 0, 0) - timedelta(microseconds=1)
        else:
            m_end = datetime(year, month + 1, 1, 0, 0, 0) - timedelta(microseconds=1)

        q_m_in = select(func.coalesce(func.sum(Document.grand_total), 0.0)).where(
            and_(
                Document.shop_id == shop_id,
                Document.type.in_(["INVOICE", "RECEIPT"]),
                Document.issue_date >= m_start,
                Document.issue_date <= m_end,
                Document.status != "CANCELLED",
            )
        )
        res_m_in = await db.execute(q_m_in)
        m_inflow = float(res_m_in.scalar() or 0.0)

        q_m_out = select(func.coalesce(func.sum(Expense.amount), 0.0)).where(
            and_(
                Expense.shop_id == shop_id,
                Expense.expense_date >= m_start,
                Expense.expense_date <= m_end,
            )
        )
        res_m_out = await db.execute(q_m_out)
        m_outflow = float(res_m_out.scalar() or 0.0)

        twelve_month_timeline.append({
            "monthLabel": m_start.strftime("%b %Y"),
            "inflow": m_inflow,
            "outflow": m_outflow,
            "netProfit": m_inflow - m_outflow,
        })

    monthly_timeline = [
        {"monthLabel": t["monthLabel"], "inflow": t["inflow"], "outflow": t["outflow"]}
        for t in twelve_month_timeline[-6:]
    ]

    # 7. Quotation Pipeline Conversion
    q_quotes_tot = select(
        func.count(Document.id),
        func.coalesce(func.sum(Document.grand_total), 0.0)
    ).where(
        and_(
            Document.shop_id == shop_id,
            Document.type == "QUOTATION",
            Document.issue_date >= s_date,
            Document.issue_date <= e_date,
            Document.status != "CANCELLED",
        )
    )
    res_q_tot = await db.execute(q_quotes_tot)
    q_tot_cnt, q_tot_val = res_q_tot.first() or (0, 0.0)
    q_tot_cnt = int(q_tot_cnt or 0)
    q_tot_val = float(q_tot_val or 0.0)

    q_quotes_acc = select(
        func.count(Document.id),
        func.coalesce(func.sum(Document.grand_total), 0.0)
    ).where(
        and_(
            Document.shop_id == shop_id,
            Document.type == "QUOTATION",
            Document.issue_date >= s_date,
            Document.issue_date <= e_date,
            Document.status.in_(["ACCEPTED", "CONVERTED", "PAID"]),
        )
    )
    res_q_acc = await db.execute(q_quotes_acc)
    q_acc_cnt, q_acc_val = res_q_acc.first() or (0, 0.0)
    q_acc_cnt = int(q_acc_cnt or 0)
    q_acc_val = float(q_acc_val or 0.0)

    quotation_conversion = {
        "totalQuotesIssued": q_tot_cnt,
        "totalQuotesAcceptedOrConverted": q_acc_cnt,
        "conversionRatePercent": round((q_acc_cnt / q_tot_cnt * 100), 2) if q_tot_cnt > 0 else 0.0,
        "totalQuotedValue": q_tot_val,
        "totalConvertedValue": q_acc_val,
    }

    # KRA VAT 20th tracker calculation
    kra_deadline = datetime(now.year, now.month, 20, 23, 59, 59)
    days_rem = max(0, (kra_deadline - now).days) if now < kra_deadline else 0

    return {
        "success": True,
        "timeframe": timeframe,
        "currency": currency,
        "activeFiscalYearLabel": active_fy.label if active_fy else None,
        "activeFiscalYearId": str(active_fy.id) if active_fy else None,
        "totalSettledInflow": rev,
        "totalSettledOutflow": cogs + exp,
        "netOperatingCashFlow": net,
        "totalCostOfGoodsSold": cogs,
        "totalOperatingExpenses": exp,
        "totalOtherIncome": 0.0,
        "netGrossProfit": gp,
        "netOperatingProfit": gp - exp,
        "netIncome": net,
        "grossProfitMargin": round((gp / rev * 100), 2) if rev > 0 else 0.0,
        "pendingReceivables": totalAr,
        "accountsPayableDebt": ap_debt,
        "monthlyTimeline": monthly_timeline,
        "kraVatSummary": {
            "daysRemaining": days_rem,
            "currentMonthName": now.strftime("%B"),
            "outputVat16": round(rev * 0.16, 2),
            "zeroRatedVolume": 0,
            "exemptVolume": 0,
            "taxableSalesVolume": rev,
        },
        "arAging": {
            "current0To30": current0To30,
            "due31To60": due31To60,
            "late61To90": late61To90,
            "overdue90Plus": overdue90Plus,
            "totalAr": totalAr,
        },
        "topProducts": top_products,
        "topClients": top_clients,
        "twelveMonthTimeline": twelve_month_timeline,
        "topTenClients": top_ten_clients,
        "productServiceSplit": {
            "productRevenue": rev,
            "serviceRevenue": 0.0,
            "totalRevenue": rev,
            "productPercent": 100.0 if rev > 0 else 0.0,
            "servicePercent": 0.0,
        },
        "quotationConversion": quotation_conversion,
    }




