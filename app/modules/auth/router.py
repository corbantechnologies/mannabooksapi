import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_internal_service, has_valid_internal_key
from app.core.rate_limit import client_ip, hit, reset
from app.core.security import decode_access_token, hash_token
from app.models.identity import User, RevokedToken, PasswordResetToken, Session
from app.modules.auth.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterOwnerRequest,
    ResetPasswordRequest,
    SessionResponse,
    UserProfileResponse,
)
from app.modules.auth.service import (
    verify_password,
    hash_password,
    invalidate_session,
    register_owner_account,
)
from app.modules.auth.tokens import (
    IssuedSession,
    RefreshError,
    find_family,
    revoke_all_for_user,
    revoke_family,
    rotate,
    start_session,
)
from app.core.datetime_utils import utcnow
from app.core.schemas import ApiModel

router = APIRouter(prefix="/auth", tags=["Authentication"])

INVALID_CREDENTIALS = "Invalid email address or password."
# Used to keep login timing identical whether or not the email exists.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


class RefreshRequest(ApiModel):
    refresh_token: str = Field(..., min_length=20, max_length=200)


class LogoutRequest(ApiModel):
    refresh_token: str | None = Field(None, max_length=200)


def _client_meta(request: Request) -> tuple[str, str | None]:
    """IP and user agent of the end user (forwarded by the Next.js server when it is the caller)."""
    ua = request.headers.get("x-client-user-agent") if has_valid_internal_key(request) else None
    return client_ip(request), (ua or request.headers.get("user-agent"))


def _token_payload(session: IssuedSession) -> dict:
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "token_type": "bearer",
        "expires_in": session.expires_in,
        "refresh_expires_in": session.refresh_expires_in,
    }


def _profile(user: User) -> UserProfileResponse:
    return UserProfileResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        is_super_admin=user.is_super_admin,
        plan=user.plan,
        subscription_status=user.subscription_status,
    )


async def _authenticate(request: Request, db: AsyncSession, req: LoginRequest) -> User:
    email = req.email.lower().strip()
    ip = client_ip(request)
    await hit(f"login:ip:{ip}", limit=30, window_seconds=15 * 60)
    await hit(f"login:email:{email}", limit=10, window_seconds=15 * 60)

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    password_ok = verify_password(req.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not password_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_CREDENTIALS)

    await reset(f"login:email:{email}")
    return user


@router.post("/register", response_model=SessionResponse)
async def register_endpoint(
    request: Request,
    req: RegisterOwnerRequest,
    db: AsyncSession = Depends(get_db),
):
    await hit(f"register:ip:{client_ip(request)}", limit=5, window_seconds=60 * 60)
    resp = await register_owner_account(db, req)
    if resp.success and resp.user:
        ip, ua = _client_meta(request)
        session = await start_session(db, resp.user.id, ip, ua)
        await db.commit()
        resp.token = session.access_token
        resp.refresh_token = session.refresh_token
        resp.expires_in = session.expires_in
        resp.refresh_expires_in = session.refresh_expires_in
    return resp


