# app/api/tenancy.py
"""
Central tenant-isolation guard.

Every workspace-scoped router is mounted with `Depends(workspace_guard(...))` in app/main.py.
The guard:
  1. Requires a signed-in USER (the internal key alone never unlocks tenant data).
  2. Collects every workspace reference in the request (path/query/body `shop_id`, `shop_slug`, …)
     and the owning workspace of every resource id in the path (document_id → Document.shop_id, …).
  3. Requires an active membership in each of those workspaces (super admins bypass).
  4. Applies role rules: VIEWER is read-only; optional per-route permissions (see `rules`).

The resolved context is stored on `request.state.workspace` for handlers that want it.
"""
import json
import logging
import re
import uuid
from typing import Iterable, Optional, Sequence

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, get_principal, SAFE_METHODS
from app.db.session import get_db
from app.models.identity import Shop, ShopMember, ShopInvitation, ShopCurrency, ShopTerm, BillingTransaction
from app.models.account import ChartOfAccount, FiscalYear, AccountingPeriod
from app.models.bill import VendorBill
from app.models.client import Client
from app.models.crm import Deal, Proposal, Contract, Project, ProjectMilestone, Timesheet
from app.models.document import Document, DocumentNote
from app.models.expense import Expense, Income
from app.models.governance import Notification
from app.models.group import GroupEntity, GroupMembership, InterCompanyTransaction
from app.models.hr import Employee, ExpenseClaim
from app.models.inventory import StockLocation
from app.models.item import Product
from app.models.operations import CostCenter, Stocktake, MembershipTier, FixedAsset, TaxInstalment, StorageBin
from app.models.payment import DocumentPayment, PaymentMethod
from app.models.supplier import Supplier
from app.modules.workspaces.rbac import ROLE_PRESET_PERMISSIONS

logger = logging.getLogger("mannabooks.tenancy")

# Request keys (path, query or top-level JSON body) that directly name a workspace.
SHOP_ID_KEYS = (
    "shop_id", "shopId", "workspace_id", "workspaceId", "source_shop_id", "target_shop_id", "initial_shop_id",
)
SHOP_SLUG_KEYS = ("shop_slug", "shopSlug", "workspace_slug", "slug")

# Path parameters that identify a tenant-owned row → candidate models (first match wins).
RESOURCE_MODELS: dict[str, Sequence[type]] = {
    "document_id": (Document,),
    "doc_id": (Document,),
    "note_id": (DocumentNote,),
    "payment_id": (DocumentPayment,),
    "client_id": (Client,),
    "supplier_id": (Supplier,),
    "item_id": (Product,),
    "account_id": (ChartOfAccount,),
    "fy_id": (FiscalYear,),
    "period_id": (AccountingPeriod,),
    "bill_id": (VendorBill,),
    "deal_id": (Deal,),
    "proposal_id": (Proposal,),
    "contract_id": (Contract,),
    "project_id": (Project,),
    "milestone_id": (ProjectMilestone,),
    "ts_id": (Timesheet,),
    "employee_id": (Employee,),
    "claim_id": (ExpenseClaim,),
    "cost_center_id": (CostCenter,),
    "stocktake_id": (Stocktake,),
    "tier_id": (MembershipTier,),
    "expense_id": (Expense,),
    "income_id": (Income,),
    "location_id": (StockLocation,),
    "bin_id": (StorageBin,),
    "asset_id": (FixedAsset,),
    "instalment_id": (TaxInstalment,),
    "request_id": (VendorBill, Document),
    "transaction_id": (BillingTransaction,),
    "member_id": (ShopMember,),
    "invite_id": (ShopInvitation,),
    "currency_id": (ShopCurrency,),
    "term_id": (ShopTerm,),
    "method_id": (PaymentMethod,),
    "notification_id": (Notification,),
}

# Group-consolidation ids: access requires owning the group.
GROUP_KEYS = ("group_id",)


class WorkspaceAccess:
    def __init__(self, principal: Principal, memberships: dict[uuid.UUID, Optional[ShopMember]]):
        self.principal = principal
        self.memberships = memberships

    @property
    def user(self):
        return self.principal.user

    @property
    def shop_ids(self) -> list[uuid.UUID]:
        return list(self.memberships.keys())


def _as_uuid(value) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, TypeError):
        return None


