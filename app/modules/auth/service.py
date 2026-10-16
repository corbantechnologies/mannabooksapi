import secrets
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from app.models.identity import User, Session, Shop, ShopMember
from app.core.config import get_settings
from app.core.security import create_access_token, verify_access_token

settings = get_settings()


def hash_password(password: str) -> str:
    """Hash password with bcrypt (compatible with Node.js bcrypt)."""
    salt = bcrypt.gensalt(10)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against existing Node bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ---------- Old session helpers (kept for migration / logout) ----------

async def create_session(db: AsyncSession, user_id) -> Tuple[str, datetime]:
    """Legacy session (still used by some internal paths). Prefer JWT."""
    token = secrets.token_hex(32)
    expires_at = utcnow() + timedelta(days=settings.COOKIE_DURATION_DAYS)

    session_record = Session(
        id=token,
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(session_record)
    await db.commit()
    return token, expires_at


async def verify_and_get_session(db: AsyncSession, token: str) -> Optional[User]:
    """Resolve a user from a JWT (with revocation checks) or a legacy DB session token."""
    if not token:
        return None
    from app.api.deps import resolve_user_from_token
    user, _ = await resolve_user_from_token(db, token)
    return user


async def invalidate_session(db: AsyncSession, token: str) -> None:
    """Revokes the active session row."""
    if token:
        await db.execute(delete(Session).where(Session.id == token))
        await db.commit()


# ---------- New JWT helpers ----------

def issue_jwt_for_user(user_id) -> str:
    """Create a JWT for the given user id."""
    return create_access_token(subject=str(user_id))


# ---------- Registration ----------

import re
import string
import random
from app.modules.auth.schemas import RegisterOwnerRequest, SessionResponse, UserProfileResponse
from app.core.datetime_utils import utcnow


async def generate_unique_shop_code(db: AsyncSession) -> str:
    chars = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(random.choices(chars, k=5))
        q = select(Shop).where(Shop.code == code)
        res = await db.execute(q)
        if not res.scalar_one_or_none():
            return code
    return secrets.token_hex(3).upper()[:5]


async def register_owner_account(db: AsyncSession, req: RegisterOwnerRequest) -> SessionResponse:
    clean_email = req.email.lower().strip()
    q = select(User).where(User.email == clean_email)
    res = await db.execute(q)
    if res.scalar_one_or_none():
        return SessionResponse(success=False, error="An account with this email already exists.")

    hashed_pass = hash_password(req.password)

    new_user = User(
        name=req.name.strip(),
        email=clean_email,
        password_hash=hashed_pass,
        is_super_admin=False,
        plan="FREE",
        subscription_status="ACTIVE",
    )
    db.add(new_user)
    await db.flush()

    base_slug = re.sub(r"[^a-z0-9]+", "-", req.business_name.lower().strip()).strip("-") or "shop"
    q_slug = select(Shop).where(Shop.slug == base_slug)
    res_slug = await db.execute(q_slug)
    final_slug = f"{base_slug}-{secrets.token_hex(2)}" if res_slug.scalar_one_or_none() else base_slug

    shop_code = await generate_unique_shop_code(db)

    new_shop = Shop(
        owner_id=new_user.id,
        name=req.business_name.strip(),
        slug=final_slug,
        code=shop_code,
        currency="KES",
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
        user_id=new_user.id,
        role="OWNER",
        is_active=True,
    )
    db.add(new_member)
    await db.commit()

    # Issue JWT instead of old session
    token = issue_jwt_for_user(new_user.id)

    return SessionResponse(
        success=True,
        token=token,
        shop_slug=final_slug,
        user=UserProfileResponse(
            id=new_user.id,
            name=new_user.name,
            email=new_user.email,
            is_super_admin=new_user.is_super_admin,
            plan=new_user.plan,
            subscription_status=new_user.subscription_status,
        ),
    )