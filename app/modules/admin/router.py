import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_, and_, delete
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.api.deps import require_super_admin
from app.models.identity import User, Shop, ShopMember, Session
from app.models.document import Document
from app.core.schemas import ApiModel

router = APIRouter(prefix="/admin", tags=["Global Admin"], dependencies=[Depends(require_super_admin)])


class UpdateWorkspacePlanRequest(ApiModel):
    plan: str
    is_lifetime_pro: bool
    subscription_status: Optional[str] = "ACTIVE"


class ToggleWorkspaceSuspensionRequest(ApiModel):
    is_suspended: bool
    reason: Optional[str] = None


class UpdateUserSubscriptionRequest(ApiModel):
    plan: str
    is_lifetime_pro: bool
    subscription_expires_at: Optional[str] = None


class ToggleUserLifetimeRequest(ApiModel):
    is_lifetime_pro: bool


class ToggleSuperAdminRequest(ApiModel):
    is_super_admin: bool


@router.get("/stats")
async def get_platform_stats(db: AsyncSession = Depends(get_db)):
    """Global platform stats & telemetry for super admin overview."""
    total_users_q = select(func.count(User.id))
    total_shops_q = select(func.count(Shop.id))
    total_docs_q = select(func.count(Document.id))
    total_turnover_q = select(func.coalesce(func.sum(Document.grand_total), 0)).where(
        or_(Document.status == "PAID", Document.status == "ISSUED")
    )
    lifetime_q = select(func.count(Shop.id)).where(Shop.is_lifetime_pro.is_(True))
    suspended_q = select(func.count(Shop.id)).where(Shop.is_suspended.is_(True))

    users_cnt = (await db.execute(total_users_q)).scalar() or 0
    shops_cnt = (await db.execute(total_shops_q)).scalar() or 0
    docs_cnt = (await db.execute(total_docs_q)).scalar() or 0
    turnover = float((await db.execute(total_turnover_q)).scalar() or 0)
    lifetime_cnt = (await db.execute(lifetime_q)).scalar() or 0
    suspended_cnt = (await db.execute(suspended_q)).scalar() or 0

    recent_shops_q = select(Shop).options(selectinload(Shop.owner)).order_by(desc(Shop.created_at)).limit(6)
    recent_shops = (await db.execute(recent_shops_q)).scalars().all()

    recent_users_q = select(User).order_by(desc(User.created_at)).limit(6)
    recent_users = (await db.execute(recent_users_q)).scalars().all()

    def serialize_shop(s: Shop):
        return {
            "id": str(s.id),
            "name": s.name,
            "slug": s.slug,
            "plan": s.plan,
            "isLifetimePro": s.is_lifetime_pro,
            "isSuspended": s.is_suspended,
            "createdAt": s.created_at.isoformat() if s.created_at else None,
            "owner": {
                "id": str(s.owner.id),
                "name": s.owner.name,
                "email": s.owner.email,
            } if s.owner else None,
        }

    def serialize_user(u: User):
        return {
            "id": str(u.id),
            "name": u.name,
            "email": u.email,
            "plan": u.plan,
            "isLifetimePro": u.is_lifetime_pro,
            "isSuperAdmin": u.is_super_admin,
            "createdAt": u.created_at.isoformat() if u.created_at else None,
        }

    return {
        "success": True,
        "stats": {
            "users": users_cnt,
            "workspaces": shops_cnt,
            "documents": docs_cnt,
            "turnover": turnover,
            "lifetimeProCount": lifetime_cnt,
            "suspendedCount": suspended_cnt,
            "recentShops": [serialize_shop(s) for s in recent_shops],
            "recentUsers": [serialize_user(u) for u in recent_users],
        }
    }


