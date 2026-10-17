# app/modules/documents/loyalty.py
"""
Loyalty points and member discounts on sales documents.

Accounting: a redemption is a discount given at the point of sale, so it lowers the taxable value.
`points x point_value_kes` comes off the document total and VAT is charged on what is left. A member
tier discount works the same way. Lines keep their list prices; on the document, `sub_total` is the
undiscounted net and `tax_amount` / `grand_total` are after the discount, so the net discount is
`sub_total + tax_amount - grand_total`. Posting books it to Loyalty Discounts (contra revenue).
Points still to be redeemed are not carried as a liability.

Points ledger: each row a document causes carries `source_document_id`, and `sync_document_loyalty`
makes those rows match the document's state (idempotent), like stock:
  REDEEM  negative  points spent on the document, from when it is issued
  EARN    positive  points earned once the sale settles (invoice Paid, stand-alone cash receipt)
  VOID    signed    corrections: negative takes earned points back, positive returns redeemed points
Credit notes reduce both in proportion; voiding returns everything.

Tiers follow qualifying spend: what the client paid on settled sales (Paid invoices and stand-alone
cash receipts, less credit notes). A client holds the highest tier whose `min_spend_kes` they reach,
and moves down again if credit notes take them below it. A sale earns at the multiplier of the tier
the client held before it, so recalculating an old sale never changes its points.

Expiry: with `points_expiry_days` set, points not used within that many days of being credited lapse
(first in, first out) as EXPIRE rows, run nightly by `expire_points`.
"""
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetime_utils import utcnow
from app.models.document import Document
from app.models.operations import ClientLoyaltyAccount, LoyaltyLedger, LoyaltyProgram, MembershipTier
from app.modules.documents.workflow import WorkflowError

logger = logging.getLogger("mannabooks.loyalty")

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
POINTS_MODES = ("POINTS_ONLY", "HYBRID")
REDEEM_TYPES = ("INVOICE", "RECEIPT")  # points are spent and earned on these
DISCOUNT_TYPES = ("QUOTATION", "INVOICE", "RECEIPT")  # member tier discount
VAT_RATES = {"V_16": Decimal("0.16")}


async def get_program(db: AsyncSession, shop_id: uuid.UUID) -> Optional[LoyaltyProgram]:
    return (await db.execute(select(LoyaltyProgram).where(LoyaltyProgram.shop_id == shop_id))).scalars().first()


def program_on(p: Optional[LoyaltyProgram]) -> bool:
    return bool(p and p.is_enabled and p.engine_mode != "OFF")


def points_on(p: Optional[LoyaltyProgram]) -> bool:
    return bool(p and p.is_enabled and p.engine_mode in POINTS_MODES)


async def get_account(
    db: AsyncSession, shop_id: uuid.UUID, client_id: uuid.UUID, create: bool = False, lock: bool = False
) -> Optional[ClientLoyaltyAccount]:
    q = select(ClientLoyaltyAccount).where(
        ClientLoyaltyAccount.shop_id == shop_id, ClientLoyaltyAccount.client_id == client_id
    )
    if lock:
        q = q.with_for_update()
    acc = (await db.execute(q)).scalars().first()
    if not acc and create:
        acc = ClientLoyaltyAccount(
            shop_id=shop_id,
            client_id=client_id,
            member_number=f"MEM-{secrets.token_hex(3).upper()}",
            current_points=0,
            lifetime_points=0,
            wallet_balance_kes=ZERO,
        )
        db.add(acc)
        await db.flush()
    return acc


async def _tier(db: AsyncSession, acc: Optional[ClientLoyaltyAccount]) -> Optional[MembershipTier]:
    return await db.get(MembershipTier, acc.tier_id) if acc and acc.tier_id else None


# ---------------------------------------------------------------------------
# Pricing: totals after a member discount and a points redemption
# ---------------------------------------------------------------------------
@dataclass
class Priced:
    sub_total: Decimal  # undiscounted net of the lines
    tax_amount: Decimal  # VAT after the discount
    grand_total: Decimal
    discount: Decimal  # what the client saves, VAT inclusive
    points_redeemed: int


def undiscounted(lines: Iterable[tuple[Decimal, Decimal]]) -> tuple[Decimal, Decimal, Decimal]:
    """(sub_total, tax, grand_total) from (net, tax) per line, ignoring any loyalty discount."""
    sub = tax = ZERO
    for net, t in lines:
        sub += Decimal(str(net))
        tax += Decimal(str(t))
    return sub, tax, sub + tax


