from typing import Optional
from fastapi import Header, HTTPException, status
from app.core.config import get_settings

settings = get_settings()


async def verify_internal_api_key(x_internal_key: str = Header(..., alias="X-Internal-Key")):
    """Ensure internal communication originates exclusively from the Next.js BFF layer."""
    if x_internal_key != settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal authorization secret.",
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