@router.get("/workspaces")
async def list_admin_workspaces(
    search: Optional[str] = Query(None),
    plan_filter: Optional[str] = Query("ALL"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List workspaces with filters, owners, members, and aggregated documents."""
    offset = (page - 1) * limit

    q = select(Shop).options(selectinload(Shop.owner), selectinload(Shop.members)).order_by(desc(Shop.created_at))
    all_shops = (await db.execute(q)).scalars().all()

    filtered = []
    s_term = search.lower().strip() if search else ""
    p_filter = (plan_filter or "ALL").upper()

    for s in all_shops:
        if s_term:
            match_name = s_term in s.name.lower()
            match_slug = s_term in s.slug.lower()
            match_pin = s_term in (s.tax_pin or "").lower()
            match_owner_email = bool(s.owner and s_term in s.owner.email.lower())
            match_owner_name = bool(s.owner and s_term in s.owner.name.lower())
            if not (match_name or match_slug or match_pin or match_owner_email or match_owner_name):
                continue

        if p_filter == "LIFETIME_PRO":
            if not s.is_lifetime_pro:
                continue
        elif p_filter == "SUSPENDED":
            if not s.is_suspended:
                continue
        elif p_filter != "ALL":
            if (s.plan or "").upper() != p_filter:
                continue

        filtered.append(s)

    total_count = len(filtered)
    page_shops = filtered[offset : offset + limit]

    workspaces = []
    for shop in page_shops:
        doc_count_q = select(func.count(Document.id)).where(Document.shop_id == shop.id)
        turnover_q = select(func.coalesce(func.sum(Document.grand_total), 0)).where(
            and_(Document.shop_id == shop.id, or_(Document.status == "PAID", Document.status == "ISSUED"))
        )

        doc_count = (await db.execute(doc_count_q)).scalar() or 0
        turnover = float((await db.execute(turnover_q)).scalar() or 0)

        workspaces.append({
            "id": str(shop.id),
            "name": shop.name,
            "shortName": shop.short_name,
            "slug": shop.slug,
            "taxPin": shop.tax_pin,
            "currency": shop.currency or "KES",
            "primaryColor": shop.primary_color,
            "phone": shop.phone,
            "email": shop.email,
            "website": shop.website,
            "isVatRegistered": shop.is_vat_registered,
            "isGlEnabled": shop.is_gl_enabled,
            "plan": "PRO" if (shop.is_lifetime_pro or (shop.owner and shop.owner.is_lifetime_pro)) else (shop.plan or "FREE"),
            "subscriptionStatus": "LIFETIME_FREE" if (shop.is_lifetime_pro or (shop.owner and shop.owner.is_lifetime_pro)) else (shop.subscription_status or "ACTIVE"),
            "isLifetimePro": bool(shop.is_lifetime_pro or (shop.owner and shop.owner.is_lifetime_pro)),
            "isSuspended": shop.is_suspended,
            "suspendedReason": shop.suspended_reason,
            "createdAt": shop.created_at.isoformat() if shop.created_at else None,
            "owner": {
                "id": str(shop.owner.id),
                "name": shop.owner.name,
                "email": shop.owner.email,
                "plan": shop.owner.plan or "FREE",
                "isLifetimePro": shop.owner.is_lifetime_pro,
                "isSuperAdmin": shop.owner.is_super_admin,
            } if shop.owner else None,
            "memberCount": len(shop.members),
            "documentCount": doc_count,
            "turnover": turnover,
        })

    return {
        "success": True,
        "workspaces": workspaces,
        "totalCount": total_count,
        "page": page,
        "totalPages": (total_count + limit - 1) // limit or 1,
    }


@router.get("/workspaces/{shop_id}")
async def get_admin_workspace_details(shop_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Deep inspection of a specific tenant workspace for root admin."""
    q = select(Shop).options(
        selectinload(Shop.owner),
        selectinload(Shop.members).selectinload(ShopMember.user)
    ).where(Shop.id == shop_id)
    shop = (await db.execute(q)).scalar_one_or_none()

    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target workspace not found.")

    doc_stats_q = select(
        Document.type,
        func.count(Document.id),
        func.coalesce(func.sum(Document.grand_total), 0)
    ).where(Document.shop_id == shop_id).group_by(Document.type)
    doc_stats_res = (await db.execute(doc_stats_q)).all()

    doc_stats = [
        {"type": r[0], "count": r[1], "totalAmount": str(r[2])}
        for r in doc_stats_res
    ]

    recent_docs_q = select(Document).where(Document.shop_id == shop_id).order_by(desc(Document.created_at)).limit(10)
    recent_docs = (await db.execute(recent_docs_q)).scalars().all()

    return {
        "success": True,
        "shop": {
            "id": str(shop.id),
            "name": shop.name,
            "shortName": shop.short_name,
            "slug": shop.slug,
            "taxPin": shop.tax_pin,
            "currency": shop.currency or "KES",
            "primaryColor": shop.primary_color,
            "phone": shop.phone,
            "email": shop.email,
            "website": shop.website,
            "isVatRegistered": shop.is_vat_registered,
            "isGlEnabled": shop.is_gl_enabled,
            "plan": shop.plan,
            "subscriptionStatus": shop.subscription_status,
            "isLifetimePro": shop.is_lifetime_pro,
            "isSuspended": shop.is_suspended,
            "suspendedReason": shop.suspended_reason,
            "createdAt": shop.created_at.isoformat() if shop.created_at else None,
            "owner": {
                "id": str(shop.owner.id),
                "name": shop.owner.name,
                "email": shop.owner.email,
            } if shop.owner else None,
            "members": [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "isActive": m.is_active,
                    "user": {
                        "id": str(m.user.id),
                        "name": m.user.name,
                        "email": m.user.email,
                    } if m.user else None,
                }
                for m in shop.members
            ],
        },
        "docStats": doc_stats,
        "recentDocs": [
            {
                "id": str(d.id),
                "type": d.type,
                "docNumber": d.doc_number,
                "status": d.status,
                "grandTotal": str(d.grand_total),
                "createdAt": d.created_at.isoformat() if d.created_at else None,
            }
            for d in recent_docs
        ],
    }


@router.patch("/workspaces/{shop_id}/plan")
async def update_workspace_plan(shop_id: uuid.UUID, req: UpdateWorkspacePlanRequest, db: AsyncSession = Depends(get_db)):
    """Update workspace plan or grant Lifetime PRO."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    shop.plan = req.plan
    shop.is_lifetime_pro = req.is_lifetime_pro
    shop.subscription_status = "LIFETIME_FREE" if req.is_lifetime_pro else (req.subscription_status or "ACTIVE")

    await db.commit()
    return {
        "success": True,
        "message": f"Successfully updated plan for {shop.name}.",
        "shop": {"id": str(shop.id), "slug": shop.slug, "name": shop.name}
    }


@router.post("/workspaces/{shop_id}/suspension")
@router.post("/workspaces/{shop_id}/toggle-suspension")
async def toggle_workspace_suspension(shop_id: uuid.UUID, req: ToggleWorkspaceSuspensionRequest, db: AsyncSession = Depends(get_db)):
    """Suspend or unsuspend a workspace."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    shop.is_suspended = req.is_suspended
    shop.suspended_reason = (req.reason or "Administrative security lockout").strip() if req.is_suspended else None

    await db.commit()
    return {
        "success": True,
        "message": f"Workspace {shop.name} has been {'suspended' if req.is_suspended else 'activated'}.",
        "shop": {"id": str(shop.id), "slug": shop.slug, "name": shop.name}
    }


@router.get("/users")
async def list_admin_users(search: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    """List platform users with search and owned workspace counts."""
    q = select(User).options(
        selectinload(User.owned_shops),
        selectinload(User.memberships)
    ).order_by(desc(User.created_at))
    all_users = (await db.execute(q)).scalars().all()

    s_term = search.lower().strip() if search else ""
    users_list = []
    for u in all_users:
        if s_term and not (s_term in u.name.lower() or s_term in u.email.lower()):
            continue

        users_list.append({
            "id": str(u.id),
            "name": u.name,
            "email": u.email,
            "isSuperAdmin": u.is_super_admin,
            "isLifetimePro": u.is_lifetime_pro,
            "plan": u.plan or "FREE",
            "subscriptionStatus": u.subscription_status or "ACTIVE",
            "subscriptionExpiresAt": u.subscription_expires_at.isoformat() if u.subscription_expires_at else None,
            "createdAt": u.created_at.isoformat() if u.created_at else None,
            "ownedShopsCount": len(u.owned_shops),
            "membershipsCount": len(u.memberships),
            "ownedShops": [
                {"id": str(s.id), "name": s.name, "slug": s.slug, "plan": s.plan}
                for s in u.owned_shops
            ],
        })

    return {"success": True, "users": users_list}


@router.patch("/users/{user_id}/subscription")
async def update_user_subscription(user_id: uuid.UUID, req: UpdateUserSubscriptionRequest, db: AsyncSession = Depends(get_db)):
    """Update user plan, lifetime status, or expiration."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.plan = req.plan
    user.is_lifetime_pro = req.is_lifetime_pro
    user.subscription_status = "LIFETIME_FREE" if req.is_lifetime_pro else "ACTIVE"

    await db.commit()
    return {"success": True, "message": f"User {user.email} subscription updated."}


@router.post("/users/{user_id}/toggle-lifetime-pro")
async def toggle_user_lifetime_pro(user_id: uuid.UUID, req: ToggleUserLifetimeRequest, db: AsyncSession = Depends(get_db)):
    """Toggle Lifetime PRO for a user account and cascade to all owned shops."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_lifetime_pro = req.is_lifetime_pro
    user.plan = "PRO" if req.is_lifetime_pro else "FREE"
    user.subscription_status = "LIFETIME_FREE" if req.is_lifetime_pro else "ACTIVE"

    # Cascade to owned shops
    shops_q = select(Shop).where(Shop.owner_id == user_id)
    user_shops = (await db.execute(shops_q)).scalars().all()
    for s in user_shops:
        s.is_lifetime_pro = req.is_lifetime_pro
        s.plan = "PRO" if req.is_lifetime_pro else "FREE"
        s.subscription_status = "LIFETIME_FREE" if req.is_lifetime_pro else "ACTIVE"

    await db.commit()
    return {
        "success": True,
        "message": f"Lifetime PRO {'granted' if req.is_lifetime_pro else 'revoked'} for {user.email} and all owned workspaces."
    }


@router.post("/users/{user_id}/toggle-super-admin")
async def toggle_user_super_admin(user_id: uuid.UUID, req: ToggleSuperAdminRequest, db: AsyncSession = Depends(get_db)):
    """Elevate or demote super admin privileges."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_super_admin = req.is_super_admin
    await db.commit()
    return {
        "success": True,
        "message": f"User {user.email} has been {'elevated to' if req.is_super_admin else 'demoted from'} Super Admin."
    }


@router.delete("/users/{user_id}")
async def delete_user_account(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a user account and cascade delete owned shops and memberships."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # Remove sessions
    await db.execute(delete(Session).where(Session.user_id == user_id))
    # Remove memberships
    await db.execute(delete(ShopMember).where(ShopMember.user_id == user_id))
    # Remove owned shops (will cascade items, documents due to FK cascade)
    await db.execute(delete(Shop).where(Shop.owner_id == user_id))
    # Remove user
    await db.delete(user)
    await db.commit()

    return {"success": True, "message": f"Account {user.email} and all associated workspaces permanently deleted."}


DEFAULT_PLANS = {
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
        "maxLocations": 3,
        "canTransferStock": True,
        "hasGeneralLedger": False,
        "hasReconciliation": False,
        "hasStatutoryPayroll": True,
        "hasApiAccess": False,
        "badge": None,
        "isHighlighted": False,
        "features": [
            "Everything in Free Starter",
            "Up to 3 Team Members with Granular Roles",
            "Up to 3 Stock Locations (Main, Backroom, Branch)",
            "Inter-Branch Stock Transfers with Audit Logs",
            "Statutory Payroll (PAYE, SHIF, Housing Levy, NSSF)",
            "Expense & Operational Cost Tracking",
            "Tax Wear & Tear and VAT Return Trackers",
        ],
    },
    "PRO": {
        "id": "PRO",
        "name": "Professional",
        "tagline": "Full double-entry general ledger & unlimited warehouse inventory",
        "priceKesMonthly": 3500,
        "priceKesAnnually": 33600,
        "annualDiscountPercent": 20,
        "maxMembers": 10,
        "maxLocations": -1,
        "canTransferStock": True,
        "hasGeneralLedger": True,
        "hasReconciliation": True,
        "hasStatutoryPayroll": True,
        "hasApiAccess": False,
        "badge": "Most Popular",
        "isHighlighted": True,
        "features": [
            "Everything in Basic",
            "Up to 10 Team Members across Workspaces",
            "CRM Deals Pipeline & Visual Kanban Board",
            "Tiered Proposals with Client e-Signatures",
            "Retainer Contracts, SLAs & Project Workspaces",
            "Full Double-Entry General Ledger (GL)",
            "Balance Sheet & Real-time Trial Balance / P&L",
            "Bank & M-Pesa Cash Account Reconciliation Tool",
            "FIFO Inventory Asset Valuation Engine",
            "Multi-Currency Transaction Ledgers",
        ],
    },
    "ENTERPRISE": {
        "id": "ENTERPRISE",
        "name": "Enterprise",
        "tagline": "Unlimited high-volume capacity & custom integrations",
        "priceKesMonthly": 10000,
        "priceKesAnnually": 96000,
        "annualDiscountPercent": 20,
        "maxMembers": -1,
        "maxLocations": -1,
        "canTransferStock": True,
        "hasGeneralLedger": True,
        "hasReconciliation": True,
        "hasStatutoryPayroll": True,
        "hasApiAccess": True,
        "badge": "Custom SLA",
        "isHighlighted": False,
        "features": [
            "Everything in Professional",
            "Unlimited Team Members & Locations",
            "Dedicated Support & Custom SLAs",
            "Automated Offsite Backup Snapshots",
        ],
    },
}

_active_plans = dict(DEFAULT_PLANS)


@router.get("/pricing")
@router.get("/pricing/plans")
async def get_admin_pricing_plans():
    return {"success": True, "plans": list(_active_plans.values())}


@router.post("/pricing")
@router.post("/pricing/plans")
async def create_admin_pricing_plan(req: dict):
    plan_id = (req.get("id") or "").upper().strip()
    if not plan_id:
        raise HTTPException(status_code=400, detail="Plan ID required")
    _active_plans[plan_id] = req
    return {"success": True, "message": f"Plan {plan_id} created successfully.", "plan": req}


@router.put("/pricing/{plan_id}")
@router.put("/pricing/plans/{plan_id}")
async def update_admin_pricing_plan(plan_id: str, req: dict):
    plan_id = plan_id.upper().strip()
    _active_plans[plan_id] = req
    return {"success": True, "message": f"Plan {plan_id} updated successfully.", "plan": req}


@router.delete("/pricing/{plan_id}")
@router.delete("/pricing/plans/{plan_id}")
async def delete_admin_pricing_plan(plan_id: str):
    plan_id = plan_id.upper().strip()
    if plan_id in _active_plans:
        del _active_plans[plan_id]
    return {"success": True, "message": f"Plan {plan_id} deleted."}


@router.post("/pricing/reset")
async def reset_admin_pricing_plans():
    global _active_plans
    _active_plans = dict(DEFAULT_PLANS)
    return {"success": True, "message": "Plans reset to default tiers."}

