# app/modules/documents/workflow.py
"""
Document workflow: which status changes each document type allows, and the rules that keep money,
stock and status consistent.

Two kinds of status:
  * chosen by people through actions (issue, void, accept, receive...), validated here;
  * set by the system from money and dates: for invoices and debit notes, Partially paid / Paid /
    Overdue follow from recorded payments, credit notes and the due date (`refresh_settlement`);
    quotations expire after their validity date.

Only drafts can be edited or deleted. Issued documents are corrected with credit/debit notes or voided.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetime_utils import to_naive_utc, utcnow
from app.models.document import Document, DocumentNote
from app.models.payment import DocumentPayment

ZERO = Decimal("0.00")

SETTLED_TYPES = ("INVOICE", "DEBIT_NOTE")  # balance-driven: payments and credit notes settle them
OPEN_STATUSES = ("ISSUED", "PARTIALLY_PAID", "OVERDUE")
SYSTEM_STATUSES = ("PARTIALLY_PAID", "OVERDUE", "PAID")  # for SETTLED_TYPES only


class WorkflowError(Exception):
    """A change the document's workflow does not allow (HTTP 409)."""


@dataclass(frozen=True)
class Transition:
    action: str
    label: str
    sources: tuple
    target: str
    tone: str = "default"  # primary | default | danger
    confirm: Optional[str] = None
    needs_reason: bool = False


def _t(*args, **kw) -> Transition:
    return Transition(*args, **kw)


_VOID_CONFIRM = "Voiding reverses its ledger entries and stock movements. The document stays on record as Cancelled."
_PURCHASE_ORDER = [
    _t("issue", "Mark as sent", ("DRAFT",), "ISSUED", "primary"),
    _t("confirm", "Supplier confirmed", ("ISSUED",), "CONFIRMED"),
    _t("receive", "Mark goods received", ("ISSUED", "CONFIRMED"), "RECEIVED", "primary"),
    _t("cancel", "Cancel order", ("DRAFT", "ISSUED", "CONFIRMED"), "CANCELLED", "danger",
       "Cancel this order? It stays on record as Cancelled.", True),
]

TRANSITIONS: dict[str, list[Transition]] = {
    "QUOTATION": [
        _t("issue", "Mark as sent", ("DRAFT",), "ISSUED", "primary"),
        _t("accept", "Mark accepted", ("ISSUED",), "CONFIRMED", "primary"),
        _t("decline", "Mark declined", ("ISSUED",), "CANCELLED", "danger", "Record that the client declined?", True),
        _t("revise", "Revise (back to draft)", ("ISSUED", "EXPIRED"), "DRAFT"),
        _t("cancel", "Cancel quotation", ("DRAFT", "ISSUED", "EXPIRED"), "CANCELLED", "danger",
           "Cancel this quotation?", True),
    ],
    "INVOICE": [
        _t("issue", "Issue invoice", ("DRAFT",), "ISSUED", "primary",
           "Issuing posts the invoice to the ledger and locks its amounts. Corrections then need a credit or debit note."),
        _t("void", "Void invoice", ("ISSUED", "OVERDUE"), "CANCELLED", "danger", _VOID_CONFIRM, True),
    ],
    "DEBIT_NOTE": [
        _t("issue", "Issue debit note", ("DRAFT",), "ISSUED", "primary",
           "Issuing posts the debit note and locks its amounts."),
        _t("void", "Void debit note", ("ISSUED", "OVERDUE"), "CANCELLED", "danger", _VOID_CONFIRM, True),
    ],
    "CREDIT_NOTE": [
        _t("issue", "Issue credit note", ("DRAFT",), "ISSUED", "primary"),
        _t("void", "Void credit note", ("ISSUED",), "CANCELLED", "danger", _VOID_CONFIRM, True),
    ],
    "RECEIPT": [
        _t("issue", "Issue receipt", ("DRAFT",), "PAID", "primary"),
        _t("void", "Void receipt", ("PAID", "ISSUED"), "CANCELLED", "danger", _VOID_CONFIRM, True),
    ],
    "LPO": _PURCHASE_ORDER,
    "PO": _PURCHASE_ORDER,
    "LSO": [
        _t("issue", "Mark as sent", ("DRAFT",), "ISSUED", "primary"),
        _t("confirm", "Supplier confirmed", ("ISSUED",), "CONFIRMED"),
        _t("receive", "Sign off service", ("ISSUED", "CONFIRMED"), "RECEIVED", "primary"),
        _t("cancel", "Cancel order", ("DRAFT", "ISSUED", "CONFIRMED"), "CANCELLED", "danger",
           "Cancel this service order?", True),
    ],
    "DELIVERY_NOTE": [
        _t("issue", "Mark dispatched", ("DRAFT",), "ISSUED", "primary"),
        _t("receive", "Mark delivered", ("ISSUED",), "RECEIVED", "primary"),
        _t("cancel", "Cancel delivery note", ("DRAFT", "ISSUED"), "CANCELLED", "danger", None, True),
    ],
    "GOODS_RECEIVED_NOTE": [
        _t("receive", "Confirm goods received", ("DRAFT", "ISSUED"), "RECEIVED", "primary"),
        _t("cancel", "Cancel note", ("DRAFT", "ISSUED"), "CANCELLED", "danger", None, True),
    ],
    "SERVICE_COMPLETION_NOTE": [
        _t("receive", "Sign off completion", ("DRAFT", "ISSUED"), "RECEIVED", "primary"),
        _t("cancel", "Cancel note", ("DRAFT", "ISSUED"), "CANCELLED", "danger", None, True),
    ],
    "PAYMENT_VOUCHER": [
        _t("approve", "Approve voucher", ("DRAFT",), "CONFIRMED", "primary"),
        _t("pay", "Mark paid", ("CONFIRMED",), "PAID", "primary"),
        _t("cancel", "Cancel voucher", ("DRAFT", "CONFIRMED"), "CANCELLED", "danger", None, True),
    ],
    "PAYROLL_VOUCHER": [],  # managed by the payroll module (approve / pay endpoints)
}