async def _read_json_body(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if "application/json" not in ctype:
        return {}
    try:
        raw = await request.body()
        if not raw:
            return {}
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _iter_values(sources: Iterable[dict], keys: Sequence[str]):
    for src in sources:
        for key in keys:
            if key in src and src[key] not in (None, ""):
                yield key, src[key]


def has_permission(membership: Optional[ShopMember], permission: str) -> bool:
    if membership is None:
        return False
    role = membership.role
    if permission == "__owner__":
        return role == "OWNER"
    if role in ("OWNER", "ADMIN"):
        return True
    if permission in ROLE_PRESET_PERMISSIONS.get(role, set()):
        return True
    if membership.custom_permissions:
        try:
            return json.loads(membership.custom_permissions).get(permission) is True
        except Exception:
            return False
    return False


def workspace_guard(
    permission: Optional[str] = None,
    rules: Optional[Sequence[tuple[str, Optional[str], Optional[str]]]] = None,
    public_paths: Sequence[str] = (),
    resolve_resources: bool = True,
):
    """
    Build a router-level dependency.

    permission: required for every route of the router (None = membership only).
    rules: list of (path_regex, methods, permission). `methods` is "*" / "READ" / "WRITE" or a
           comma list like "POST,PATCH". The first matching rule decides the permission.
    public_paths: path regexes (matched against the path after /v1) that skip the guard entirely,
           e.g. tokenised customer-portal endpoints.
    resolve_resources: map path ids (document_id, …) to their workspace. Disable for routers whose
           ids legitimately point at another tenant's rows (B2B inbox).
    """
    compiled_public = [re.compile(p) for p in public_paths]
    compiled_rules = [(re.compile(p), m, perm) for p, m, perm in (rules or [])]

    async def dependency(request: Request, db: AsyncSession = Depends(get_db)):
        path = request.url.path
        rel_path = path[3:] if path.startswith("/v1/") else path
        if any(rx.search(rel_path) for rx in compiled_public):
            return None

        principal = await get_principal(request, db)
        user = principal.user
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A signed-in user is required.")

        method = request.method.upper()
        required = permission
        for rx, methods, perm in compiled_rules:
            if not rx.search(rel_path):
                continue
            if methods in (None, "*"):
                matched = True
            elif methods == "READ":
                matched = method in SAFE_METHODS
            elif methods == "WRITE":
                matched = method not in SAFE_METHODS
            else:
                matched = method in {m.strip().upper() for m in methods.split(",")}
            if matched:
                required = perm
                break

        path_params = dict(request.path_params)
        query = dict(request.query_params)
        body = await _read_json_body(request) if method not in SAFE_METHODS else {}
        sources = (path_params, query, body)

        shop_ids: set[uuid.UUID] = set()
        slug_refs = [val for _, val in _iter_values(sources, SHOP_SLUG_KEYS)]
        for _, val in _iter_values(sources, SHOP_ID_KEYS):
            sid = _as_uuid(val)
            if sid is None:
                # Some legacy callers pass the workspace slug in `shop_id`.
                if isinstance(val, str):
                    slug_refs.append(val)
                    continue
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid workspace id.")
            shop_ids.add(sid)

        for val in slug_refs:
            sid = (await db.execute(select(Shop.id).where(Shop.slug == str(val)))).scalar_one_or_none()
            if sid is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")
            shop_ids.add(sid)

        if resolve_resources:
            for key, raw in path_params.items():
                models = RESOURCE_MODELS.get(key)
                rid = _as_uuid(raw) if models else None
                if rid is None:
                    continue
                for model in models:
                    owner = (await db.execute(select(model.shop_id).where(model.id == rid))).scalar_one_or_none()
                    if owner is not None:
                        shop_ids.add(owner)
                        break

        # Group consolidation: caller must own the group.
        group_ids = {_as_uuid(v) for _, v in _iter_values(sources, GROUP_KEYS)}
        if "membership_id" in path_params and "group-consolidation" in rel_path:
            gid = (await db.execute(select(GroupMembership.group_id).where(GroupMembership.id == _as_uuid(path_params["membership_id"])))).scalar_one_or_none()
            group_ids.add(gid)
        if "tx_id" in path_params and "group-consolidation" in rel_path:
            gid = (await db.execute(select(InterCompanyTransaction.group_id).where(InterCompanyTransaction.id == _as_uuid(path_params["tx_id"])))).scalar_one_or_none()
            group_ids.add(gid)
        group_ids.discard(None)

        if user.is_super_admin:
            request.state.workspace = WorkspaceAccess(principal, {sid: None for sid in shop_ids})
            return request.state.workspace

        for gid in group_ids:
            owner_id = (await db.execute(select(GroupEntity.owner_id).where(GroupEntity.id == gid))).scalar_one_or_none()
            if owner_id is not None and owner_id != user.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this group.")

        memberships: dict[uuid.UUID, Optional[ShopMember]] = {}
        for sid in shop_ids:
            membership = (
                await db.execute(
                    select(ShopMember).where(ShopMember.shop_id == sid, ShopMember.user_id == user.id)
                )
            ).scalars().first()
            if membership is None or not membership.is_active:
                owner_id = (await db.execute(select(Shop.owner_id).where(Shop.id == sid))).scalar_one_or_none()
                if owner_id is None:
                    # Unknown workspace id: let the handler return its own 404.
                    continue
                if owner_id != user.id:
                    logger.warning("Tenant access denied user=%s shop=%s path=%s", user.id, sid, path)
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this workspace.")
                membership = ShopMember(shop_id=sid, user_id=user.id, role="OWNER", is_active=True)

            if membership.role == "VIEWER" and method not in SAFE_METHODS:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Viewers have read-only access.")
            if required and not has_permission(membership, required):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Your role ({membership.role}) is not allowed to perform this action.",
                )
            memberships[sid] = membership

        if required and not shop_ids and not group_ids:
            # A permission-gated route must name its workspace; otherwise we cannot authorise it.
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace (shop_id) is required.")

        request.state.workspace = WorkspaceAccess(principal, memberships)
        return request.state.workspace

    return dependency
