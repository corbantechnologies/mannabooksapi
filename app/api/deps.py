# app/api/deps.py
from typing import Optional
from fastapi import Header, HTTPException, status, Depends, Cookie
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.core.config import get_settings
from app.core.security import verify_access_token
from app.models.identity import User

settings = get_settings()


# ============================================================
# Internal / cron dependencies (kept for BFF & crons)
# ============================================================

async def verify_internal_api_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    cookie_token: Optional[str] = Cookie(None, alias="kenya-nzuri-kabisa"),
    manna_cookie: Optional[str] = Cookie(None, alias="manna_session_token"),
    access_cookie: Optional[str] = Cookie(None, alias="manna_access_token"),
    db: AsyncSession = Depends(get_db),
):
    """Ensure internal communication originates from Next.js BFF or an authenticated user."""
    if x_internal_key is not None:
        if x_internal_key == settings.INTERNAL_API_KEY:
            return True
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal authorization secret.",
        )

    # 1. Bearer Header
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split()[1]

    # 2. Cookie fallback from Next.js reverse proxy rewrites
    if not token:
        token = cookie_token or manna_cookie or access_cookie

    if token:
        user_id = verify_access_token(token)
        if user_id:
            return True
        from app.modules.auth.service import verify_and_get_session
        user = await verify_and_get_session(db, token)
        if user:
            return True

    if not settings.is_production:
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid internal authorization secret.",
    )


async def verify_cron_or_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Enforce fail-closed security: CRON_SECRET via Bearer token OR X-Internal-Key."""
    bearer_token = (
        authorization.replace("Bearer ", "").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )

    is_valid_internal = x_internal_key and x_internal_key == settings.INTERNAL_API_KEY
    is_valid_cron = bearer_token and bearer_token == settings.CRON_SECRET

    if not is_valid_internal and not is_valid_cron:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized cron trigger: Invalid CRON_SECRET or Internal API key.",
        )


# ============================================================
# JWT-based current user
# ============================================================

async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    cookie_token: Optional[str] = Cookie(None, alias="kenya-nzuri-kabisa"),
    manna_cookie: Optional[str] = Cookie(None, alias="manna_session_token"),
    access_cookie: Optional[str] = Cookie(None, alias="manna_access_token"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract Bearer JWT from header or forwarded cookie, verify it, and return the User.
    Always raises 401 (never 422).
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split()[1]
    elif cookie_token or manna_cookie or access_cookie:
        token = cookie_token or manna_cookie or access_cookie

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized - Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = verify_access_token(token)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized - Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized - User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user