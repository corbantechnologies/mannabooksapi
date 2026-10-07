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
