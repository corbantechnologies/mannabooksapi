from typing import Optional
from fastapi import Header, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.core.config import get_settings

settings = get_settings()


async def verify_internal_api_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Ensure internal communication originates from Next.js BFF or an authenticated user session."""
    # 1. Valid internal secret
    if x_internal_key and x_internal_key == settings.INTERNAL_API_KEY:
        return True

    # 2. Valid user session bearer token
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split()[1]
        from app.modules.auth.service import verify_and_get_session
        user = await verify_and_get_session(db, token)
        if user:
            return True

    # 3. Development / Staging fallback if caller didn't configure key
    if not settings.is_production:
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid internal authorization secret or session token.",
    )


async def verify_cron_or_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Enforce fail-closed security: CRON_SECRET via Bearer token OR X-Internal-Key."""
    bearer_token = authorization.replace("Bearer ", "").strip() if authorization and authorization.startswith("Bearer ") else None

    is_valid_internal = x_internal_key and x_internal_key == settings.INTERNAL_API_KEY
    is_valid_cron = bearer_token and bearer_token == settings.CRON_SECRET

    if not is_valid_internal and not is_valid_cron:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized cron trigger: Invalid CRON_SECRET or Internal API key.",
        )
