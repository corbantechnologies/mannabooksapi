# app/modules/accounts/ledger_router.py
"""
Ledger operations: fiscal years and periods (with closing), manual/compound journals, opening
balances, and the ledger integrity check. Mounted at /accounts next to the chart-of-accounts router.
"""
import calendar
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AliasChoices, BaseModel, Field, field_validator
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_acting_user_id
from app.core.datetime_utils import to_naive_utc, utcnow
from app.db.session import get_db
from app.models.account import AccountingPeriod, ChartOfAccount, FiscalYear
from app.models.governance import AuditLog
from app.models.journal import JournalEntry
from app.modules.accounts.posting import (
    add_entries,
    account_map,
    assert_period_open,
    integrity_report,
    pair,
    remove_source_entries,
    repair_ledger,
)

router = APIRouter(prefix="/accounts", tags=["General Ledger"])

CENT = Decimal("0.01")
RETAINED_EARNINGS = "3100"
OPENING_EQUITY = "3200"


def _audit(shop_id, user_id, action, table, record_id, label, before=None, after=None) -> AuditLog:
    return AuditLog(
        shop_id=shop_id, user_id=user_id, action=action, table_name=table,
        record_id=record_id, record_label=label[:255], before_state=before, after_state=after,
    )


async def _own_accounts(db: AsyncSession, shop_id: uuid.UUID, ids: set[uuid.UUID]) -> dict[uuid.UUID, ChartOfAccount]:
    rows = (await db.execute(select(ChartOfAccount).where(ChartOfAccount.shop_id == shop_id, ChartOfAccount.id.in_(ids)))).scalars().all()
    found = {a.id: a for a in rows}
    if len(found) != len(ids):
        raise HTTPException(status_code=400, detail="One or more accounts do not exist in this workspace.")
    return found


# ---------------------------------------------------------------------------
# Fiscal years & periods
# ---------------------------------------------------------------------------
class DeclareFiscalYearRequest(BaseModel):
    shop_id: uuid.UUID
    label: str = Field(..., min_length=2, max_length=100)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None


def _month_periods(start: date, end: date) -> list[tuple[str, date, date]]:
    out, cur = [], start
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        p_end = min(last, end)
        out.append((cur.strftime("%b %Y"), cur, p_end))
        cur = p_end + timedelta(days=1)
    return out


async def _ensure_periods(db: AsyncSession, fy: FiscalYear) -> list[AccountingPeriod]:
    periods = (
        await db.execute(select(AccountingPeriod).where(AccountingPeriod.fiscal_year_id == fy.id).order_by(AccountingPeriod.start_date))
    ).scalars().all()
    if periods:
        return list(periods)
    created = []
    for name, s, e in _month_periods(fy.start_date, fy.end_date):
        p = AccountingPeriod(shop_id=fy.shop_id, fiscal_year_id=fy.id, period_name=name, start_date=s, end_date=e,
                             status="CLOSED" if fy.is_closed else "OPEN")
        db.add(p)
        created.append(p)
    await db.flush()
    return created


@router.post("/fiscal-years", status_code=201)
async def declare_fiscal_year(req: DeclareFiscalYearRequest, db: AsyncSession = Depends(get_db)):
    try:
        s = date.fromisoformat((req.startDate or req.start_date or "")[:10])
        e = date.fromisoformat((req.endDate or req.end_date or "")[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail="Start and end dates are required (YYYY-MM-DD).")
    if e <= s:
        raise HTTPException(status_code=400, detail="The end date must be after the start date.")
    if (e - s).days > 550:
        raise HTTPException(status_code=400, detail="A fiscal year can be at most 18 months long.")
    overlap = (
        await db.execute(
            select(FiscalYear.label).where(FiscalYear.shop_id == req.shop_id, FiscalYear.start_date <= e, FiscalYear.end_date >= s)
        )
    ).scalars().first()
    if overlap:
        raise HTTPException(status_code=409, detail=f"These dates overlap the fiscal year '{overlap}'.")

    fy = FiscalYear(shop_id=req.shop_id, label=req.label.strip(), start_date=s, end_date=e, is_closed=False, is_current=False)
    db.add(fy)
    await db.flush()
    periods = await _ensure_periods(db, fy)
    await db.commit()
    return {
        "success": True,
        "fiscalYear": {"id": str(fy.id), "label": fy.label, "startDate": str(s), "endDate": str(e), "isClosed": False, "isCurrent": False},
        "periodsCreated": len(periods),
    }


async def _account_balances(db: AsyncSession, shop_id: uuid.UUID, start: date, end: date, exclude_source: Optional[uuid.UUID] = None):
    """Debit-positive balance per account for entries dated within [start, end]."""
    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end, datetime.max.time())
    conds = [JournalEntry.shop_id == shop_id, JournalEntry.entry_date >= lo, JournalEntry.entry_date <= hi]
    if exclude_source:
        conds.append(JournalEntry.source_id.is_distinct_from(exclude_source))
    bal: dict[uuid.UUID, Decimal] = defaultdict(lambda: Decimal("0"))
    for d, c, amt in (await db.execute(select(JournalEntry.debit_account_id, JournalEntry.credit_account_id, JournalEntry.amount).where(*conds))).all():
        bal[d] += amt
        bal[c] -= amt
    count, total = (await db.execute(select(func.count(JournalEntry.id), func.coalesce(func.sum(JournalEntry.amount), 0)).where(*conds))).one()
    return bal, int(count), Decimal(total)


