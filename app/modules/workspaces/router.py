from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.identity import Shop, ShopMember
from app.modules.auth.service import verify_and_get_session

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


@router.get("/by-slug/{slug}/context", dependencies=[Depends(verify_internal_api_key)])
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
            "shop": {
                "id": str(shop.id),
                "name": shop.name,
                "slug": shop.slug,
                "currency": shop.currency,
                "taxPin": shop.tax_pin,
                "isVatRegistered": shop.is_vat_registered,
                "vatNumber": shop.vat_number,
                "fiscalYearStartMonth": shop.fiscal_year_start_month,
                "isGlEnabled": shop.is_gl_enabled,
            },
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
        "shop": {
            "id": str(shop.id),
            "name": shop.name,
            "slug": shop.slug,
            "currency": shop.currency,
            "taxPin": shop.tax_pin,
            "isVatRegistered": shop.is_vat_registered,
            "vatNumber": shop.vat_number,
            "fiscalYearStartMonth": shop.fiscal_year_start_month,
            "isGlEnabled": shop.is_gl_enabled,
        },
    }
