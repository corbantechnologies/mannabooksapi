# app/api/deps.py
"""
Authentication dependencies.

Two kinds of callers exist:
  * Users, identified by a JWT sent as `Authorization: Bearer <jwt>` (Next.js server) or as the
    HttpOnly `access_token` cookie (browser calls proxied through the Next.js /api/v1 rewrite).
  * The Next.js server itself (crons, webhooks, public portal pages), identified by `X-Internal-Key`.

Rules:
  * A token that is present but invalid is ALWAYS rejected, even if a valid internal key is also sent.
    Otherwise a forged/expired cookie forwarded by the Next.js server would downgrade to "internal".
  * Workspace-scoped routes additionally require a *user* (see app/api/tenancy.py); the internal key
    alone never grants access to tenant data.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import uuid

from fastapi import HTTPException, status, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.db.session import get_db
from app.core.config import get_settings
from app.core.security import decode_access_token, constant_time_equals
from app.models.identity import User, Session, RevokedToken
from app.core.datetime_utils import utcnow

settings = get_settings()

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: Optional[User]
    is_internal: bool
    token_from_cookie: bool = False
    token_payload: Optional[dict] = None


def _unauthorized(detail: str = "Missing or invalid authentication credentials.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def has_valid_internal_key(request: Request) -> bool:
    return constant_time_equals(request.headers.get("x-internal-key"), settings.INTERNAL_API_KEY)


def extract_token(request: Request) -> tuple[Optional[str], bool]:
    """Return (token, came_from_cookie). Only `Authorization: Bearer` and the session cookie are accepted."""
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
            return parts[1], False
        raise _unauthorized("Malformed Authorization header.")
    cookie_val = request.cookies.get(settings.COOKIE_NAME)
    if cookie_val:
        return cookie_val, True
    return None, False


async def resolve_user_from_token(db: AsyncSession, token: str) -> tuple[Optional[User], Optional[dict]]:
    """Validate a JWT (signature, expiry, revocation, password-change cutoff) or a legacy DB session."""
    payload = decode_access_token(token)
    if payload:
        jti = payload.get("jti")
        if jti and await db.get(RevokedToken, jti):
            return None, None
        try:
            user_id = uuid.UUID(str(payload["sub"]))
        except (ValueError, TypeError):
            return None, None
        user = await db.get(User, user_id)
        if not user:
            return None, None
        cutoff = getattr(user, "tokens_valid_after", None)
        if cutoff is not None:
            cutoff_ts = int(cutoff.replace(tzinfo=timezone.utc).timestamp())
            if int(payload.get("iat", 0)) < cutoff_ts:
                return None, None
        return user, payload

    # Legacy stateful sessions (64-char hex ids) – revocable server side.
    if len(token) == 64:
        record = (await db.execute(select(Session).where(Session.id == token))).scalar_one_or_none()
        if record:
            if utcnow() >= record.expires_at:
                await db.execute(delete(Session).where(Session.id == token))
                await db.commit()
                return None, None
            return await db.get(User, record.user_id), None
    return None, None


async def get_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    """Authenticate the caller as a user and/or the internal Next.js service. Raises 401 otherwise."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached

    is_internal = has_valid_internal_key(request)
    token, from_cookie = extract_token(request)

    user = None
    payload = None
    if token:
        user, payload = await resolve_user_from_token(db, token)
        if not user:
            raise _unauthorized("Invalid or expired session. Please sign in again.")
        # CSRF defence for cookie-authenticated browser requests: cross-site forms cannot set custom headers.
        if from_cookie and request.method.upper() not in SAFE_METHODS and not request.headers.get("x-requested-with"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing X-Requested-With header.")

    if not user and not is_internal:
        raise _unauthorized()

    principal = Principal(user=user, is_internal=is_internal, token_from_cookie=from_cookie, token_payload=payload)
    request.state.principal = principal
    return principal


async def verify_internal_api_key(principal: Principal = Depends(get_principal)) -> Principal:
    """Backwards-compatible name: caller must be an authenticated user or the internal service."""
    return principal


async def require_internal_service(request: Request) -> bool:
    """Only the Next.js server (holder of INTERNAL_API_KEY) may call this route."""
    if not has_valid_internal_key(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Internal endpoint.")
    return True


async def verify_cron_or_internal_key(request: Request) -> bool:
    """CRON_SECRET via `Authorization: Bearer` OR X-Internal-Key. Fails closed."""
    auth = request.headers.get("authorization") or ""
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    if has_valid_internal_key(request) or constant_time_equals(bearer, settings.CRON_SECRET):
        return True
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized cron trigger.",
    )


async def get_current_user(principal: Principal = Depends(get_principal)) -> User:
    """The authenticated end user. The internal key alone is not a user."""
    if not principal.user:
        raise _unauthorized("A signed-in user is required.")
    return principal.user


async def require_super_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required.")
    return user


def get_acting_user_id(request: Request) -> Optional[uuid.UUID]:
    """User id of the authenticated caller (never taken from request parameters)."""
    principal: Optional[Principal] = getattr(request.state, "principal", None)
    return principal.user.id if principal and principal.user else None
