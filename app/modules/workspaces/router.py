from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.identity import Shop, ShopMember
from app.modules.auth.service import verify_and_get_session

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


@router.get("/my-workspaces")
async def get_my_workspaces_endpoint(
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """List all workspaces the authenticated user belongs to, auto-healing owner memberships."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    token = parts[1]
    user = await verify_and_get_session(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    q_members = (
        select(ShopMember, Shop)
        .join(Shop, ShopMember.shop_id == Shop.id)
        .where(ShopMember.user_id == user.id, ShopMember.is_active == True)
    )
    res_members = await db.execute(q_members)
    results = res_members.all()

    workspaces = []
    seen_ids = set()
    for member, shop in results:
        seen_ids.add(shop.id)
        workspaces.append({
            "id": str(shop.id),
            "name": shop.name,
            "slug": shop.slug,
            "role": member.role,
        })

    # Auto-heal: owned shops without membership
    q_owned = select(Shop).where(Shop.owner_id == user.id)
    res_owned = await db.execute(q_owned)
    owned_shops = res_owned.scalars().all()
    for shop in owned_shops:
        if shop.id not in seen_ids:
            new_mem = ShopMember(shop_id=shop.id, user_id=user.id, role="OWNER", is_active=True)
            db.add(new_mem)
            await db.commit()
            workspaces.append({
                "id": str(shop.id),
                "name": shop.name,
                "slug": shop.slug,
                "role": "OWNER",
            })

    return {"success": True, "workspaces": workspaces}


def serialize_shop(shop: Shop) -> dict:
    return {
        "id": str(shop.id),
        "ownerId": str(shop.owner_id),
        "name": shop.name,
        "slug": shop.slug,
        "code": getattr(shop, "code", None),
        "currency": shop.currency or "KES",
        "taxPin": getattr(shop, "tax_pin", None),
        "isVatRegistered": getattr(shop, "is_vat_registered", False),
        "vatNumber": getattr(shop, "vat_number", None),
        "fiscalYearStartMonth": getattr(shop, "fiscal_year_start_month", 1),
        "isGlEnabled": getattr(shop, "is_gl_enabled", False),
        "primaryColor": getattr(shop, "primary_color", "#064e3b") or "#064e3b",
        "plan": getattr(shop, "plan", "FREE") or "FREE",
        "subscriptionStatus": getattr(shop, "subscription_status", "ACTIVE") or "ACTIVE",
        "isLifetimePro": getattr(shop, "is_lifetime_pro", False),
    }


@router.get("/by-slug/{slug}/context")
async def get_workspace_context_by_slug(
    slug: str,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Resolve full active workspace context for Next.js layout and RSC rendering."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    token = parts[1]
    user = await verify_and_get_session(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    q_shop = select(Shop).where(Shop.slug == slug)
    res_shop = await db.execute(q_shop)
    shop = res_shop.scalar_one_or_none()

    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    # SuperAdmin Bypass
    if user.is_super_admin:
        return {
            "success": True,
            "role": "OWNER",
            "shop": serialize_shop(shop),
        }


    # Membership check & Auto-Heal Owner
    q_member = select(ShopMember).where(ShopMember.shop_id == shop.id, ShopMember.user_id == user.id)
    res_member = await db.execute(q_member)
    membership = res_member.scalar_one_or_none()

    if not membership and shop.owner_id == user.id:
        membership = ShopMember(
            shop_id=shop.id,
            user_id=user.id,
            role="OWNER",
            is_active=True,
        )
        db.add(membership)
        await db.commit()

    if not membership or not membership.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this workspace")

    return {
        "success": True,
        "role": membership.role,
        "shop": serialize_shop(shop),
    }


PLAN_SPECS_DEF = {
    "FREE": {
        "id": "FREE",
        "name": "Free Starter",
        "tagline": "Essential walk-in invoicing & POS for sole operators",
        "priceKesMonthly": 0,
        "priceKesAnnually": 0,
        "annualDiscountPercent": 0,
        "maxMembers": 1,
        "maxLocations": 1,
        "canTransferStock": False,
        "hasGeneralLedger": False,
        "hasReconciliation": False,
        "hasStatutoryPayroll": False,
        "hasApiAccess": False,
        "badge": None,
        "isHighlighted": False,
        "features": [
            "1 Workspace & 1 Team Member (Owner)",
            "Single Stock Location (Main Store)",
            "Standard Quotes, Invoices & Receipts",
            "Walk-in POS & 58mm/80mm Thermal Receipt Slips",
            "KRA eTIMS CU QR Verification",
            "Client Statement of Account Ledgers",
        ],
    },
    "BASIC": {
        "id": "BASIC",
        "name": "Basic",
        "tagline": "Team collaboration & multi-node stock for growing retail shops",
        "priceKesMonthly": 1500,
        "priceKesAnnually": 14400,
        "annualDiscountPercent": 20,
        "maxMembers": 3,
        "maxLocations": 2,
        "canTransferStock": True,
        "hasGeneralLedger": False,
        "hasReconciliation": False,
        "hasStatutoryPayroll": False,
        "hasApiAccess": False,
        "badge": "Popular",
        "isHighlighted": True,
        "features": [
            "Up to 3 Team Members with Role-Based Access",
            "2 Stock Locations (Main Store + 1 Branch/Warehouse)",
            "Inter-Location Stock Transfers",
            "Client Portal & Online Statement Access",
            "Customer Loyalty & Cash-Back Wallet Engine",
            "Automated WhatsApp & Email Invoice Delivery",
            "Standard Reports & Profit & Loss Analytics",
        ],
    },
    "PRO": {
        "id": "PRO",
        "name": "Professional",
        "tagline": "Full statutory compliance, double-entry GL & advanced business intelligence",
        "priceKesMonthly": 3500,
        "priceKesAnnually": 33600,
        "annualDiscountPercent": 20,
        "maxMembers": 10,
        "maxLocations": 5,
        "canTransferStock": True,
        "hasGeneralLedger": True,
        "hasReconciliation": True,
        "hasStatutoryPayroll": True,
        "hasApiAccess": True,
        "badge": "Best Value",
        "isHighlighted": False,
        "features": [
            "Up to 10 Team Members across 5 Locations",
            "Double-Entry General Ledger & Chart of Accounts",
            "Live Bank Statement Automated Reconciliation",
            "Statutory Kenyan Payroll (PAYE, NSSF, SHIF, Housing Levy)",
            "Vendor Bills, Purchase Orders & AP Aging Reports",
            "Multi-Currency Invoicing with Real-Time FX Gains/Losses",
            "Automated Daily DB Snapshots & Priority Support",
        ],
    },
    "ENTERPRISE": {
        "id": "ENTERPRISE",
        "name": "Enterprise",
        "tagline": "Unlimited scale, multi-branch warehouses & dedicated SLA support",
        "priceKesMonthly": 8000,
        "priceKesAnnually": 76800,
        "annualDiscountPercent": 20,
        "maxMembers": 999999,
        "maxLocations": 999999,
        "canTransferStock": True,
        "hasGeneralLedger": True,
        "hasReconciliation": True,
        "hasStatutoryPayroll": True,
        "hasApiAccess": True,
        "badge": None,
        "isHighlighted": False,
        "features": [
            "Unlimited Team Members & Multi-Branch Warehouses",
            "Dedicated PostgreSQL Database Cluster",
            "99.9% Uptime SLA & 24/7 Telephone Escalation Support",
            "Bespoke ERP Customizations & KRA eTIMS VSCU Hardware Bridge",
            "Unlimited Multi-Entity Group Consolidation",
        ],
    },
}


@router.get("/{shop_id}/plan-details")
async def get_shop_plan_details_endpoint(
    shop_id: str,
    db: AsyncSession = Depends(get_db),
):
    import uuid
    from sqlalchemy import func
    from app.models.identity import User

    parsed_id = uuid.UUID(shop_id) if isinstance(shop_id, str) else shop_id
    q_shop = select(Shop).where(Shop.id == parsed_id)
    shop = (await db.execute(q_shop)).scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Workspace not found")

    q_owner = select(User).where(User.id == shop.owner_id)
    owner = (await db.execute(q_owner)).scalar_one_or_none()

    is_lifetime = bool(
        getattr(shop, "is_lifetime_pro", False) or
        (owner and (getattr(owner, "is_lifetime_pro", False) or getattr(owner, "is_super_admin", False)))
    )

    owner_plan = (getattr(owner, "plan", "") or "").upper() if owner else ""
    shop_plan = (getattr(shop, "plan", "") or "").upper()
    raw_plan = "PRO" if is_lifetime else (owner_plan if owner_plan and owner_plan != "FREE" else (shop_plan if shop_plan and shop_plan != "FREE" else "FREE"))
    effective_plan = raw_plan if raw_plan in PLAN_SPECS_DEF else "FREE"
    plan_spec = PLAN_SPECS_DEF[effective_plan]

    q_members_cnt = select(func.count(ShopMember.id)).where(ShopMember.shop_id == parsed_id, ShopMember.is_active == True)
    members_count = (await db.execute(q_members_cnt)).scalar() or 1
    locations_count = 1

    return {
        "success": True,
        "planDetails": {
            "shopId": str(shop.id),
            "shopName": shop.name,
            "slug": shop.slug,
            "plan": effective_plan,
            "planSpec": plan_spec,
            "isLifetimePro": is_lifetime,
            "isSuspended": False,
            "subscriptionStatus": getattr(shop, "subscription_status", "ACTIVE") or "ACTIVE",
            "isExpired": False,
            "inGracePeriod": False,
            "graceDaysRemaining": None,
            "graceEndsAt": None,
            "expiresAt": None,
            "daysRemaining": None,
            "usage": {
                "membersCount": members_count,
                "locationsCount": locations_count,
                "maxMembers": plan_spec["maxMembers"],
                "maxLocations": plan_spec["maxLocations"],
                "membersRemaining": max(0, plan_spec["maxMembers"] - members_count),
                "locationsRemaining": max(0, plan_spec["maxLocations"] - locations_count),
                "canAddMember": members_count < plan_spec["maxMembers"],
                "canAddLocation": locations_count < plan_spec["maxLocations"],
            },
            "limits": {
                "maxMembers": plan_spec["maxMembers"],
                "maxLocations": plan_spec["maxLocations"],
                "canTransferStock": plan_spec["canTransferStock"],
                "canAccessGL": plan_spec["hasGeneralLedger"],
                "canAccessReconciliation": plan_spec["hasReconciliation"],
                "canAccessPayroll": plan_spec["hasStatutoryPayroll"],
            },
        },
    }



@router.get("/{shop_id}/dashboard-summary")
async def get_dashboard_summary_endpoint(
    shop_id: str,
    db: AsyncSession = Depends(get_db),
):
    import uuid
    from app.models.document import Document
    from app.models.client import Client
    from app.models.item import Product
    from app.models.expense import Expense
    from app.models.supplier import Supplier
    from sqlalchemy import func, desc, select

    parsed_id = uuid.UUID(shop_id) if isinstance(shop_id, str) else shop_id

    # 1. Documents (last 500 for stats)
    q_docs = (
        select(Document, Client.name.label("client_name"), Supplier.name.label("supplier_name"))
        .outerjoin(Client, Document.client_id == Client.id)
        .outerjoin(Supplier, Document.supplier_id == Supplier.id)
        .where(Document.shop_id == parsed_id)
        .order_by(desc(Document.issue_date))
        .limit(500)
    )
    docs_res = (await db.execute(q_docs)).all()

    all_docs = []
    for doc, c_name, s_name in docs_res:
        all_docs.append({
            "id": str(doc.id),
            "docNumber": doc.doc_number,
            "type": doc.type,
            "status": doc.status,
            "grandTotal": str(doc.grand_total or "0.00"),
            "currency": doc.currency or "KES",
            "issueDate": doc.issue_date.isoformat() if doc.issue_date else None,
            "dueDate": doc.due_date.isoformat() if doc.due_date else None,
            "parentDocumentId": str(doc.parent_document_id) if doc.parent_document_id else None,
            "client": {"id": str(doc.client_id), "name": c_name or "Walk-In Customer"} if doc.client_id else None,
            "supplier": {"id": str(doc.supplier_id), "name": s_name} if doc.supplier_id else None,
        })

    recent_docs = all_docs[:10]

    # 2. Counts
    q_client_cnt = select(func.count(Client.id)).where(Client.shop_id == parsed_id)
    client_cnt = (await db.execute(q_client_cnt)).scalar() or 0

    q_product_cnt = select(func.count(Product.id)).where(Product.shop_id == parsed_id)
    product_cnt = (await db.execute(q_product_cnt)).scalar() or 0

    # 3. Products
    q_products = select(Product).where(Product.shop_id == parsed_id).limit(100)
    prods_res = (await db.execute(q_products)).scalars().all()
    all_products = [{"id": str(p.id), "name": p.name, "unitPrice": str(p.unit_price)} for p in prods_res]

    # 4. Clients
    q_clients = select(Client).where(Client.shop_id == parsed_id).limit(100)
    clients_res = (await db.execute(q_clients)).scalars().all()
    all_clients = [{"id": str(c.id), "name": c.name} for c in clients_res]

    # 5. Expenses
    q_expenses = select(Expense).where(Expense.shop_id == parsed_id).order_by(desc(Expense.expense_date)).limit(4)
    exp_res = (await db.execute(q_expenses)).scalars().all()
    recent_expenses = [
        {
            "id": str(e.id),
            "expenseDate": e.expense_date.isoformat() if e.expense_date else None,
            "amount": str(e.amount),
            "category": e.category,
            "description": e.description,
            "vendorName": None,
        }
        for e in exp_res
    ]

    # 6. Recurring invoices
    q_rec = (
        select(Document, Client.name.label("client_name"), Client.email.label("client_email"))
        .outerjoin(Client, Document.client_id == Client.id)
        .where(Document.shop_id == parsed_id, Document.type == "INVOICE", Document.is_recurring == True)
        .order_by(Document.next_recurring_date.asc())
    )
    rec_res = (await db.execute(q_rec)).all()
    recurring_invoices = [
        {
            "id": str(d.id),
            "docNumber": d.doc_number,
            "clientName": c_name or "Walk-In Customer",
            "clientEmail": c_email or "—",
            "clientId": str(d.client_id) if d.client_id else None,
            "currency": d.currency or "KES",
            "grandTotal": str(d.grand_total or "0.00"),
            "isRecurring": True,
            "recurringInterval": d.recurring_interval,
            "nextRecurringDate": d.next_recurring_date.isoformat() if d.next_recurring_date else None,
            "createdAt": d.created_at.isoformat() if d.created_at else None,
        }
        for d, c_name, c_email in rec_res
    ]

    return {
        "success": True,
        "recentDocs": recent_docs,
        "allDocs": all_docs,
        "clientCount": client_cnt,
        "productCount": product_cnt,
        "paymentCount": 1,
        "allProducts": all_products,
        "allClients": all_clients,
        "recentExpenses": recent_expenses,
        "recurringInvoices": recurring_invoices,
    }


@router.get("/{shop_id}/recurring-invoices")
async def get_recurring_invoices_endpoint(
    shop_id: str,
    db: AsyncSession = Depends(get_db),
):
    import uuid
    from app.models.document import Document
    from app.models.client import Client
    from sqlalchemy import select

    parsed_id = uuid.UUID(shop_id) if isinstance(shop_id, str) else shop_id
    q_rec = (
        select(Document, Client.name.label("client_name"), Client.email.label("client_email"))
        .outerjoin(Client, Document.client_id == Client.id)
        .where(Document.shop_id == parsed_id, Document.type == "INVOICE", Document.is_recurring == True)
        .order_by(Document.next_recurring_date.asc())
    )
    rec_res = (await db.execute(q_rec)).all()
    recurring_invoices = [
        {
            "id": str(d.id),
            "docNumber": d.doc_number,
            "clientName": c_name or "Walk-In Customer",
            "clientEmail": c_email or "—",
            "clientId": str(d.client_id) if d.client_id else None,
            "currency": d.currency or "KES",
            "grandTotal": str(d.grand_total or "0.00"),
            "isRecurring": True,
            "recurringInterval": d.recurring_interval,
            "nextRecurringDate": d.next_recurring_date.isoformat() if d.next_recurring_date else None,
            "createdAt": d.created_at.isoformat() if d.created_at else None,
        }
        for d, c_name, c_email in rec_res
    ]
    return {"success": True, "recurringInvoices": recurring_invoices}



import re
import secrets
from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr
from decimal import Decimal
from datetime import datetime, timedelta
from app.models.identity import User, ShopInvitation
from app.modules.auth.service import generate_unique_shop_code


class CreateWorkspaceRequest(BaseModel):
    business_name: str
    currency: str = "KES"


class WorkspaceSettingsUpdate(BaseModel):
    name: Optional[str] = None
    currency: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    primary_color: Optional[str] = None
    tax_pin: Optional[str] = None
    is_vat_registered: Optional[bool] = None
    vat_number: Optional[str] = None
    is_gl_enabled: Optional[bool] = None
    fiscal_year_start_month: Optional[int] = None


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = "EMPLOYEE"
    custom_permissions: Dict[str, bool] = {}
    assigned_location_ids: Optional[List[str]] = []
    hide_cost_prices: bool = False
    direct_approval_limit: Decimal = Decimal("0.00")


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = None
    custom_permissions: Optional[Dict[str, bool]] = None
    assigned_location_ids: Optional[List[str]] = None
    hide_cost_prices: Optional[bool] = None
    direct_approval_limit: Optional[Decimal] = None


@router.post("/create")
async def create_workspace_endpoint(
    req: CreateWorkspaceRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Creates an additional workspace for the authenticated user."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    base_slug = re.sub(r"[^a-z0-9]+", "-", req.business_name.lower().strip()).strip("-") or "shop"
    q_slug = select(Shop).where(Shop.slug == base_slug)
    res_slug = await db.execute(q_slug)
    final_slug = f"{base_slug}-{secrets.token_hex(2)}" if res_slug.scalar_one_or_none() else base_slug

    shop_code = await generate_unique_shop_code(db)

    new_shop = Shop(
        owner_id=user.id,
        name=req.business_name.strip(),
        slug=final_slug,
        code=shop_code,
        currency=req.currency.upper()[:3],
        is_vat_registered=False,
        plan="FREE",
        subscription_status="ACTIVE",
        is_lifetime_pro=False,
        primary_color="#000000",
    )
    db.add(new_shop)
    await db.flush()

    new_member = ShopMember(
        shop_id=new_shop.id,
        user_id=user.id,
        role="OWNER",
        is_active=True,
    )
    db.add(new_member)
    await db.commit()

    return {"success": True, "shop": {"id": str(new_shop.id), "name": new_shop.name, "slug": new_shop.slug}}


@router.patch("/{shop_id}/settings")
async def update_workspace_settings_endpoint(
    shop_id: str,
    req: WorkspaceSettingsUpdate,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Update workspace parameters (name, currency, branding, tax settings)."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    q_shop = select(Shop).where(Shop.id == shop_id)
    res_shop = await db.execute(q_shop)
    shop = res_shop.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    if req.name is not None:
        shop.name = req.name.strip()
    if req.currency is not None:
        shop.currency = req.currency.upper()[:3]
    if req.primary_color is not None:
        shop.primary_color = req.primary_color
    if req.tax_pin is not None:
        shop.tax_pin = req.tax_pin.strip()
    if req.is_vat_registered is not None:
        shop.is_vat_registered = req.is_vat_registered
    if req.vat_number is not None:
        shop.vat_number = req.vat_number.strip()
    if req.is_gl_enabled is not None:
        shop.is_gl_enabled = req.is_gl_enabled
    if req.fiscal_year_start_month is not None:
        shop.fiscal_year_start_month = req.fiscal_year_start_month

    await db.commit()
    return {"success": True, "shop": {"id": str(shop.id), "name": shop.name, "slug": shop.slug}}


@router.get("/{shop_id}/members")
async def get_workspace_members_endpoint(
    shop_id: str,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """List members and pending invitations for a workspace."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    q_members = (
        select(ShopMember, User)
        .join(User, ShopMember.user_id == User.id)
        .where(ShopMember.shop_id == shop_id, ShopMember.is_active == True)
    )
    res_members = await db.execute(q_members)
    members_data = []
    for member, u in res_members.all():
        members_data.append({
            "id": str(member.id),
            "userId": str(u.id),
            "name": u.name,
            "email": u.email,
            "role": member.role,
            "customPermissions": member.custom_permissions,
            "hideCostPrices": member.hide_cost_prices,
            "directApprovalLimit": float(member.direct_approval_limit),
            "isActive": member.is_active,
            "createdAt": member.created_at.isoformat() if member.created_at else None,
        })

    q_invites = select(ShopInvitation).where(ShopInvitation.shop_id == shop_id, ShopInvitation.status == "PENDING")
    res_invites = await db.execute(q_invites)
    invites_data = []
    for inv in res_invites.scalars().all():
        invites_data.append({
            "id": str(inv.id),
            "email": inv.email,
            "role": inv.role,
            "status": inv.status,
            "expiresAt": inv.expires_at.isoformat(),
        })

    return {"success": True, "members": members_data, "invitations": invites_data}


