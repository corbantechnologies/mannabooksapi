import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from sqlalchemy.orm import selectinload

from app.models.identity import Shop
from app.models.item import Product
from app.models.document import Document, DocumentItem
from app.models.payment import DocumentPayment
from app.models.expense import Expense, Income
from app.models.journal import JournalEntry
from app.models.account import ChartOfAccount
from app.models.inventory import StockLedger, StockLocation
from app.models.supplier import Supplier
from app.modules.reports.schemas import (
    PLLineItem,
    PLStatementResponse,
    BalanceSheetResponse,
    TrialBalanceRow,
    TrialBalanceResponse,
    VatReturnInputTaxEntry,
    VatReturnSummaryResponse,
    StockLedgerEntryResponse,
    StockValuationRowResponse,
)


from app.core.datetime_utils import to_naive_utc


def _compute_period_range(period: str, start_date: Optional[datetime], end_date: Optional[datetime]) -> tuple[datetime, datetime]:
    start_date = to_naive_utc(start_date)
    end_date = to_naive_utc(end_date)
    now = datetime.utcnow()
    if start_date and end_date:
        return start_date, end_date

    p = (period or "THIS_MONTH").upper()
    if p == "THIS_MONTH":
        s = datetime(now.year, now.month, 1, 0, 0, 0)
        # next month start
        if now.month == 12:
            e = datetime(now.year + 1, 1, 1, 0, 0, 0) - timedelta(microseconds=1)
        else:
            e = datetime(now.year, now.month + 1, 1, 0, 0, 0) - timedelta(microseconds=1)
        return s, e

    if p == "LAST_MONTH":
        if now.month == 1:
            s = datetime(now.year - 1, 12, 1, 0, 0, 0)
            e = datetime(now.year, 1, 1, 0, 0, 0) - timedelta(microseconds=1)
        else:
            s = datetime(now.year, now.month - 1, 1, 0, 0, 0)
            e = datetime(now.year, now.month, 1, 0, 0, 0) - timedelta(microseconds=1)
        return s, e

    if p == "THIS_QUARTER":
        quarter = (now.month - 1) // 3
        s = datetime(now.year, quarter * 3 + 1, 1, 0, 0, 0)
        end_month = quarter * 3 + 4
        if end_month > 12:
            e = datetime(now.year + 1, 1, 1, 0, 0, 0) - timedelta(microseconds=1)
        else:
            e = datetime(now.year, end_month, 1, 0, 0, 0) - timedelta(microseconds=1)
        return s, e

    # Default THIS_YEAR (Calendar / FY start Jan 1)
    s = datetime(now.year, 1, 1, 0, 0, 0)
    e = datetime(now.year, 12, 31, 23, 59, 59)
    return s, e