# Statuses each type can hold (used to validate the status sent on create).
TYPE_STATUSES: dict[str, set[str]] = {
    t: {"DRAFT"} | {tr.target for tr in trs} | {s for tr in trs for s in tr.sources}
    for t, trs in TRANSITIONS.items()
}
for _t_name in SETTLED_TYPES:
    TYPE_STATUSES[_t_name] |= set(SYSTEM_STATUSES)
TYPE_STATUSES["QUOTATION"].add("EXPIRED")
TYPE_STATUSES["PAYROLL_VOUCHER"] = {"DRAFT", "CONFIRMED", "PAID", "CANCELLED"}
TYPE_STATUSES["CREDIT_NOTE"].add("PAID")  # refunded in cash

# source type -> {target type: (label, source statuses allowed)}
_LIVE = ("ISSUED", "PARTIALLY_PAID", "OVERDUE", "PAID", "CONFIRMED", "RECEIVED")
CONVERSIONS: dict[str, dict[str, tuple[str, tuple]]] = {
    "QUOTATION": {"INVOICE": ("Convert to invoice", ("ISSUED", "CONFIRMED"))},
    "INVOICE": {
        "RECEIPT": ("Issue receipt", ("PAID",)),
        "DELIVERY_NOTE": ("Generate delivery note", ("ISSUED", "PARTIALLY_PAID", "OVERDUE", "PAID")),
        "DEBIT_NOTE": ("Raise debit note", ("ISSUED", "PARTIALLY_PAID", "OVERDUE", "PAID")),
    },
    "RECEIPT": {"DELIVERY_NOTE": ("Generate delivery note", ("PAID", "ISSUED"))},
    "DELIVERY_NOTE": {"RECEIPT": ("Issue receipt", ("ISSUED", "RECEIVED"))},
    "LPO": {"GOODS_RECEIVED_NOTE": ("Goods received note", _LIVE), "PAYMENT_VOUCHER": ("Payment voucher", _LIVE)},
    "PO": {"GOODS_RECEIVED_NOTE": ("Goods received note", _LIVE), "PAYMENT_VOUCHER": ("Payment voucher", _LIVE)},
    "LSO": {"SERVICE_COMPLETION_NOTE": ("Service completion note", _LIVE), "PAYMENT_VOUCHER": ("Payment voucher", _LIVE)},
    "GOODS_RECEIVED_NOTE": {"PAYMENT_VOUCHER": ("Payment voucher", ("RECEIVED", "ISSUED"))},
    "SERVICE_COMPLETION_NOTE": {"PAYMENT_VOUCHER": ("Payment voucher", ("RECEIVED", "ISSUED"))},
}


