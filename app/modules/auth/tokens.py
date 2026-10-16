# app/modules/auth/tokens.py
"""
Session tokens: a short-lived JWT access token plus a rotating, single-use refresh token.

- Access token: JWT_EXPIRE_MINUTES (15 min). Stateless, revocable via jti denylist / tokens_valid_after.
- Refresh token: random 256-bit value, stored only as a SHA-256 hash. Every use rotates it.
  * Absolute lifetime: REFRESH_TOKEN_DAYS from sign-in (the family's expires_at).
  * Idle timeout: SESSION_IDLE_HOURS since the token was issued (i.e. since the last refresh).
  * Reuse of a rotated token after REFRESH_REUSE_GRACE_SECONDS revokes the whole family.
"""
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, hash_token
from app.models.identity import RefreshToken, User
from app.core.datetime_utils import utcnow

logger = logging.getLogger("mannabooks.auth")


class RefreshError(Exception):
    """The refresh token cannot be used; the client must sign in again."""


@dataclass
class IssuedSession:
    access_token: str
    refresh_token: str
    expires_in: int  # access token lifetime in seconds
    refresh_expires_in: int  # seconds until the family's absolute expiry


def _access_lifetime_seconds() -> int:
    return get_settings().JWT_EXPIRE_MINUTES * 60


async def _new_refresh_row(
    db: AsyncSession,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    family_expires_at: datetime,
    ip: Optional[str],
    user_agent: Optional[str],
) -> tuple[RefreshToken, str]:
    raw = secrets.token_urlsafe(32)
    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_token(raw),
        created_at=utcnow(),
        expires_at=family_expires_at,
        ip_address=(ip or "")[:64] or None,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(row)
    return row, raw


def _issued(user_id: uuid.UUID, raw_refresh: str, family_expires_at: datetime) -> IssuedSession:
    return IssuedSession(
        access_token=create_access_token(subject=str(user_id)),
        refresh_token=raw_refresh,
        expires_in=_access_lifetime_seconds(),
        refresh_expires_in=max(0, int((family_expires_at - utcnow()).total_seconds())),
    )


async def start_session(
    db: AsyncSession, user_id: uuid.UUID, ip: Optional[str] = None, user_agent: Optional[str] = None
) -> IssuedSession:
    """Sign-in: start a new refresh family. Caller commits."""
    family_expires_at = utcnow() + timedelta(days=get_settings().REFRESH_TOKEN_DAYS)
    _, raw = await _new_refresh_row(db, user_id, uuid.uuid4(), family_expires_at, ip, user_agent)
    await db.flush()
    return _issued(user_id, raw, family_expires_at)


async def revoke_family(db: AsyncSession, family_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoke_reason=reason)
    )


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoke_reason=reason)
    )


async def find_family(db: AsyncSession, raw_refresh: str) -> Optional[uuid.UUID]:
    if not raw_refresh:
        return None
    return (
        await db.execute(select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_token(raw_refresh)))
    ).scalar_one_or_none()


async def rotate(
    db: AsyncSession, raw_refresh: str, ip: Optional[str] = None, user_agent: Optional[str] = None
) -> tuple[User, IssuedSession]:
    """Exchange a refresh token for a new access + refresh token. Commits. Raises RefreshError."""
    settings = get_settings()
    now = utcnow()

    row = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh or "")).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise RefreshError("unknown token")

    if row.revoked_at is not None:
        within_grace = (now - row.revoked_at).total_seconds() <= settings.REFRESH_REUSE_GRACE_SECONDS
        if row.revoke_reason == "rotated" and within_grace:
            # Several requests from the same browser refreshed at once; issue a sibling token.
            pass
        else:
            if row.revoke_reason == "rotated":
                logger.warning("Refresh token reuse detected: user=%s family=%s", row.user_id, row.family_id)
                await revoke_family(db, row.family_id, "reuse")
                await db.commit()
            raise RefreshError("revoked")

    if now >= row.expires_at:
        raise RefreshError("session expired")
    if now - row.created_at > timedelta(hours=settings.SESSION_IDLE_HOURS):
        await revoke_family(db, row.family_id, "idle")
        await db.commit()
        raise RefreshError("idle timeout")

    user = await db.get(User, row.user_id)
    if user is None:
        raise RefreshError("user gone")
    cutoff = getattr(user, "tokens_valid_after", None)
    if cutoff is not None and row.created_at < cutoff:
        raise RefreshError("signed out everywhere")

    new_row, raw = await _new_refresh_row(db, row.user_id, row.family_id, row.expires_at, ip, user_agent)
    if row.revoked_at is None:
        row.revoked_at = now
        row.revoke_reason = "rotated"
        row.replaced_by_id = new_row.id
    await db.commit()
    return user, _issued(row.user_id, raw, row.expires_at)
