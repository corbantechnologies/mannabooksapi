# app/core/security.py
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
import jwt
from jwt import PyJWTError
from app.core.config import get_settings

settings = get_settings()


def create_access_token(subject: str | Any, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token with a unique jti so it can be revoked individually."""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES))

    to_encode = {
        "sub": str(subject),
        "exp": expire,
        "iat": now,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Return the verified JWT payload, or None if the token is invalid or expired."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except PyJWTError:
        return None
    if not payload.get("sub"):
        return None
    return payload


def verify_access_token(token: str) -> Optional[str]:
    """
    Verify JWT signature/expiry and return the subject (user_id).
    Does NOT check revocation; use app.api.deps.resolve_user_from_token for that.
    """
    payload = decode_access_token(token)
    return payload.get("sub") if payload else None


def constant_time_equals(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return secrets.compare_digest(a.strip().encode("utf-8"), b.strip().encode("utf-8"))


def hash_token(raw_token: str) -> str:
    """One-way hash for single-use secrets (password reset tokens) stored at rest."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