@router.post("/{shop_id}/invitations")
async def invite_team_member_endpoint(
    shop_id: str,
    req: InviteMemberRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Invite or directly attach an employee to the workspace."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    user = await verify_and_get_session(db, parts[1])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    clean_email = req.email.lower().strip()

    # Check if user already exists
    q_target = select(User).where(User.email == clean_email)
    res_target = await db.execute(q_target)
    target_user = res_target.scalar_one_or_none()

    import json
    permissions_str = json.dumps(req.custom_permissions)

    if target_user:
        # Check if already a member
        q_existing = select(ShopMember).where(ShopMember.shop_id == shop_id, ShopMember.user_id == target_user.id)
        res_existing = await db.execute(q_existing)
        existing_mem = res_existing.scalar_one_or_none()
        if existing_mem:
            if existing_mem.is_active:
                return {"success": False, "error": "User is already a member of this workspace."}
            existing_mem.is_active = True
            existing_mem.role = req.role
            existing_mem.custom_permissions = permissions_str
            await db.commit()
            return {"success": True, "message": "Membership reactivated."}

        new_mem = ShopMember(
            shop_id=shop_id,
            user_id=target_user.id,
            role=req.role,
            custom_permissions=permissions_str,
            assigned_location_ids=req.assigned_location_ids,
            hide_cost_prices=req.hide_cost_prices,
            direct_approval_limit=req.direct_approval_limit,
            is_active=True,
        )
        db.add(new_mem)
        await db.commit()
        return {"success": True, "message": "User added directly to workspace."}

    # Otherwise create pending invitation
    invite_token = secrets.token_hex(32)
    invitation = ShopInvitation(
        shop_id=shop_id,
        email=clean_email,
        role=req.role,
        custom_permissions=permissions_str,
        assigned_location_ids=req.assigned_location_ids,
        hide_cost_prices=req.hide_cost_prices,
        direct_approval_limit=req.direct_approval_limit,
        token=invite_token,
        status="PENDING",
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invitation)
    await db.commit()

    return {"success": True, "token": invite_token, "message": "Invitation created."}


@router.delete("/{shop_id}/invitations/{invite_id}")
async def cancel_invitation_endpoint(
    shop_id: str,
    invite_id: str,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Revoke and delete a pending invitation."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    q = select(ShopInvitation).where(ShopInvitation.id == invite_id, ShopInvitation.shop_id == shop_id)
    res = await db.execute(q)
    inv = res.scalar_one_or_none()
    if inv:
        await db.delete(inv)
        await db.commit()
    return {"success": True}


@router.patch("/{shop_id}/members/{member_id}")
async def update_member_endpoint(
    shop_id: str,
    member_id: str,
    req: UpdateMemberRequest,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Update role, permissions, and settings for a team member."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    q = select(ShopMember).where(ShopMember.id == member_id, ShopMember.shop_id == shop_id)
    res = await db.execute(q)
    member = res.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if req.role is not None:
        member.role = req.role
    if req.custom_permissions is not None:
        import json
        member.custom_permissions = json.dumps(req.custom_permissions)
    if req.assigned_location_ids is not None:
        member.assigned_location_ids = req.assigned_location_ids
    if req.hide_cost_prices is not None:
        member.hide_cost_prices = req.hide_cost_prices
    if req.direct_approval_limit is not None:
        member.direct_approval_limit = req.direct_approval_limit

    await db.commit()
    return {"success": True}


@router.delete("/{shop_id}/members/{member_id}")
async def remove_member_endpoint(
    shop_id: str,
    member_id: str,
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate or remove a team member from workspace."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    q = select(ShopMember).where(ShopMember.id == member_id, ShopMember.shop_id == shop_id)
    res = await db.execute(q)
    member = res.scalar_one_or_none()
    if member:
        member.is_active = False
        await db.commit()
    return {"success": True}
