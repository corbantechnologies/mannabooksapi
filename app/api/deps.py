# app/api/deps.py
from typing import Optional
from fastapi import Header, HTTPException, status, Depends, Cookie, Request
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
    request: Request,
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    access_token: Optional[str] = Cookie(None, alias="access_token"),
    db: AsyncSession = Depends(get_db),
):
    """Ensure internal communication originates from Next.js BFF or an authenticated user."""
    # 1. Valid Internal Key from BFF
    if x_internal_key and x_internal_key.strip():
        if x_internal_key.strip() == settings.INTERNAL_API_KEY.strip():
            return True

    # 2. Bearer Header
    token = None
    if authorization:
        parts = authorization.split()
        if len(parts) >= 2 and parts[0].lower() == "bearer":
            token = parts[1]
        elif len(parts) == 1:
            token = parts[0]

    # 3. Cookie fallback from standard access_token or request cookies
    if not token and access_token:
        token = access_token
    if not token and request and hasattr(request, "cookies"):
        for c_name in ["access_token", "session_token", "session", "token"]:
            val = request.cookies.get(c_name)
            if val:
                token = val
                break

    # 4. Verify token
    if token:
        user_id = verify_access_token(token)
        if user_id:
            return True
        from app.modules.auth.service import verify_and_get_session
        user = await verify_and_get_session(db, token)
        if user:
            return True

    # 5. NextAuth cookie fallback
    if request and hasattr(request, "cookies"):
        next_auth_cookie = (
            request.cookies.get("next-auth.session-token") or
            request.cookies.get("__Secure-next-auth.session-token")
        )
        if next_auth_cookie:
            return True

    if not settings.is_production:
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid authentication credentials.",
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
    access_token: Optional[str] = Cookie(None, alias="access_token"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract Bearer JWT from header or standard access_token cookie, verify it, and return the User.
    Always raises 401 (never 422).
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        parts = authorization.split()
        if len(parts) > 1:
            token = parts[1]
    elif access_token:
        token = access_token
    elif authorization:
        token = authorization.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized - Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Try JWT
    user_id = verify_access_token(token)
    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            return user

    # 2. Try session token
    from app.modules.auth.service import verify_and_get_session
    user = await verify_and_get_session(db, token)
    if user:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized - Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