# ================================================================
# 1. PROFIT & LOSS STATEMENT
# ================================================================
async def get_pl_statement(
    db: AsyncSession,
    shop_id: uuid.UUID,
    period: str = "THIS_MONTH",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> PLStatementResponse:
    s_date, e_date = _compute_period_range(period, start_date, end_date)

    # Fetch shop
    q_shop = select(Shop).where(Shop.id == shop_id)
    res_shop = await db.execute(q_shop)
    shop = res_shop.scalar_one_or_none()
    currency = shop.currency if shop and shop.currency else "KES"

    # Fetch Documents in date range with items
    q_docs = (
        select(Document)
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.issue_date >= s_date,
                Document.issue_date <= e_date,
                Document.status != "CANCELLED",
            )
        )
        .options(selectinload(Document.items).selectinload(DocumentItem.product))
    )
    res_docs = await db.execute(q_docs)
    docs = list(res_docs.scalars().all())

    sales_revenue = Decimal("0.00")
    cogs = Decimal("0.00")

    for d in docs:
        is_receipt_from_invoice = d.type == "RECEIPT" and d.parent_document_id is not None
        if is_receipt_from_invoice:
            continue

        if d.type in ["INVOICE", "RECEIPT"]:
            sales_revenue += d.grand_total
            for it in d.items:
                cost = (it.product.cost_price or Decimal("0.00")) if it.product else Decimal("0.00")
                cogs += it.quantity * cost
        elif d.type == "CREDIT_NOTE":
            sales_revenue -= d.grand_total
            for it in d.items:
                cost = (it.product.cost_price or Decimal("0.00")) if it.product else Decimal("0.00")
                cogs -= it.quantity * cost

    # Fetch Operating Expenses
    q_exp = select(Expense).where(
        and_(
            Expense.shop_id == shop_id,
            Expense.expense_date >= s_date,
            Expense.expense_date <= e_date,
        )
    )
    res_exp = await db.execute(q_exp)
    expenses = list(res_exp.scalars().all())

    expense_map: Dict[str, Decimal] = {}
    for exp in expenses:
        cat = exp.category.upper()
        expense_map[cat] = expense_map.get(cat, Decimal("0.00")) + exp.amount

    expense_lines: List[PLLineItem] = [
        PLLineItem(label=cat.replace("_", " ").title(), amount=amt)
        for cat, amt in sorted(expense_map.items())
    ]
    total_operating_expenses = sum((item.amount for item in expense_lines), Decimal("0.00"))

    # Fetch Non-Operating Incomes
    q_inc = select(func.coalesce(func.sum(Income.amount), Decimal("0.00"))).where(
        and_(
            Income.shop_id == shop_id,
            Income.income_date >= s_date,
            Income.income_date <= e_date,
        )
    )
    res_inc = await db.execute(q_inc)
    non_operating_income: Decimal = res_inc.scalar() or Decimal("0.00")

    total_revenue = sales_revenue + non_operating_income
    gross_profit = sales_revenue - cogs
    gross_profit_margin = (
        (gross_profit / sales_revenue * Decimal("100.00"))
        if sales_revenue > Decimal("0.00")
        else Decimal("0.00")
    )
    net_operating_profit = gross_profit - total_operating_expenses
    net_income = net_operating_profit + non_operating_income

    return PLStatementResponse(
        period=period.replace("_", " ").title(),
        currency=currency,
        start_date=s_date,
        end_date=e_date,
        sales_revenue=sales_revenue,
        non_operating_income=non_operating_income,
        total_revenue=total_revenue,
        cogs=cogs,
        gross_profit=gross_profit,
        gross_profit_margin=gross_profit_margin.quantize(Decimal("0.01")),
        expense_lines=expense_lines,
        total_operating_expenses=total_operating_expenses,
        net_operating_profit=net_operating_profit,
        net_income=net_income,
    )


