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