async def _pl_summary(db: AsyncSession, shop_id: uuid.UUID, start: date, end: date, exclude_source=None):
    bal, count, total = await _account_balances(db, shop_id, start, end, exclude_source)
    accounts = {a.id: a for a in (await db.execute(select(ChartOfAccount).where(ChartOfAccount.shop_id == shop_id))).scalars().all()}
    revenue = {k: -v for k, v in bal.items() if k in accounts and accounts[k].account_type == "REVENUE"}
    expense = {k: v for k, v in bal.items() if k in accounts and accounts[k].account_type == "EXPENSE"}
    return accounts, revenue, expense, count, total


def _top(items: dict, accounts: dict, total: Decimal) -> list[dict]:
    rows = sorted(((accounts[k], v) for k, v in items.items() if v != 0), key=lambda r: r[1], reverse=True)[:5]
    return [
        {"code": a.code, "name": a.name, "amount": float(v), "percentage": float((v / total * 100).quantize(CENT)) if total else 0.0}
        for a, v in rows
    ]


@router.get("/fiscal-years/{fy_id}")
async def fiscal_year_detail(fy_id: uuid.UUID, shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    fy = await db.get(FiscalYear, fy_id)
    if not fy or fy.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    periods = await _ensure_periods(db, fy)
    await db.commit()

    accounts, revenue, expense, count, total = await _pl_summary(db, shop_id, fy.start_date, fy.end_date, exclude_source=fy.id)
    total_rev = sum(revenue.values(), Decimal("0"))
    total_exp = sum(expense.values(), Decimal("0"))
    net = total_rev - total_exp

    quarters = []
    for i in range(4):
        q_periods = periods[i * 3:(i + 1) * 3]
        if not q_periods:
            break
        _, q_rev, q_exp, _, _ = await _pl_summary(db, shop_id, q_periods[0].start_date, q_periods[-1].end_date, exclude_source=fy.id)
        r, x = sum(q_rev.values(), Decimal("0")), sum(q_exp.values(), Decimal("0"))
        quarters.append({
            "quarter": f"Q{i + 1}",
            "revenue": float(r),
            "expenses": float(x),
            "netProfit": float(r - x),
            "status": "CLOSED" if all(p.status == "CLOSED" for p in q_periods) else "OPEN",
        })

    closed = [p for p in periods if p.status == "CLOSED"]
    unclosed = [p.period_name for p in periods if p.status != "CLOSED"]
    re_acc = next((a for a in accounts.values() if a.code == RETAINED_EARNINGS), None)
    return {
        "success": True,
        "fiscalYear": {"id": str(fy.id), "label": fy.label, "startDate": str(fy.start_date), "endDate": str(fy.end_date),
                       "isClosed": bool(fy.is_closed), "isCurrent": bool(fy.is_current)},
        "periods": [
            {"id": str(p.id), "periodName": p.period_name, "startDate": str(p.start_date), "endDate": str(p.end_date),
             "status": p.status, "closedAt": p.closed_at.isoformat() if p.closed_at else None,
             "fiscalYearId": str(fy.id), "fiscalYearLabel": fy.label}
            for p in periods
        ],
        "kpis": {
            "totalRevenue": float(total_rev), "totalExpenses": float(total_exp),
            "netProfit": float(net), "netIncome": float(net),
            "netMargin": float((net / total_rev * 100).quantize(CENT)) if total_rev else 0.0,
            "entryCount": count, "totalEntries": count, "totalDebits": float(total), "isBalanced": True,
            "closedPeriodsCount": len(closed), "totalPeriodsCount": len(periods),
            "lockProgressPercent": round(len(closed) / len(periods) * 100) if periods else 0,
        },
        "quarters": quarters,
        "topRevenueAccounts": _top(revenue, accounts, total_rev),
        "topExpenseAccounts": _top(expense, accounts, total_exp),
        "revenueAccounts": _top(revenue, accounts, total_rev),
        "closingChecklist": {
            "allPeriodsClosed": not unclosed,
            "unclosedCount": len(unclosed),
            "unclosedPeriodNames": unclosed,
            "isBalanced": True,
            "retainedEarningsAccount": f"{re_acc.code} {re_acc.name}" if re_acc else "3100 Retained Earnings (created on close)",
            "canCloseYear": not unclosed and not fy.is_closed,
        },
    }


class ShopBody(BaseModel):
    shop_id: uuid.UUID


@router.post("/fiscal-years/{fy_id}/close")
async def close_fiscal_year(fy_id: uuid.UUID, req: ShopBody, request: Request, db: AsyncSession = Depends(get_db)):
    """Close the year: all periods must be closed; profit or loss is transferred to Retained Earnings."""
    fy = await db.get(FiscalYear, fy_id)
    if not fy or fy.shop_id != req.shop_id:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    if fy.is_closed:
        return {"success": True, "message": f"Fiscal year '{fy.label}' is already closed."}
    periods = await _ensure_periods(db, fy)
    open_names = [p.period_name for p in periods if p.status != "CLOSED"]
    if open_names:
        raise HTTPException(status_code=409, detail=f"Close every period first. Still open: {', '.join(open_names[:6])}.")

    accounts, revenue, expense, _, _ = await _pl_summary(db, fy.shop_id, fy.start_date, fy.end_date, exclude_source=fy.id)
    acc = await account_map(db, fy.shop_id)
    retained = acc[RETAINED_EARNINGS]
    when = datetime.combine(fy.end_date, datetime.min.time())
    # Year-end entries are dated inside the (now closed) year on purpose, so they bypass the period lock.
    await db.execute(delete(JournalEntry).where(JournalEntry.shop_id == fy.shop_id, JournalEntry.source_id == fy.id))
    count = 0
    for acc_id, amount in revenue.items():  # credit balances -> debit revenue, credit retained earnings
        e = pair(fy.shop_id, when, f"Year-end close {fy.label}: {accounts[acc_id].name}", accounts[acc_id], retained,
                 amount if amount > 0 else -amount, "year_end_close", fy.id, "YEAR-END")
        if e is not None:
            if amount < 0:
                e.debit_account_id, e.credit_account_id = retained.id, accounts[acc_id].id
            db.add(e)
            count += 1
    for acc_id, amount in expense.items():  # debit balances -> debit retained earnings, credit expense
        e = pair(fy.shop_id, when, f"Year-end close {fy.label}: {accounts[acc_id].name}", retained, accounts[acc_id],
                 amount if amount > 0 else -amount, "year_end_close", fy.id, "YEAR-END")
        if e is not None:
            if amount < 0:
                e.debit_account_id, e.credit_account_id = accounts[acc_id].id, retained.id
            db.add(e)
            count += 1

    fy.is_closed = True
    fy.is_current = False
    net = sum(revenue.values(), Decimal("0")) - sum(expense.values(), Decimal("0"))
    db.add(_audit(fy.shop_id, get_acting_user_id(request), "FISCAL_YEAR_CLOSED", "fiscal_years", fy.id, fy.label,
                  after={"closingEntries": count, "netProfit": str(net)}))
    await db.commit()
    return {"success": True, "message": f"Fiscal year '{fy.label}' closed. Net result {net:,.2f} moved to Retained Earnings.", "closingEntries": count}


@router.post("/fiscal-years/{fy_id}/reopen")
async def reopen_fiscal_year(fy_id: uuid.UUID, req: ShopBody, request: Request, db: AsyncSession = Depends(get_db)):
    fy = await db.get(FiscalYear, fy_id)
    if not fy or fy.shop_id != req.shop_id:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    await db.execute(delete(JournalEntry).where(JournalEntry.shop_id == fy.shop_id, JournalEntry.source_id == fy.id))
    fy.is_closed = False
    db.add(_audit(fy.shop_id, get_acting_user_id(request), "FISCAL_YEAR_REOPENED", "fiscal_years", fy.id, fy.label))
    await db.commit()
    return {"success": True, "message": f"Fiscal year '{fy.label}' reopened; closing entries removed. Periods stay closed until you reopen them."}


@router.get("/periods")
async def list_periods(shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    fys = (await db.execute(select(FiscalYear).where(FiscalYear.shop_id == shop_id))).scalars().all()
    for fy in fys:
        await _ensure_periods(db, fy)
    await db.commit()
    labels = {fy.id: fy.label for fy in fys}
    periods = (
        await db.execute(select(AccountingPeriod).where(AccountingPeriod.shop_id == shop_id).order_by(AccountingPeriod.start_date.desc()))
    ).scalars().all()
    return {
        "success": True,
        "periods": [
            {
                "id": str(p.id), "periodName": p.period_name, "startDate": str(p.start_date), "endDate": str(p.end_date),
                "status": p.status, "closedAt": p.closed_at.isoformat() if p.closed_at else None, "closedByName": None,
                "fiscalYearId": str(p.fiscal_year_id) if p.fiscal_year_id else None,
                "fiscalYearLabel": labels.get(p.fiscal_year_id, "Unassigned"),
                "fiscalYear": {"id": str(p.fiscal_year_id), "label": labels[p.fiscal_year_id]} if p.fiscal_year_id in labels else None,
            }
            for p in periods
        ],
    }


@router.get("/periods/{period_id}/details")
async def period_details(period_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    p = await db.get(AccountingPeriod, period_id)
    if not p:
        raise HTTPException(status_code=404, detail="Accounting period not found")
    accounts, revenue, expense, count, total = await _pl_summary(db, p.shop_id, p.start_date, p.end_date)
    rev, exp = sum(revenue.values(), Decimal("0")), sum(expense.values(), Decimal("0"))
    lo, hi = datetime.combine(p.start_date, datetime.min.time()), datetime.combine(p.end_date, datetime.max.time())
    entries = (
        await db.execute(
            select(JournalEntry).where(JournalEntry.shop_id == p.shop_id, JournalEntry.entry_date >= lo, JournalEntry.entry_date <= hi)
            .order_by(JournalEntry.entry_date.desc()).limit(100)
        )
    ).scalars().all()
    return {
        "success": True,
        "period": {"id": str(p.id), "periodName": p.period_name, "startDate": str(p.start_date), "endDate": str(p.end_date),
                   "status": p.status, "closedAt": p.closed_at.isoformat() if p.closed_at else None},
        "kpis": {"entryCount": count, "totalRevenue": float(rev), "totalExpenses": float(exp), "netIncome": float(rev - exp),
                 "isBalanced": True, "totalDebits": float(total), "totalCredits": float(total)},
        "journalEntries": [
            {"id": str(e.id), "entryDate": e.entry_date.isoformat(), "description": e.description, "amount": str(e.amount),
             "debitAccount": accounts.get(e.debit_account_id).name if accounts.get(e.debit_account_id) else None,
             "creditAccount": accounts.get(e.credit_account_id).name if accounts.get(e.credit_account_id) else None,
             "sourceType": e.source_type}
            for e in entries
        ],
    }


async def _set_period_status(db: AsyncSession, period_id: uuid.UUID, request: Request, close: bool):
    p = await db.get(AccountingPeriod, period_id)
    if not p:
        raise HTTPException(status_code=404, detail="Accounting period not found")
    if not close and p.fiscal_year_id:
        fy = await db.get(FiscalYear, p.fiscal_year_id)
        if fy and fy.is_closed:
            raise HTTPException(status_code=409, detail=f"Reopen the fiscal year '{fy.label}' first.")
    p.status = "CLOSED" if close else "OPEN"
    p.closed_at = utcnow() if close else None
    p.closed_by_id = get_acting_user_id(request) if close else None
    db.add(_audit(p.shop_id, get_acting_user_id(request), "PERIOD_CLOSED" if close else "PERIOD_REOPENED",
                  "accounting_periods", p.id, p.period_name))
    await db.commit()
    return {"success": True, "message": f"Period '{p.period_name}' {'closed. Postings dated in it are now blocked' if close else 'reopened'}."}


@router.post("/periods/{period_id}/close")
async def close_period(period_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    return await _set_period_status(db, period_id, request, close=True)


@router.post("/periods/{period_id}/reopen")
async def reopen_period(period_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    return await _set_period_status(db, period_id, request, close=False)


# ---------------------------------------------------------------------------
# Journals
# ---------------------------------------------------------------------------
class ManualJournalRequest(BaseModel):
    entry_date: Optional[datetime] = None
    description: str = Field(..., min_length=2, max_length=500)
    debit_account_id: uuid.UUID
    credit_account_id: uuid.UUID
    amount: Decimal
    reference_number: Optional[str] = Field(None, max_length=100)
    cost_center_id: Optional[uuid.UUID] = None
    backdated_reason: Optional[str] = Field(None, max_length=500)

    @field_validator("amount")
    @classmethod
    def _positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be greater than zero.")
        return v.quantize(CENT)


def _backdated(entry_date: datetime) -> bool:
    return (utcnow() - entry_date).days > 1


@router.post("/journal-entries", status_code=201)
async def create_manual_journal(req: ManualJournalRequest, request: Request, shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    if req.debit_account_id == req.credit_account_id:
        raise HTTPException(status_code=400, detail="Debit and credit accounts must be different.")
    accs = await _own_accounts(db, shop_id, {req.debit_account_id, req.credit_account_id})
    when = to_naive_utc(req.entry_date) or utcnow()
    batch = uuid.uuid4()
    ref = (req.reference_number or "").strip() or f"JV-{when:%Y%m%d}-{secrets.token_hex(2).upper()}"
    e = pair(shop_id, when, req.description.strip(), accs[req.debit_account_id], accs[req.credit_account_id], req.amount,
             "manual", batch, ref, get_acting_user_id(request))
    e.cost_center_id = req.cost_center_id
    e.is_backdated = _backdated(when)
    e.backdated_reason = req.backdated_reason if e.is_backdated else None
    await add_entries(db, shop_id, [e])
    await db.commit()
    return {"success": True, "entry": {"id": str(e.id), "entryDate": e.entry_date.isoformat(), "description": e.description,
                                       "amount": str(e.amount), "reference": ref}}


class JournalLine(BaseModel):
    account_id: uuid.UUID
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    line_description: Optional[str] = Field(None, max_length=500)


class CompoundJournalRequest(BaseModel):
    entry_date: Optional[datetime] = None
    description: str = Field("Journal entry", max_length=500)
    reference_number: Optional[str] = Field(None, max_length=100)
    cost_center_id: Optional[uuid.UUID] = None
    backdated_reason: Optional[str] = Field(None, max_length=500)
    lines: List[JournalLine] = Field(..., min_length=2, max_length=100)


@router.post("/compound-journal-entries", status_code=201)
async def create_compound_journal(req: CompoundJournalRequest, request: Request, shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    debits, credits = [], []
    for ln in req.lines:
        d, c = ln.debit_amount.quantize(CENT), ln.credit_amount.quantize(CENT)
        if d < 0 or c < 0 or (d > 0 and c > 0):
            raise HTTPException(status_code=400, detail="Each line needs either a debit or a credit amount (not both, not negative).")
        if d > 0:
            debits.append([ln, d])
        elif c > 0:
            credits.append([ln, c])
    total_d = sum((d for _, d in debits), Decimal("0"))
    total_c = sum((c for _, c in credits), Decimal("0"))
    if not debits or not credits:
        raise HTTPException(status_code=400, detail="A journal needs at least one debit and one credit.")
    if total_d != total_c:
        raise HTTPException(status_code=400, detail=f"Debits ({total_d:,.2f}) must equal credits ({total_c:,.2f}).")

    accs = await _own_accounts(db, shop_id, {ln.account_id for ln in req.lines})
    when = to_naive_utc(req.entry_date) or utcnow()
    batch = uuid.uuid4()
    ref = (req.reference_number or "").strip() or f"JV-{when:%Y%m%d}-{secrets.token_hex(2).upper()}"
    user = get_acting_user_id(request)

    # Split the N debits / M credits into balanced pairs (greedy matching keeps every line's total exact).
    entries, i, j = [], 0, 0
    while i < len(debits) and j < len(credits):
        amt = min(debits[i][1], credits[j][1])
        d_line, c_line = debits[i][0], credits[j][0]
        if d_line.account_id == c_line.account_id:
            raise HTTPException(status_code=400, detail="The same account cannot be debited and credited in one journal.")
        e = pair(shop_id, when, d_line.line_description or c_line.line_description or req.description,
                 accs[d_line.account_id], accs[c_line.account_id], amt, "manual", batch, ref, user)
        e.cost_center_id = req.cost_center_id
        e.is_backdated = _backdated(when)
        e.backdated_reason = req.backdated_reason if e.is_backdated else None
        entries.append(e)
        debits[i][1] -= amt
        credits[j][1] -= amt
        if debits[i][1] == 0:
            i += 1
        if credits[j][1] == 0:
            j += 1

    await add_entries(db, shop_id, entries)
    await db.commit()
    return {"success": True, "count": len(entries), "reference": ref, "batchId": str(batch), "total": str(total_d)}


class OpeningLine(BaseModel):
    accountId: uuid.UUID = Field(..., validation_alias=AliasChoices("accountId", "account_id"))
    debit: Decimal = Field(Decimal("0"), validation_alias=AliasChoices("debit", "debitAmount", "debit_amount"))
    credit: Decimal = Field(Decimal("0"), validation_alias=AliasChoices("credit", "creditAmount", "credit_amount"))


class OpeningBalancesRequest(BaseModel):
    as_of_date: Optional[datetime] = None
    lines: List[OpeningLine] = Field(default_factory=list, max_length=500)


@router.post("/opening-balances", status_code=201)
async def set_opening_balances(req: OpeningBalancesRequest, request: Request, shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    """Replace the workspace's opening balances. Each line is offset against Opening Balances Equity (3200)."""
    when = to_naive_utc(req.as_of_date) or utcnow()
    acc = await account_map(db, shop_id)
    equity = acc[OPENING_EQUITY]
    lines = [ln for ln in req.lines if ln.accountId != equity.id and (ln.debit > 0 or ln.credit > 0)]
    if any(ln.debit < 0 or ln.credit < 0 or (ln.debit > 0 and ln.credit > 0) for ln in req.lines):
        raise HTTPException(status_code=400, detail="Each account needs either a debit or a credit balance.")
    accs = await _own_accounts(db, shop_id, {ln.accountId for ln in lines}) if lines else {}

    # One opening-balance batch per workspace: replace any previous one.
    previous = (
        await db.execute(select(JournalEntry.source_id).where(JournalEntry.shop_id == shop_id, JournalEntry.source_type == "opening_balance").distinct())
    ).scalars().all()
    for src in previous:
        if src:
            await remove_source_entries(db, shop_id, src)
    await db.execute(delete(JournalEntry).where(JournalEntry.shop_id == shop_id, JournalEntry.source_type == "opening_balance"))

    batch, user = uuid.uuid4(), get_acting_user_id(request)
    entries = []
    for ln in lines:
        a = accs[ln.accountId]
        if ln.debit > 0:
            entries.append(pair(shop_id, when, f"Opening balance: {a.name}", a, equity, ln.debit.quantize(CENT), "opening_balance", batch, "OPENING-BALANCES", user))
        else:
            entries.append(pair(shop_id, when, f"Opening balance: {a.name}", equity, a, ln.credit.quantize(CENT), "opening_balance", batch, "OPENING-BALANCES", user))
    await add_entries(db, shop_id, entries)
    total_d = sum((ln.debit for ln in lines), Decimal("0"))
    total_c = sum((ln.credit for ln in lines), Decimal("0"))
    await db.commit()
    return {
        "success": True,
        "createdCount": len(entries),
        "totalDebits": str(total_d),
        "totalCredits": str(total_c),
        "equityDifference": str(total_d - total_c),
    }


# ---------------------------------------------------------------------------
# Chart of accounts: safe delete
# ---------------------------------------------------------------------------
@router.delete("/{account_id}")
async def delete_account(account_id: uuid.UUID, shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    account = await db.get(ChartOfAccount, account_id)
    if not account or account.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.is_system:
        raise HTTPException(status_code=400, detail="System accounts cannot be deleted.")
    used = (
        await db.execute(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.shop_id == shop_id,
                (JournalEntry.debit_account_id == account_id) | (JournalEntry.credit_account_id == account_id),
            )
        )
    ).scalar()
    if used:
        raise HTTPException(status_code=409, detail=f"This account has {used} journal entries and cannot be deleted.")
    await db.delete(account)
    await db.commit()
    return {"success": True, "message": "Account deleted successfully"}


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------
@router.get("/integrity")
async def ledger_integrity(shop_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)):
    """Is the ledger in step with documents, payments, expenses and income?"""
    return await integrity_report(db, shop_id)


@router.post("/integrity/repair")
async def ledger_repair(req: ShopBody, db: AsyncSession = Depends(get_db)):
    """Re-post every record the integrity check flags (records in closed periods are skipped and listed)."""
    return await repair_ledger(db, req.shop_id)