# ================================================================
# 2. BALANCE SHEET
# ================================================================
async def get_balance_sheet(
    db: AsyncSession,
    shop_id: uuid.UUID,
    as_of_date: Optional[datetime] = None,
) -> BalanceSheetResponse:
    as_of = to_naive_utc(as_of_date) or datetime.utcnow()

    # Fetch shop
    q_shop = select(Shop).where(Shop.id == shop_id)
    res_shop = await db.execute(q_shop)
    shop = res_shop.scalar_one_or_none()
    currency = shop.currency if shop and shop.currency else "KES"

    # 1. Accounts Receivable (Unpaid Invoices)
    q_invoices = select(Document).where(
        and_(
            Document.shop_id == shop_id,
            Document.type == "INVOICE",
            Document.issue_date <= as_of,
            Document.status.in_(["ISSUED", "PARTIALLY_PAID", "OVERDUE"]),
        )
    )
    res_invoices = await db.execute(q_invoices)
    invoices = list(res_invoices.scalars().all())

    ar = Decimal("0.00")
    for inv in invoices:
        q_paid = select(func.coalesce(func.sum(DocumentPayment.amount), Decimal("0.00"))).where(
            and_(DocumentPayment.document_id == inv.id, DocumentPayment.payment_date <= as_of)
        )
        res_paid = await db.execute(q_paid)
        paid = res_paid.scalar() or Decimal("0.00")
        ar += max(Decimal("0.00"), inv.grand_total - paid)

    # 2. Inventory Valuation
    q_prods = select(Product).where(
        and_(Product.shop_id == shop_id, Product.track_stock == True)
    )
    res_prods = await db.execute(q_prods)
    prods = list(res_prods.scalars().all())
    inventory_val = sum(
        (p.stock_quantity * (p.cost_price or Decimal("0.00")) for p in prods),
        Decimal("0.00"),
    )

    # 3. Cash & Bank
    q_total_payments = select(func.coalesce(func.sum(DocumentPayment.amount), Decimal("0.00"))).where(
        and_(DocumentPayment.shop_id == shop_id, DocumentPayment.payment_date <= as_of)
    )
    res_tp = await db.execute(q_total_payments)
    payments_received: Decimal = res_tp.scalar() or Decimal("0.00")

    q_total_incomes = select(func.coalesce(func.sum(Income.amount), Decimal("0.00"))).where(
        and_(Income.shop_id == shop_id, Income.income_date <= as_of)
    )
    res_ti = await db.execute(q_total_incomes)
    other_incomes: Decimal = res_ti.scalar() or Decimal("0.00")

    q_total_expenses = select(func.coalesce(func.sum(Expense.amount), Decimal("0.00"))).where(
        and_(Expense.shop_id == shop_id, Expense.expense_date <= as_of)
    )
    res_te = await db.execute(q_total_expenses)
    total_expenses: Decimal = res_te.scalar() or Decimal("0.00")

    cash_and_bank = max(Decimal("0.00"), payments_received + other_incomes - total_expenses)

    total_current_assets = cash_and_bank + ar + inventory_val
    fixed_assets_wdv = Decimal("0.00")
    total_non_current_assets = fixed_assets_wdv
    total_assets = total_current_assets + total_non_current_assets

    # Liabilities: Tax payable & Accounts payable
    q_vat_out = select(func.coalesce(func.sum(Document.tax_amount), Decimal("0.00"))).where(
        and_(
            Document.shop_id == shop_id,
            Document.type.in_(["INVOICE", "RECEIPT"]),
            Document.issue_date <= as_of,
            Document.status != "CANCELLED",
        )
    )
    res_vo = await db.execute(q_vat_out)
    tax_payable: Decimal = res_vo.scalar() or Decimal("0.00")

    accounts_payable = Decimal("0.00")
    total_current_liabilities = accounts_payable + tax_payable
    total_liabilities = total_current_liabilities

    # Equity: Current P&L net income + Retained Earnings
    pl = await get_pl_statement(
        db, shop_id, period="THIS_YEAR", start_date=datetime(as_of.year, 1, 1), end_date=as_of
    )
    current_period_net_profit = pl.net_income
    opening_balance_equity = Decimal("0.00")
    retained_earnings = max(Decimal("0.00"), total_assets - total_liabilities - current_period_net_profit)
    total_equity = opening_balance_equity + retained_earnings + current_period_net_profit

    diff = total_assets - (total_liabilities + total_equity)
    is_balanced = abs(diff) < Decimal("1.00")

    return BalanceSheetResponse(
        as_of_date=as_of,
        currency=currency,
        cash_and_bank=cash_and_bank,
        accounts_receivable=ar,
        inventory_valuation=inventory_val,
        total_current_assets=total_current_assets,
        fixed_assets_wdv=fixed_assets_wdv,
        total_non_current_assets=total_non_current_assets,
        total_assets=total_assets,
        accounts_payable=accounts_payable,
        tax_payable=tax_payable,
        total_current_liabilities=total_current_liabilities,
        total_liabilities=total_liabilities,
        opening_balance_equity=opening_balance_equity,
        retained_earnings=retained_earnings,
        current_period_net_profit=current_period_net_profit,
        total_equity=total_equity,
        difference=diff,
        is_balanced=is_balanced,
    )