async def price_document(
    db: AsyncSession,
    shop_id: uuid.UUID,
    doc_type: str,
    client_id: Optional[uuid.UUID],
    lines: list[tuple[Decimal, Decimal, str]],  # (net, tax, tax_type) per line
    points: int = 0,
    tier_discount: bool = False,
) -> Priced:
    """Validate a redemption / member discount and work out the document totals."""
    sub, tax, gross = undiscounted((n, t) for n, t, _ in lines)
    points = int(points or 0)
    if points < 0:
        raise WorkflowError("Points to redeem can't be negative.")
    if not points and not tier_discount:
        return Priced(sub, tax, gross, ZERO, 0)
    if not client_id:
        raise WorkflowError("Choose a client to redeem points or apply a member discount.")
    program = await get_program(db, shop_id)
    if not program_on(program):
        raise WorkflowError("The loyalty programme is switched off.")
    acc = await get_account(db, shop_id, client_id)

    # Per line: [net, tax, rate] after each discount step.
    rows = [[Decimal(str(n)), Decimal(str(t)), VAT_RATES.get(tt, ZERO)] for n, t, tt in lines]
    discount = ZERO

    if tier_discount:
        if doc_type not in DISCOUNT_TYPES:
            raise WorkflowError("Member discounts apply to quotations, invoices and cash receipts.")
        tier = await _tier(db, acc)
        pct = Decimal(str(tier.discount_percent)) if tier else ZERO
        if pct <= 0:
            raise WorkflowError("This client's membership tier has no discount.")
        for r in rows:
            dn = (r[0] * pct / 100).quantize(CENT, ROUND_HALF_UP)
            dt = (r[1] * pct / 100).quantize(CENT, ROUND_HALF_UP)
            r[0] -= dn
            r[1] -= dt
            discount += dn + dt

    if points:
        if doc_type not in REDEEM_TYPES:
            raise WorkflowError("Points can be redeemed on invoices and cash receipts.")
        if not points_on(program):
            raise WorkflowError("This loyalty programme doesn't use points.")
        point_value = Decimal(str(program.point_value_kes or 0))
        if point_value <= 0:
            raise WorkflowError("Set a value per point in the loyalty settings before redeeming.")
        balance = acc.current_points if acc else 0
        if balance < program.min_redeem_points:
            raise WorkflowError(
                f"Redeeming needs a balance of at least {program.min_redeem_points} points; the client has {balance}."
            )
        if points > balance:
            raise WorkflowError(f"The client has {balance} points, not enough to redeem {points}.")
        value = (points * point_value).quantize(CENT, ROUND_HALF_UP)
        remaining = sum((r[0] + r[1] for r in rows), ZERO)
        if value > remaining:
            most = int((remaining / point_value).to_integral_value(ROUND_DOWN))
            raise WorkflowError(
                f"{points} points are worth {value:,.2f}, more than the total ({remaining:,.2f}). "
                f"Redeem at most {most} points."
            )
        # Spread the redemption over the lines by value; each share is VAT inclusive.
        left = value
        live = [r for r in rows if r[0] + r[1] > 0]
        for i, r in enumerate(live):
            share = left if i == len(live) - 1 else (value * (r[0] + r[1]) / remaining).quantize(CENT, ROUND_HALF_UP)
            share = min(share, r[0] + r[1])
            left -= share
            vat = (share * r[2] / (1 + r[2])).quantize(CENT, ROUND_HALF_UP)
            r[0] -= share - vat
            r[1] -= vat
        discount += value

    tax_after = sum((r[1] for r in rows), ZERO)
    return Priced(sub, tax_after, gross - discount, discount, points)


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------
def _settled_sale():
    return or_(
        and_(Document.type == "INVOICE", Document.status == "PAID"),
        and_(Document.type == "RECEIPT", Document.parent_document_id.is_(None), Document.status.in_(["PAID", "ISSUED"])),
    )


async def qualifying_spend(
    db: AsyncSession, shop_id: uuid.UUID, client_id: uuid.UUID, before: Optional[Document] = None
) -> Decimal:
    """What the client paid on settled sales, less credit notes; `before` limits it to earlier sales."""
    where = [Document.shop_id == shop_id, Document.client_id == client_id, _settled_sale()]
    if before is not None:
        where += [Document.id != before.id, Document.issue_date <= before.issue_date]
    sales = (await db.execute(select(func.coalesce(func.sum(Document.grand_total), ZERO)).where(*where))).scalar()
    cn = aliased(Document)
    credits = (
        await db.execute(
            select(func.coalesce(func.sum(cn.grand_total), ZERO)).where(
                cn.parent_document_id.in_(select(Document.id).where(*where)),
                cn.type == "CREDIT_NOTE",
                cn.status.in_(["ISSUED", "PAID"]),
            )
        )
    ).scalar()
    return max(ZERO, Decimal(str(sales or 0)) - Decimal(str(credits or 0)))


