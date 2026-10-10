import secrets
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from app.models.identity import User, Session, Shop, ShopMember
from app.core.config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    """Hash password with bcrypt (compatible with Node.js bcrypt)."""
    salt = bcrypt.gensalt(10)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against existing Node bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


async def create_session(db: AsyncSession, user_id) -> Tuple[str, datetime]:
    """Generates 64-hex token and persists session row into PostgreSQL."""
    token = secrets.token_hex(32)
    expires_at = datetime.utcnow() + timedelta(days=settings.COOKIE_DURATION_DAYS)

    session_record = Session(
        id=token,
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(session_record)
    await db.commit()
    return token, expires_at


async def verify_and_get_session(db: AsyncSession, token: str) -> Optional[User]:
    """Validates session token from cookie/bearer against database, clearing stale sessions."""
    if not token:
        return None

    q = (
        select(Session)
        .where(Session.id == token)
        .options(selectinload(Session.user))
    )
    res = await db.execute(q)
    record = res.scalar_one_or_none()

    if not record or not record.user:
        return None

    if datetime.utcnow() >= record.expires_at:
        await db.execute(delete(Session).where(Session.id == token))
        await db.commit()
        return None

    return record.user


async def invalidate_session(db: AsyncSession, token: str) -> None:
    """Revokes the active session row."""
    if token:
        await db.execute(delete(Session).where(Session.id == token))
        await db.commit()


import re
import string
import random
from app.modules.auth.schemas import RegisterOwnerRequest, SessionResponse, UserProfileResponse


async def generate_unique_shop_code(db: AsyncSession) -> str:
    """Generates a unique 5-character uppercase alphanumeric code for a shop."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(random.choices(chars, k=5))
        q = select(Shop).where(Shop.code == code)
        res = await db.execute(q)
        if not res.scalar_one_or_none():
            return code
    return secrets.token_hex(3).upper()[:5]


async def register_owner_account(db: AsyncSession, req: RegisterOwnerRequest) -> SessionResponse:
    """Creates user, shop workspace, owner membership and returns new session."""
    # 1. Enforce unique emails
    clean_email = req.email.lower().strip()
    q = select(User).where(User.email == clean_email)
    res = await db.execute(q)
    existing_user = res.scalar_one_or_none()
    if existing_user:
        return SessionResponse(success=False, error="An account with this email already exists.")

    # 2. Hash password with bcrypt
    hashed_pass = hash_password(req.password)

    # 3. Create User
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

    # 4. Generate clean unique URL slug
    base_slug = re.sub(r"[^a-z0-9]+", "-", req.business_name.lower().strip()).strip("-")
    if not base_slug:
        base_slug = "shop"

    q_slug = select(Shop).where(Shop.slug == base_slug)
    res_slug = await db.execute(q_slug)
    if res_slug.scalar_one_or_none():
        final_slug = f"{base_slug}-{secrets.token_hex(2)}"
    else:
        final_slug = base_slug

    shop_code = await generate_unique_shop_code(db)

    # 5. Create Shop workspace
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

    # 6. Create ShopMember (role: OWNER)
    new_member = ShopMember(
        shop_id=new_shop.id,
        user_id=new_user.id,
        role="OWNER",
        is_active=True,
    )
    db.add(new_member)
    await db.flush()

    # 7. Commit database writes
    await db.commit()

    # 8. Issue active session token
    token, _ = await create_session(db, new_user.id)

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