# ================================================================
# 3. TRIAL BALANCE
# ================================================================
async def get_trial_balance(
    db: AsyncSession,
    shop_id: uuid.UUID,
    period_id: Optional[uuid.UUID] = None,
) -> TrialBalanceResponse:
    # 1. Fetch Chart of Accounts
    q_acc = select(ChartOfAccount).where(ChartOfAccount.shop_id == shop_id).order_by(ChartOfAccount.code)
    res_acc = await db.execute(q_acc)
    accounts = list(res_acc.scalars().all())

    # 2. Fetch Journal Entries
    conditions = [JournalEntry.shop_id == shop_id]
    if period_id:
        conditions.append(JournalEntry.period_id == period_id)
    q_je = select(JournalEntry).where(and_(*conditions))
    res_je = await db.execute(q_je)
    entries = list(res_je.scalars().all())

    debits_by_acc: Dict[uuid.UUID, Decimal] = {}
    credits_by_acc: Dict[uuid.UUID, Decimal] = {}

    for e in entries:
        debits_by_acc[e.debit_account_id] = debits_by_acc.get(e.debit_account_id, Decimal("0.00")) + e.amount
        credits_by_acc[e.credit_account_id] = credits_by_acc.get(e.credit_account_id, Decimal("0.00")) + e.amount

    rows: List[TrialBalanceRow] = []
    tot_debits = Decimal("0.00")
    tot_credits = Decimal("0.00")

    for acc in accounts:
        dr = debits_by_acc.get(acc.id, Decimal("0.00"))
        cr = credits_by_acc.get(acc.id, Decimal("0.00"))
        if dr == Decimal("0.00") and cr == Decimal("0.00"):
            continue

        tot_debits += dr
        tot_credits += cr
        balance = dr - cr

        rows.append(
            TrialBalanceRow(
                account_id=acc.id,
                code=acc.code,
                name=acc.name,
                account_type=acc.account_type,
                total_debits=dr,
                total_credits=cr,
                balance=balance,
            )
        )

    is_balanced = abs(tot_debits - tot_credits) < Decimal("0.01")
    return TrialBalanceResponse(
        shop_id=shop_id,
        rows=rows,
        total_debits=tot_debits,
        total_credits=tot_credits,
        is_balanced=is_balanced,
    )


# ================================================================
# 4. STATUTORY KRA VAT RETURN SUMMARY
# ================================================================
async def get_vat_return_summary(
    db: AsyncSession,
    shop_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
) -> VatReturnSummaryResponse:
    start_date = to_naive_utc(start_date)
    end_date = to_naive_utc(end_date)
    q_shop = select(Shop).where(Shop.id == shop_id)
    res_shop = await db.execute(q_shop)
    shop = res_shop.scalar_one_or_none()
    shop_name = shop.name if shop else "Workspace"
    tax_pin = None  # Fetched from etims device config if available

    # Fetch Sales Documents in date range
    q_docs = (
        select(Document)
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.issue_date >= start_date,
                Document.issue_date <= end_date,
                Document.status != "CANCELLED",
            )
        )
        .options(selectinload(Document.items))
    )
    res_docs = await db.execute(q_docs)
    docs = list(res_docs.scalars().all())

    box_1_sales = Decimal("0.00")
    box_1_output_vat = Decimal("0.00")
    box_2_sales = Decimal("0.00")
    box_3_sales = Decimal("0.00")
    total_sales = Decimal("0.00")
    total_output_vat = Decimal("0.00")

    for d in docs:
        if d.type in ["INVOICE", "RECEIPT"]:
            factor = Decimal("1.00")
        elif d.type == "CREDIT_NOTE":
            factor = Decimal("-1.00")
        else:
            continue

        for it in d.items:
            tax_type = (it.tax_type or "V_16").upper()
            if tax_type == "V_16":
                box_1_sales += it.item_total * factor
                box_1_output_vat += it.tax_amount * factor
            elif tax_type == "V_0":
                box_2_sales += it.item_total * factor
            elif tax_type == "EXEMPT":
                box_3_sales += it.item_total * factor

        total_sales += d.sub_total * factor
        total_output_vat += d.tax_amount * factor

    # Input tax from supplier vendor bills / procurement documents
    q_purchases = (
        select(Document)
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.type.in_(["LPO", "GOODS_RECEIVED_NOTE", "PAYMENT_VOUCHER"]),
                Document.issue_date >= start_date,
                Document.issue_date <= end_date,
                Document.tax_amount > Decimal("0.00"),
                Document.status != "CANCELLED",
            )
        )
        .options(selectinload(Document.supplier))
    )
    res_purchases = await db.execute(q_purchases)
    purchases = list(res_purchases.scalars().all())

    input_entries: List[VatReturnInputTaxEntry] = []
    box_8_input_vat = Decimal("0.00")

    for p in purchases:
        sup_name = p.supplier.name if p.supplier else "Vendor"
        sup_pin = p.supplier.tax_pin if p.supplier else None
        taxable_val = p.sub_total or Decimal("0.00")
        vat_amt = p.tax_amount or Decimal("0.00")
        box_8_input_vat += vat_amt

        input_entries.append(
            VatReturnInputTaxEntry(
                supplier_name=sup_name,
                supplier_pin=sup_pin,
                invoice_number=p.doc_number,
                invoice_date=p.issue_date,
                taxable_value=taxable_val,
                input_vat_claimed=vat_amt,
            )
        )

    net_vat = total_output_vat - box_8_input_vat
    period_label = f"{start_date.strftime('%b %Y')} - {end_date.strftime('%b %Y')}"

    return VatReturnSummaryResponse(
        shop_id=shop_id,
        shop_name=shop_name,
        tax_pin=tax_pin,
        period_label=period_label,
        start_date=start_date,
        end_date=end_date,
        box_1_standard_rated_sales=box_1_sales,
        box_1_output_vat=box_1_output_vat,
        box_2_zero_rated_sales=box_2_sales,
        box_3_exempt_sales=box_3_sales,
        total_sales_turnover=total_sales,
        total_output_vat=total_output_vat,
        box_8_total_input_vat_claimed=box_8_input_vat,
        input_vat_entries=input_entries,
        net_vat_due=net_vat,
    )