async def tier_for_spend(db: AsyncSession, shop_id: uuid.UUID, spend: Decimal) -> Optional[MembershipTier]:
    return (
        await db.execute(
            select(MembershipTier)
            .where(MembershipTier.shop_id == shop_id, MembershipTier.min_spend_kes <= spend)
            .order_by(MembershipTier.min_spend_kes.desc(), MembershipTier.display_order.desc())
            .limit(1)
        )
    ).scalars().first()


async def refresh_tier(db: AsyncSession, shop_id: uuid.UUID, client_id: uuid.UUID) -> Optional[ClientLoyaltyAccount]:
    """Put the client in the tier their qualifying spend reaches (up or down)."""
    tier = await tier_for_spend(db, shop_id, await qualifying_spend(db, shop_id, client_id))
    acc = await get_account(db, shop_id, client_id, create=tier is not None)
    new_id = tier.id if tier else None
    if acc and acc.tier_id != new_id:
        acc.tier_id = new_id
        await db.flush()
    return acc


# ---------------------------------------------------------------------------
# Points ledger
# ---------------------------------------------------------------------------
def _earns(doc: Document) -> bool:
    t, s = str(doc.type), str(doc.status)
    if t == "INVOICE":
        return s == "PAID"
    if t == "RECEIPT":
        return doc.parent_document_id is None and s in ("PAID", "ISSUED")
    return False


async def _credited(db: AsyncSession, doc: Document) -> Decimal:
    v = (
        await db.execute(
            select(func.coalesce(func.sum(Document.grand_total), ZERO)).where(
                Document.parent_document_id == doc.id,
                Document.type == "CREDIT_NOTE",
                Document.status.in_(["ISSUED", "PAID"]),
            )
        )
    ).scalar()
    return Decimal(str(v or 0))


async def sync_document_loyalty(
    db: AsyncSession, doc: Document, user_id: Optional[uuid.UUID] = None, strict: bool = True
) -> None:
    """
    Make the points this document spent and earned match its state (idempotent).

    `strict`: an issue that would overdraw the client's points raises WorkflowError. Background
    refreshes pass False and only log it.
    """
    if not doc.client_id or str(doc.type) not in REDEEM_TYPES:
        return
    rows = (
        await db.execute(
            select(LoyaltyLedger.movement_type, LoyaltyLedger.points_delta).where(
                LoyaltyLedger.source_document_id == doc.id
            )
        )
    ).all()
    earned = spent = 0
    for mtype, delta in rows:
        if mtype == "EARN" or (mtype == "VOID" and delta < 0):
            earned += delta
        elif mtype == "REDEEM" or (mtype == "VOID" and delta > 0):
            spent -= delta

    want_earned = want_spent = 0
    credited = ZERO
    redeem = int(doc.loyalty_points_redeemed or 0)
    if str(doc.status) not in ("DRAFT", "CANCELLED"):
        total = Decimal(str(doc.grand_total or 0))
        credited = await _credited(db, doc) if str(doc.type) == "INVOICE" else ZERO
        kept = (total - credited) / total if total > 0 else Decimal(1)
        kept = max(Decimal(0), min(Decimal(1), kept))
        want_spent = int((redeem * kept).to_integral_value(ROUND_HALF_UP))
        if _earns(doc):
            program = await get_program(db, doc.shop_id)
            rate = Decimal(str(program.earn_rate_kes or 0)) if program else ZERO
            if points_on(program) and rate > 0:
                # The tier the client held before this sale, so its points never shift later.
                tier = await tier_for_spend(db, doc.shop_id, await qualifying_spend(db, doc.shop_id, doc.client_id, doc))
                mult = Decimal(str(tier.points_multiplier)) if tier and tier.points_multiplier else Decimal(1)
                eligible = max(ZERO, total - credited)
                want_earned = int((eligible / rate * mult).to_integral_value(ROUND_DOWN))
            else:
                want_earned = earned  # programme switched off: keep what was earned until the sale is undone

    if want_earned == earned and want_spent == spent:
        doc.loyalty_points_earned = earned
        if program_on(await get_program(db, doc.shop_id)):
            await refresh_tier(db, doc.shop_id, doc.client_id)
        return

    acc = await get_account(db, doc.shop_id, doc.client_id, create=True, lock=True)
    label = f"{str(doc.type).replace('_', ' ').title()} {doc.doc_number}"
    why = "voided" if str(doc.status) == "CANCELLED" else "credit note" if credited > 0 else "no longer paid"
    moves: list[tuple[str, int, str]] = []
    if want_spent < spent:
        moves.append(("VOID", spent - want_spent, f"Points returned: {label} ({why})"))
    if want_earned > earned:
        moves.append(("EARN", want_earned - earned, f"Earned on {label}"))
    if want_spent > spent:
        need = want_spent - spent
        balance = acc.current_points + sum(d for _, d, _ in moves)
        if need > balance:
            msg = f"The client has {balance} points, not enough to redeem {need} on {doc.doc_number}."
            if strict:
                raise WorkflowError(msg)
            logger.warning("Loyalty: %s", msg)
            want_spent = spent
        else:
            moves.append(("REDEEM", -need, f"Redeemed on {label}"))
    if want_earned < earned:
        moves.append(("VOID", want_earned - earned, f"Earned points taken back: {label} ({why})"))

    for mtype, delta, note in moves:
        acc.current_points += delta
        if mtype == "EARN" or (mtype == "VOID" and delta < 0):
            acc.lifetime_points = max(0, acc.lifetime_points + delta)
        db.add(
            LoyaltyLedger(
                shop_id=doc.shop_id,
                account_id=acc.id,
                movement_type=mtype,
                points_delta=delta,
                running_points_balance=acc.current_points,
                source_document_id=doc.id,
                notes=note,
                created_by_id=user_id,
            )
        )
    doc.loyalty_points_earned = want_earned
    await db.flush()
    if program_on(await get_program(db, doc.shop_id)):
        await refresh_tier(db, doc.shop_id, doc.client_id)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------
