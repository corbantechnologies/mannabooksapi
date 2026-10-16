import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from app.core.schemas import ApiModel


# ==========================================
# 1. PROFIT & LOSS STATEMENT
# ==========================================
class PLLineItem(ApiModel):
    label: str
    amount: Decimal
    account_code: Optional[str] = None


class PLStatementResponse(ApiModel):
    period: str
    currency: str
    start_date: datetime
    end_date: datetime
    # Revenue
    sales_revenue: Decimal
    non_operating_income: Decimal
    total_revenue: Decimal
    # COGS & Margins
    cogs: Decimal
    gross_profit: Decimal
    gross_profit_margin: Decimal
    # Expenses Breakdown
    expense_lines: List[PLLineItem]
    total_operating_expenses: Decimal
    # Bottom Lines
    net_operating_profit: Decimal
    net_income: Decimal


# ==========================================
# 2. BALANCE SHEET
# ==========================================
class BalanceSheetResponse(ApiModel):
    as_of_date: datetime
    currency: str
    # Current Assets
    cash_and_bank: Decimal
    accounts_receivable: Decimal
    inventory_valuation: Decimal
    total_current_assets: Decimal
    # Non-Current Assets
    fixed_assets_wdv: Decimal
    total_non_current_assets: Decimal
    total_assets: Decimal
    # Liabilities
    accounts_payable: Decimal
    tax_payable: Decimal
    total_current_liabilities: Decimal
    total_liabilities: Decimal
    # Equity
    opening_balance_equity: Decimal
    retained_earnings: Decimal
    current_period_net_profit: Decimal
    total_equity: Decimal
    # Reconciliation
    difference: Decimal
    is_balanced: bool


# ==========================================
# 3. TRIAL BALANCE
# ==========================================
class TrialBalanceRow(ApiModel):
    account_id: uuid.UUID
    code: str
    name: str
    account_type: str
    total_debits: Decimal
    total_credits: Decimal
    balance: Decimal


class TrialBalanceResponse(ApiModel):
    shop_id: uuid.UUID
    rows: List[TrialBalanceRow]
    total_debits: Decimal
    total_credits: Decimal
    is_balanced: bool


# ==========================================
# 4. KRA STATUTORY VAT RETURN SUMMARY
# ==========================================
class VatReturnInputTaxEntry(ApiModel):
    supplier_name: str
    supplier_pin: Optional[str] = None
    invoice_number: str
    invoice_date: datetime
    taxable_value: Decimal
    input_vat_claimed: Decimal


class VatReturnSummaryResponse(ApiModel):
    shop_id: uuid.UUID
    shop_name: str
    tax_pin: Optional[str] = None
    period_label: str
    start_date: datetime
    end_date: datetime
    # Output Tax (Sales)
    box_1_standard_rated_sales: Decimal  # 16% VAT Sales
    box_1_output_vat: Decimal
    box_2_zero_rated_sales: Decimal      # 0% VAT Sales
    box_3_exempt_sales: Decimal          # Exempt Sales
    total_sales_turnover: Decimal
    total_output_vat: Decimal
    # Input Tax (Purchases)
    box_8_total_input_vat_claimed: Decimal
    input_vat_entries: List[VatReturnInputTaxEntry]
    # Net Assessment
    net_vat_due: Decimal                 # Positive = Payable to KRA, Negative = Refund/Credit


# ==========================================
# 5. STOCK MOVEMENT & VALUATION LEDGER
# ==========================================
class StockLedgerEntryResponse(ApiModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    sku: Optional[str] = None
    location_id: Optional[uuid.UUID] = None
    location_name: Optional[str] = None
    movement_type: str                  # INFLOW, OUTFLOW, TRANSFER, ADJUSTMENT
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    reference: Optional[str] = None
    source_doc_id: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StockValuationRowResponse(ApiModel):
    product_id: uuid.UUID
    name: str
    sku: Optional[str] = None
    stock_quantity: Decimal
    unit_cost: Decimal
    total_valuation: Decimal