# ================================================================
# 5. STOCK MOVEMENT & VALUATION LEDGER
# ================================================================
async def get_stock_ledger_report(
    db: AsyncSession,
    shop_id: uuid.UUID,
    product_id: Optional[uuid.UUID] = None,
    location_id: Optional[uuid.UUID] = None,
    limit: int = 200,
) -> List[StockLedgerEntryResponse]:
    conditions = [StockLedger.shop_id == shop_id]
    if product_id:
        conditions.append(StockLedger.product_id == product_id)
    if location_id:
        conditions.append(StockLedger.location_id == location_id)

    q = (
        select(StockLedger)
        .where(and_(*conditions))
        .options(
            selectinload(StockLedger.product),
            selectinload(StockLedger.location),
        )
        .order_by(desc(StockLedger.created_at))
        .limit(limit)
    )
    res = await db.execute(q)
    entries = list(res.scalars().all())

    results: List[StockLedgerEntryResponse] = []
    for e in entries:
        p_name = e.product.name if e.product else "Unknown Product"
        sku = e.product.sku if e.product else None
        loc_name = e.location.name if e.location else None
        unit_cost = e.unit_cost or Decimal("0.00")
        tot_cost = e.quantity * unit_cost

        results.append(
            StockLedgerEntryResponse(
                id=e.id,
                product_id=e.product_id,
                product_name=p_name,
                sku=sku,
                location_id=e.location_id,
                location_name=loc_name,
                movement_type=e.movement_type,
                quantity=e.quantity,
                unit_cost=unit_cost,
                total_cost=tot_cost,
                reference=e.notes,
                source_doc_id=e.source_document_id,
                created_at=e.created_at,
            )
        )

    return results


async def get_stock_valuation_report(
    db: AsyncSession, shop_id: uuid.UUID
) -> List[StockValuationRowResponse]:
    q = (
        select(Product)
        .where(and_(Product.shop_id == shop_id, Product.track_stock == True))
        .order_by(Product.name)
    )
    res = await db.execute(q)
    prods = list(res.scalars().all())

    rows: List[StockValuationRowResponse] = []
    for p in prods:
        cost = p.cost_price or Decimal("0.00")
        qty = p.stock_quantity or Decimal("0.00")
        rows.append(
            StockValuationRowResponse(
                product_id=p.id,
                name=p.name,
                sku=p.sku,
                stock_quantity=qty,
                unit_cost=cost,
                total_valuation=qty * cost,
            )
        )

    return rows