@router.post("/login", response_model=SessionResponse)
async def login_endpoint(
    request: Request,
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        user = await _authenticate(request, db, req)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            return SessionResponse(success=False, error=INVALID_CREDENTIALS)
        raise
    ip, ua = _client_meta(request)
    session = await start_session(db, user.id, ip, ua)
    await db.commit()
    return SessionResponse(
        success=True,
        token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
        refresh_expires_in=session.refresh_expires_in,
        user=_profile(user),
    )


@router.post("/token")
async def token_endpoint(
    request: Request,
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    user = await _authenticate(request, db, req)
    ip, ua = _client_meta(request)
    session = await start_session(db, user.id, ip, ua)
    await db.commit()
    return {
        **_token_payload(session),
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "is_super_admin": user.is_super_admin,
            "plan": user.plan,
            "subscription_status": user.subscription_status,
        },
    }


@router.get("/me")
async def get_current_user_endpoint(
    current_user: User = Depends(get_current_user),
):
    return {
        "success": True,
        "user": {
            "id": str(current_user.id),
            "name": current_user.name,
            "email": current_user.email,
            "isSuperAdmin": current_user.is_super_admin,
            "plan": current_user.plan,
            "subscriptionStatus": current_user.subscription_status,
        },
    }


@router.post("/refresh", dependencies=[Depends(require_internal_service)])
async def refresh_endpoint(
    request: Request,
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    INTERNAL ONLY (Next.js server). Rotate a refresh token: returns a new access token and a new
    refresh token; the old refresh token stops working.
    """
    ip, ua = _client_meta(request)
    await hit(f"refresh:ip:{ip}", limit=120, window_seconds=15 * 60)
    try:
        _, session = await rotate(db, req.refresh_token, ip, ua)
    except RefreshError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has ended. Please sign in again.",
        )
    return _token_payload(session)


@router.post("/logout")
async def logout_endpoint(
    request: Request,
    req: LogoutRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Sign out this device: revoke the refresh-token family (internal caller only) and denylist the
    access token's jti. Works even when the access token has already expired.
    """
    if req and req.refresh_token and has_valid_internal_key(request):
        family_id = await find_family(db, req.refresh_token)
        if family_id:
            await revoke_family(db, family_id, "logout")

    auth = request.headers.get("authorization", "")
    parts = auth.split()
    token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else request.cookies.get("access_token")
    payload = decode_access_token(token) if token else None
    if payload and payload.get("jti"):
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc).replace(tzinfo=None)
        if not await db.get(RevokedToken, payload["jti"]):
            db.add(RevokedToken(jti=payload["jti"], expires_at=expires_at))
        # Opportunistic cleanup of expired denylist rows.
        await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))
    elif token and len(token) == 64:
        await invalidate_session(db, token)

    await db.commit()
    return {"success": True}


@router.post("/logout-all")
async def logout_all_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate every token issued to this user so far (all devices)."""
    await db.execute(
        update(User).where(User.id == current_user.id).values(tokens_valid_after=utcnow())
    )
    await db.execute(delete(Session).where(Session.user_id == current_user.id))
    await revoke_all_for_user(db, current_user.id, "logout_all")
    await db.commit()
    return {"success": True}


@router.post("/forgot-password", dependencies=[Depends(require_internal_service)])
async def forgot_password_endpoint(
    request: Request,
    req: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    INTERNAL ONLY. Called by the Next.js server, which emails the link itself.
    The raw token is returned to that trusted caller and never stored; only its hash is persisted.
    Browsers must use the Next.js /api/auth/forgot-password route, which always answers generically.
    """
    email = req.email.lower().strip()
    await hit(f"forgot:email:{email}", limit=3, window_seconds=60 * 60)
    await hit(f"forgot:ip:{client_ip(request)}", limit=10, window_seconds=60 * 60)

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        return {"success": True}

    await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    raw_token = secrets.token_urlsafe(32)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token=hash_token(raw_token),
            expires_at=utcnow() + timedelta(hours=1),
        )
    )
    await db.commit()

    return {
        "success": True,
        "token": raw_token,
        "userName": user.name,
        "email": user.email,
    }


@router.post("/reset-password")
async def reset_password_endpoint(
    request: Request,
    req: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    await hit(f"reset:ip:{client_ip(request)}", limit=10, window_seconds=15 * 60)

    token_match = (
        await db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token == hash_token(req.token),
                PasswordResetToken.expires_at > utcnow(),
            )
        )
    ).scalar_one_or_none()

    if not token_match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The reset link is invalid or has expired. Please request a new one.",
        )

    user = await db.get(User, token_match.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The reset link is invalid or has expired. Please request a new one.",
        )

    user.password_hash = hash_password(req.new_password)
    # Sign out every existing session for this account.
    user.tokens_valid_after = utcnow()
    await db.execute(delete(Session).where(Session.user_id == user.id))
    await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    await revoke_all_for_user(db, user.id, "password_reset")
    await db.commit()

    return {"success": True}