# ---------------------------------------------------------------------------
# Money: paid, credited, balance
# ---------------------------------------------------------------------------
@dataclass
class Figures:
    total: Decimal
    paid: Decimal
    credited: Decimal  # credit notes that reduce what the client owes (not cash refunds)
    credit_capacity: Decimal  # how much more can still be credited
    payment_count: int

    @property
    def balance(self) -> Decimal:
        return max(ZERO, self.total - self.paid - self.credited)


async def figures(db: AsyncSession, doc: Document) -> Figures:
    total = Decimal(str(doc.grand_total or 0))
    paid, count = (
        await db.execute(
            select(func.coalesce(func.sum(DocumentPayment.amount), ZERO), func.count(DocumentPayment.id)).where(
                DocumentPayment.document_id == doc.id
            )
        )
    ).one()
    rows = (
        await db.execute(
            select(Document.status, func.coalesce(func.sum(Document.grand_total), ZERO))
            .where(
                Document.parent_document_id == doc.id,
                Document.type == "CREDIT_NOTE",
                Document.status.in_(["ISSUED", "PAID"]),
            )
            .group_by(Document.status)
        )
    ).all()
    by_status = {s: Decimal(str(v)) for s, v in rows}
    credited = by_status.get("ISSUED", ZERO)
    all_credits = credited + by_status.get("PAID", ZERO)
    paid = Decimal(str(paid))
    if count == 0 and str(doc.status) == "PAID":
        # Older invoices were marked paid without payment records; treat them as settled.
        paid = max(ZERO, total - credited)
    return Figures(total, paid, credited, max(ZERO, total - all_credits), int(count))


def settlement_status(doc: Document, f: Figures, now=None) -> str:
    now = now or utcnow()
    if f.balance <= ZERO:
        return "PAID"
    due = to_naive_utc(doc.due_date)
    if due and due < now:
        return "OVERDUE"
    return "PARTIALLY_PAID" if f.paid > ZERO else "ISSUED"


async def refresh_settlement(db: AsyncSession, doc: Document, now=None) -> bool:
    """
    Set Partially paid / Paid / Overdue from payments, credits and the due date, and keep loyalty
    points in step (earned once Paid, reduced by credit notes). Returns True if the status changed.
    """
    if str(doc.type) not in SETTLED_TYPES or str(doc.status) not in OPEN_STATUSES + ("PAID",):
        return False
    new = settlement_status(doc, await figures(db, doc), now)
    changed = new != doc.status
    if changed:
        was_paid = doc.status == "PAID"
        doc.status = new
        await db.flush()
        if was_paid or new == "PAID":
            await _sync_gl(db, doc)  # the "settled without payments" entry depends on PAID
    await _sync_loyalty(db, doc, strict=False)
    return changed


async def sweep_overdue_and_expired(db: AsyncSession, shop_id: Optional[uuid.UUID] = None) -> tuple[int, int]:
    """Bulk: open invoices past their due date become Overdue; sent quotations past validity become Expired."""
    now = utcnow()
    scope = [Document.shop_id == shop_id] if shop_id else []
    overdue = await db.execute(
        update(Document)
        .where(
            Document.type.in_(SETTLED_TYPES),
            Document.status.in_(["ISSUED", "PARTIALLY_PAID"]),
            Document.due_date.isnot(None),
            Document.due_date < now,
            *scope,
        )
        .values(status="OVERDUE")
        .execution_options(synchronize_session=False)
    )
    expired = await db.execute(
        update(Document)
        .where(
            Document.type == "QUOTATION",
            Document.status == "ISSUED",
            Document.due_date.isnot(None),
            Document.due_date < now,
            *scope,
        )
        .values(status="EXPIRED")
        .execution_options(synchronize_session=False)
    )
    return overdue.rowcount or 0, expired.rowcount or 0


