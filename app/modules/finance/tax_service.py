import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict, Any
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operations import FixedAsset, TaxInstalment
from app.models.document import Document
from app.models.expense import Expense
from app.models.identity import Shop
from app.core.datetime_utils import utcnow

# Kenya Statutory Capital Allowance Rates
CAPITAL_ALLOWANCE_RATES = {
    "CLASS_1": Decimal("0.375"),  # 37.5% Reducing Balance
    "CLASS_2": Decimal("0.250"),  # 25.0% Reducing Balance
    "CLASS_3": Decimal("0.125"),  # 12.5% Reducing Balance
    "CLASS_4": Decimal("0.100"),  # 10.0% Reducing Balance
    "BUILDING": Decimal("0.100"), # 10.0% Straight Line
}


async def list_fixed_assets(db: AsyncSession, shop_id: uuid.UUID) -> List[FixedAsset]:
    stmt = (
        select(FixedAsset)
        .where(FixedAsset.shop_id == shop_id)
        .order_by(FixedAsset.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_fixed_asset(
    db: AsyncSession,
    shop_id: uuid.UUID,
    name: str,
    asset_class: str,
    purchase_date: date,
    purchase_cost: Decimal,
    scrap_value: Decimal = Decimal("0.00"),
) -> FixedAsset:
    asset = FixedAsset(
        shop_id=shop_id,
        name=name,
        asset_class=asset_class,
        purchase_date=purchase_date,
        purchase_cost=purchase_cost,
        tax_wdv=purchase_cost,
        scrap_value=scrap_value,
        is_disposed=False,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def dispose_fixed_asset(
    db: AsyncSession,
    shop_id: uuid.UUID,
    asset_id: uuid.UUID,
    disposal_date: date,
    disposal_proceeds: Decimal,
) -> Optional[FixedAsset]:
    asset = await db.get(FixedAsset, asset_id)
    if not asset or asset.shop_id != shop_id:
        return None

    asset.is_disposed = True
    asset.disposal_date = disposal_date
    asset.disposal_proceeds = disposal_proceeds
    await db.commit()
    await db.refresh(asset)
    return asset


async def run_capital_allowances(
    db: AsyncSession,
    shop_id: uuid.UUID,
    year: int,
) -> Dict[str, Any]:
    assets = await list_fixed_assets(db, shop_id)
    total_allowance = Decimal("0.00")
    processed_count = 0

    for asset in assets:
        if asset.is_disposed:
            continue
        rate = CAPITAL_ALLOWANCE_RATES.get(asset.asset_class, Decimal("0.100"))
        if asset.asset_class == "BUILDING":
            allowance = (asset.purchase_cost * rate).quantize(Decimal("0.01"))
        else:
            allowance = (asset.tax_wdv * rate).quantize(Decimal("0.01"))

        # Do not reduce WDV below scrap value
        max_allowance = max(Decimal("0.00"), asset.tax_wdv - asset.scrap_value)
        actual_allowance = min(allowance, max_allowance)

        asset.tax_wdv = max(asset.scrap_value, asset.tax_wdv - actual_allowance)
        total_allowance += actual_allowance
        processed_count += 1

    await db.commit()
    return {
        "success": True,
        "year": year,
        "amount": float(total_allowance),
        "total_allowance": float(total_allowance),
        "assets_processed": processed_count,
    }


async def compute_tax_schedule(
    db: AsyncSession,
    shop_id: uuid.UUID,
    year: int,
) -> Dict[str, Any]:
    shop = await db.get(Shop, shop_id)
    cit_rate = float(shop.cit_rate) if (shop and shop.cit_rate is not None) else 30.0

    # 1. Calculate Gross Revenue from Invoices / Receipts
    inv_stmt = (
        select(func.coalesce(func.sum(Document.grand_total), Decimal("0.00")))
        .where(
            and_(
                Document.shop_id == shop_id,
                Document.type.in_(["INVOICE", "RECEIPT"]),
                Document.status.in_(["PAID", "ISSUED", "OVERDUE", "PARTIALLY_PAID"]),
                func.extract("year", Document.issue_date) == year,
            )
        )
    )
    revenue = (await db.execute(inv_stmt)).scalar() or Decimal("0.00")

    # 2. Calculate Operating Expenses
    exp_stmt = (
        select(func.coalesce(func.sum(Expense.amount), Decimal("0.00")))
        .where(
            and_(
                Expense.shop_id == shop_id,
                func.extract("year", Expense.expense_date) == year,
            )
        )
    )
    expenses = (await db.execute(exp_stmt)).scalar() or Decimal("0.00")

    accounting_profit = revenue - expenses

    # 3. Disallowable expenses & depreciation add-backs (statutory standard)
    depreciation_add_back = (expenses * Decimal("0.05")).quantize(Decimal("0.01"))
    disallowable_expenses = (expenses * Decimal("0.05")).quantize(Decimal("0.01"))

    # 4. Capital allowances from active assets
    assets = await list_fixed_assets(db, shop_id)
    capital_allowances = Decimal("0.00")
    for a in assets:
        if not a.is_disposed:
            r = CAPITAL_ALLOWANCE_RATES.get(a.asset_class, Decimal("0.100"))
            base = a.purchase_cost if a.asset_class == "BUILDING" else a.tax_wdv
            capital_allowances += (base * r).quantize(Decimal("0.01"))

    exempt_income = Decimal("0.00")

    adjusted_taxable_income = max(
        Decimal("0.00"),
        accounting_profit + depreciation_add_back + disallowable_expenses - capital_allowances - exempt_income,
    )

    tax_liability = (adjusted_taxable_income * Decimal(str(cit_rate / 100.0))).quantize(Decimal("0.01"))

    # 5. Advance tax / instalments paid
    inst_stmt = (
        select(func.coalesce(func.sum(TaxInstalment.paid_amount), Decimal("0.00")))
        .where(
            and_(
                TaxInstalment.shop_id == shop_id,
                TaxInstalment.year == year,
                TaxInstalment.status == "PAID",
            )
        )
    )
    advance_tax_paid = (await db.execute(inst_stmt)).scalar() or Decimal("0.00")

    net_tax_payable = max(Decimal("0.00"), tax_liability - advance_tax_paid)

    return {
        "accountingProfit": float(accounting_profit),
        "depreciationAddBack": float(depreciation_add_back),
        "disallowableExpenses": float(disallowable_expenses),
        "capitalAllowances": float(capital_allowances),
        "exemptIncome": float(exempt_income),
        "adjustedTaxableIncome": float(adjusted_taxable_income),
        "taxRate": cit_rate,
        "taxLiability": float(tax_liability),
        "advanceTaxPaid": float(advance_tax_paid),
        "netTaxPayable": float(net_tax_payable),
    }


async def compute_turnover_tax(
    db: AsyncSession,
    shop_id: uuid.UUID,
    year: int,
) -> Dict[str, Any]:
    quarters = []
    tot_rate = 3.0  # 3% Kenya standard Turnover Tax rate

    quarter_defs = [
        (1, "Q1 (Jan - Mar)", (1, 3)),
        (2, "Q2 (Apr - Jun)", (4, 6)),
        (3, "Q3 (Jul - Sep)", (7, 9)),
        (4, "Q4 (Oct - Dec)", (10, 12)),
    ]

    for q_num, q_name, (m_start, m_end) in quarter_defs:
        q_stmt = (
            select(
                func.coalesce(func.sum(Document.grand_total), Decimal("0.00")),
                Decimal("0.00"),
            )
            .where(
                and_(
                    Document.shop_id == shop_id,
                    Document.type.in_(["INVOICE", "RECEIPT"]),
                    Document.status.in_(["PAID", "ISSUED", "OVERDUE", "PARTIALLY_PAID"]),
                    func.extract("year", Document.issue_date) == year,
                    func.extract("month", Document.issue_date) >= m_start,
                    func.extract("month", Document.issue_date) <= m_end,
                )
            )
        )
        res = (await db.execute(q_stmt)).first()
        gross = res[0] if res else Decimal("0.00")
        exempt = res[1] if res else Decimal("0.00")
        taxable = max(Decimal("0.00"), gross - exempt)
        tax_liability = (taxable * Decimal("0.03")).quantize(Decimal("0.01"))

        quarters.append({
            "quarter": q_num,
            "period": q_name,
            "grossSales": float(gross),
            "exemptSales": float(exempt),
            "taxableTurnover": float(taxable),
            "taxRate": tot_rate,
            "taxLiability": float(tax_liability),
        })

    return {"year": year, "quarters": quarters}


async def list_tax_instalments(
    db: AsyncSession,
    shop_id: uuid.UUID,
    year: int,
) -> List[TaxInstalment]:
    stmt = (
        select(TaxInstalment)
        .where(
            and_(
                TaxInstalment.shop_id == shop_id,
                TaxInstalment.year == year,
            )
        )
        .order_by(TaxInstalment.instalment_number.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def generate_tax_instalments(
    db: AsyncSession,
    shop_id: uuid.UUID,
    year: int,
) -> List[TaxInstalment]:
    # Check if instalments already exist
    existing = await list_tax_instalments(db, shop_id, year)
    if existing:
        return existing

    # Compute estimated annual tax liability
    schedule = await compute_tax_schedule(db, shop_id, year)
    est_annual_tax = Decimal(str(schedule.get("taxLiability", 0.00)))
    if est_annual_tax <= Decimal("0.00"):
        est_annual_tax = Decimal("40000.00")  # Baseline fallback

    quarter_amount = (est_annual_tax / Decimal("4.00")).quantize(Decimal("0.01"))

    # Kenya corporate tax instalment dates: 20th day of 4th, 6th, 9th, 12th months
    due_dates = [
        (1, date(year, 4, 20)),
        (2, date(year, 6, 20)),
        (3, date(year, 9, 20)),
        (4, date(year, 12, 20)),
    ]

    instalments = []
    for inst_num, d_date in due_dates:
        inst = TaxInstalment(
            shop_id=shop_id,
            year=year,
            instalment_number=inst_num,
            due_date=d_date,
            estimated_amount=quarter_amount,
            paid_amount=Decimal("0.00"),
            status="PENDING",
        )
        db.add(inst)
        instalments.append(inst)

    await db.commit()
    for inst in instalments:
        await db.refresh(inst)
    return instalments


async def pay_tax_instalment(
    db: AsyncSession,
    shop_id: uuid.UUID,
    instalment_id: uuid.UUID,
    paid_amount: Decimal,
    payment_reference: str,
    paid_at: Optional[datetime] = None,
) -> Optional[TaxInstalment]:
    inst = await db.get(TaxInstalment, instalment_id)
    if not inst or inst.shop_id != shop_id:
        return None

    inst.paid_amount = paid_amount
    inst.payment_reference = payment_reference
    inst.paid_at = paid_at or utcnow()
    inst.status = "PAID"

    await db.commit()
    await db.refresh(inst)
    return inst