async def expire_points(db: AsyncSession, shop_id: Optional[uuid.UUID] = None, now: Optional[datetime] = None) -> dict:
    """
    Lapse points older than the programme's `points_expiry_days` (first in, first out) and refresh
    tiers. Idempotent: what has lapsed is recorded as EXPIRE rows, which count as points used.
    """
    now = now or utcnow()
    q = select(LoyaltyProgram).where(LoyaltyProgram.is_enabled == True)  # noqa: E712
    if shop_id:
        q = q.where(LoyaltyProgram.shop_id == shop_id)
    expired_points = accounts_touched = tiers_changed = 0
    for program in (await db.execute(q)).scalars().all():
        accounts = (
            await db.execute(
                select(ClientLoyaltyAccount).where(ClientLoyaltyAccount.shop_id == program.shop_id).with_for_update()
            )
        ).scalars().all()
        days = int(program.points_expiry_days or 0)
        cutoff = now - timedelta(days=days)
        for acc in accounts:
            before = acc.tier_id
            if program.engine_mode != "OFF":
                await refresh_tier(db, program.shop_id, acc.client_id)
            tiers_changed += acc.tier_id != before
            if days <= 0 or program.engine_mode not in POINTS_MODES or acc.current_points <= 0:
                continue
            old_credits, used = (
                await db.execute(
                    select(
                        func.coalesce(func.sum(LoyaltyLedger.points_delta).filter(
                            LoyaltyLedger.points_delta > 0, LoyaltyLedger.created_at <= cutoff), 0),
                        func.coalesce(func.sum(LoyaltyLedger.points_delta).filter(LoyaltyLedger.points_delta < 0), 0),
                    ).where(LoyaltyLedger.account_id == acc.id)
                )
            ).one()
            lapse = min(int(old_credits) + int(used), acc.current_points)  # `used` is negative
            if lapse <= 0:
                continue
            acc.current_points -= lapse
            db.add(
                LoyaltyLedger(
                    shop_id=acc.shop_id,
                    account_id=acc.id,
                    movement_type="EXPIRE",
                    points_delta=-lapse,
                    running_points_balance=acc.current_points,
                    notes=f"{lapse} points not used within {days} days",
                )
            )
            expired_points += lapse
            accounts_touched += 1
    await db.commit()
    return {"success": True, "expiredPoints": expired_points, "accountsExpired": accounts_touched,
            "tiersChanged": tiers_changed}


__all__ = [
    "price_document", "undiscounted", "sync_document_loyalty", "get_program", "get_account", "Priced",
    "qualifying_spend", "tier_for_spend", "refresh_tier", "expire_points",
]