# ---------------------------------------------------------------------------
# Stock: sales take stock out when they go live, voids put it back
# ---------------------------------------------------------------------------
def is_stock_sale(doc: Document) -> bool:
    """Invoices and stand-alone cash receipts move stock. Receipts for an invoice or delivery note don't."""
    t, s = str(doc.type), str(doc.status)
    if t == "INVOICE":
        return s in OPEN_STATUSES + ("PAID",)
    if t == "RECEIPT":
        return s in ("PAID", "ISSUED") and doc.parent_document_id is None
    return False


async def sync_document_stock(db: AsyncSession, doc: Document, user_id: Optional[uuid.UUID] = None) -> None:
    """
    Make this document's stock movements match its state (idempotent).

    Ledger rows for a document carry `source_document_id`. Effect on stock: SALE takes out `quantity`,
    RETURN puts back `quantity`, VOID applies its signed `quantity` (+ in / - out).
    """
    from app.models.inventory import StockLedger
    from app.models.item import Product

    rows = (
        await db.execute(
            select(StockLedger.product_id, StockLedger.movement_type, StockLedger.quantity).where(
                StockLedger.source_document_id == doc.id
            )
        )
    ).all()
    current: dict[uuid.UUID, Decimal] = {}
    for pid, mtype, qty in rows:
        q = Decimal(str(qty))
        effect = -q if mtype == "SALE" else q if mtype in ("RETURN", "VOID") else ZERO
        current[pid] = current.get(pid, ZERO) + effect

    desired: dict[uuid.UUID, Decimal] = {}
    if is_stock_sale(doc):
        for it in doc.items or []:
            if it.product_id:
                desired[it.product_id] = desired.get(it.product_id, ZERO) - Decimal(str(it.quantity))
    elif str(doc.type) == "CREDIT_NOTE" and str(doc.status) in ("ISSUED", "PAID"):
        desired = dict(current)  # restocking was decided when the credit note was issued

    for pid in set(current) | set(desired):
        diff = desired.get(pid, ZERO) - current.get(pid, ZERO)
        if diff == ZERO:
            continue
        prod = (
            await db.execute(select(Product).where(Product.id == pid, Product.shop_id == doc.shop_id).with_for_update())
        ).scalar_one_or_none()
        if not prod or (not prod.track_stock and pid not in current):
            continue
        prod.stock_quantity = Decimal(str(prod.stock_quantity or 0)) + diff
        selling = diff < ZERO and not current.get(pid)
        db.add(
            StockLedger(
                shop_id=doc.shop_id,
                product_id=pid,
                location_id=doc.location_id or prod.default_location_id,
                movement_type="SALE" if selling else "VOID",
                quantity=-diff if selling else diff,
                unit_cost=prod.cost_price or ZERO,
                running_balance=prod.stock_quantity,
                source_document_id=doc.id,
                notes=f"{'Sold on' if selling else 'Reversal for'} {doc.type.replace('_', ' ').title()} {doc.doc_number}",
                created_by_id=user_id,
            )
        )
    await db.flush()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _label(doc: Document) -> str:
    return str(doc.type).replace("_", " ").lower()


async def _sync_gl(db: AsyncSession, doc: Document) -> None:
    from app.modules.documents.service import _sync_gl as sync

    await sync(db, doc.shop_id, doc)


async def _sync_loyalty(db: AsyncSession, doc: Document, user_id: Optional[uuid.UUID] = None, strict: bool = True) -> None:
    from app.modules.documents.loyalty import sync_document_loyalty

    await sync_document_loyalty(db, doc, user_id, strict)


async def _blocked(db: AsyncSession, doc: Document, tr: Transition, f: Optional[Figures]) -> Optional[str]:
    t = str(doc.type)
    if tr.action == "void" and t in SETTLED_TYPES and f is not None:
        if f.payment_count or f.paid > ZERO:
            return "Payments are recorded against it. Remove them first, or raise a credit note instead."
        if f.credited > ZERO or f.credit_capacity < f.total:
            return "Credit notes exist against it. Void them first."
    if tr.action == "void" and doc.kra_cu_invoice_number and t in ("INVOICE", "DEBIT_NOTE", "RECEIPT"):
        return "It is signed by KRA eTIMS. Raise a credit note instead."
    if tr.action == "issue" and Decimal(str(doc.grand_total or 0)) <= ZERO and t not in ("DELIVERY_NOTE",):
        return "Add at least one line with a value before issuing."
    if tr.action == "void" and t == "RECEIPT" and doc.parent_document_id is None:
        children = (
            await db.execute(
                select(func.count(Document.id)).where(
                    Document.parent_document_id == doc.id, Document.status != "CANCELLED"
                )
            )
        ).scalar()
        if children:
            return "Documents were generated from it. Cancel those first."
    if tr.action == "issue" and t == "CREDIT_NOTE" and doc.parent_document_id:
        parent = await db.get(Document, doc.parent_document_id)
        if parent:
            pf = await figures(db, parent)
            if Decimal(str(doc.grand_total)) > pf.credit_capacity:
                return f"It exceeds what can still be credited on {parent.doc_number} ({pf.credit_capacity:,.2f})."
    return None


async def available_actions(db: AsyncSession, doc: Document) -> dict:
    """Everything the UI needs to show only valid buttons."""
    t, s = str(doc.type), str(doc.status)
    f = await figures(db, doc) if t in SETTLED_TYPES else None
    actions = []
    for tr in TRANSITIONS.get(t, []):
        if s not in tr.sources:
            continue
        reason = await _blocked(db, doc, tr, f)
        actions.append({
            "action": tr.action, "label": tr.label, "target": tr.target, "tone": tr.tone,
            "confirm": tr.confirm, "needsReason": tr.needs_reason, "enabled": reason is None, "reason": reason,
        })

    children = (
        await db.execute(
            select(Document.type).where(Document.parent_document_id == doc.id, Document.status != "CANCELLED")
        )
    ).scalars().all()
    conversions = []
    for target, (label, sources) in CONVERSIONS.get(t, {}).items():
        if s not in sources:
            continue
        reason = None
        if t == "QUOTATION" and target == "INVOICE" and "INVOICE" in children:
            reason = "Already converted to an invoice."
        conversions.append({"target": target, "label": label, "enabled": reason is None, "reason": reason})

    open_balance = f is not None and s in OPEN_STATUSES and f.balance > ZERO
    can_credit = t == "INVOICE" and s in OPEN_STATUSES + ("PAID",) and f is not None and f.credit_capacity > ZERO
    return {
        "actions": actions,
        "conversions": conversions,
        "canEdit": s == "DRAFT" and t != "PAYROLL_VOUCHER",
        "canDelete": s == "DRAFT",
        "canRecordPayment": open_balance,
        "canRemovePayments": t in SETTLED_TYPES and s != "CANCELLED",
        "canCreditNote": can_credit,
        "canChangeDueDate": s == "DRAFT" or (t in SETTLED_TYPES and s in OPEN_STATUSES) or (t == "QUOTATION" and s in ("ISSUED", "EXPIRED")),
        "isLocked": s != "DRAFT",
        "statusManagedBySystem": t in SETTLED_TYPES and s in OPEN_STATUSES + ("PAID",),
        "amountPaid": str(f.paid) if f else None,
        "amountCredited": str(f.credited) if f else None,
        "balanceDue": str(f.balance) if f else None,
        "creditableAmount": str(f.credit_capacity) if f else None,
        "isOverdue": bool(f and s == "OVERDUE"),
    }


def transition_for(doc: Document, action: str) -> Transition:
    for tr in TRANSITIONS.get(str(doc.type), []):
        if tr.action == action:
            if str(doc.status) not in tr.sources:
                raise WorkflowError(f"You can't {tr.label.lower()} when the {_label(doc)} is {doc.status.replace('_', ' ').lower()}.")
            return tr
    raise WorkflowError(f"'{action}' is not available for a {_label(doc)}.")


def action_for_status(doc: Document, target: str) -> str:
    """Older clients send a target status; map it to the action that gets there."""
    target = str(target).upper()
    if target == doc.status:
        return ""
    t = str(doc.type)
    if t in SETTLED_TYPES and target in SYSTEM_STATUSES:
        raise WorkflowError(
            "Paid, partially paid and overdue are set automatically. Record a payment, or issue a credit note."
        )
    for tr in TRANSITIONS.get(t, []):
        if tr.target == target and doc.status in tr.sources:
            return tr.action
    raise WorkflowError(
        f"A {_label(doc)} can't move from {doc.status.replace('_', ' ').lower()} to {target.replace('_', ' ').lower()}."
    )


async def apply_action(
    db: AsyncSession,
    doc: Document,
    action: str,
    user_id: Optional[uuid.UUID] = None,
    reason: Optional[str] = None,
) -> Document:
    """Validate and perform one workflow action (does not commit)."""
    tr = transition_for(doc, action)
    f = await figures(db, doc) if str(doc.type) in SETTLED_TYPES else None
    blocked = await _blocked(db, doc, tr, f)
    if blocked:
        raise WorkflowError(f"Can't {tr.label.lower()}: {blocked}")
    if tr.needs_reason and not (reason or "").strip() and action in ("void",):
        raise WorkflowError("Give a reason for voiding.")

    before = doc.status
    doc.status = tr.target
    t = str(doc.type)
    if t == "QUOTATION":
        doc.client_portal_response = {"accept": "ACCEPTED", "decline": "DECLINED"}.get(action, None if action == "revise" else doc.client_portal_response)
    if t == "CREDIT_NOTE" and action == "issue" and doc.parent_document_id:
        parent = await db.get(Document, doc.parent_document_id)
        if parent:
            pf = await figures(db, parent)
            if pf.balance <= ZERO:
                doc.status = "PAID"  # nothing owed: the credit is refunded
    await db.flush()

    await _sync_loyalty(db, doc, user_id)  # points redeemed on issue, earned on a cash sale, returned on void
    if t in SETTLED_TYPES and doc.status == "ISSUED":
        await refresh_settlement(db, doc)
    await sync_document_stock(db, doc, user_id)
    await _sync_gl(db, doc)
    if t == "CREDIT_NOTE" and doc.parent_document_id:
        parent = await db.get(Document, doc.parent_document_id)
        if parent:
            await refresh_settlement(db, parent)

    if user_id:
        note = f"{tr.label}: {before.replace('_', ' ').title()} → {doc.status.replace('_', ' ').title()}"
        if reason and reason.strip():
            note += f". Reason: {reason.strip()}"
        db.add(DocumentNote(shop_id=doc.shop_id, document_id=doc.id, user_id=user_id, note=note))
    return doc


def ensure_draft(doc: Document, what: str = "edited") -> None:
    if str(doc.status) != "DRAFT":
        hint = (
            "Void it instead." if what == "deleted"
            else "Raise a credit or debit note to correct amounts." if str(doc.type) in SETTLED_TYPES
            else "Revise it back to draft first." if str(doc.type) == "QUOTATION"
            else "It is locked once issued."
        )
        raise WorkflowError(f"Only drafts can be {what}. This {_label(doc)} is {doc.status.replace('_', ' ').lower()}. {hint}")


def check_conversion(doc: Document, target: str) -> None:
    rules = CONVERSIONS.get(str(doc.type), {})
    if target not in rules:
        raise WorkflowError(f"A {_label(doc)} can't be converted to a {target.replace('_', ' ').lower()}.")
    label, sources = rules[target]
    if str(doc.status) not in sources:
        if str(doc.type) == "INVOICE" and target == "RECEIPT":
            raise WorkflowError("Record the remaining payment first. A receipt is issued once the invoice is fully paid.")
        raise WorkflowError(f"Can't {label.lower()} while the {_label(doc)} is {doc.status.replace('_', ' ').lower()}.")


def initial_status(doc_type: str, requested: str) -> str:
    """Status a new document may start in. Invoices start Draft or Issued (Paid is handled by the caller)."""
    allowed = TYPE_STATUSES.get(doc_type, {"DRAFT"})
    if doc_type in SETTLED_TYPES and requested in ("PARTIALLY_PAID", "OVERDUE"):
        return "ISSUED"
    if requested not in allowed:
        raise WorkflowError(f"A new {doc_type.replace('_', ' ').lower()} can't start as {requested.replace('_', ' ').lower()}.")
    return requested


__all__ = [
    "WorkflowError", "TRANSITIONS", "CONVERSIONS", "SETTLED_TYPES", "OPEN_STATUSES", "figures",
    "refresh_settlement", "sweep_overdue_and_expired", "sync_document_stock", "available_actions",
    "apply_action", "action_for_status", "ensure_draft", "check_conversion", "initial_status", "is_stock_sale",
]
