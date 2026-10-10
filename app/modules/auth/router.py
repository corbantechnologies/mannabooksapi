from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.api.deps import verify_internal_api_key
from app.models.identity import User
from app.modules.auth.schemas import (
    LoginRequest,
    RegisterOwnerRequest,
    SessionResponse,
    UserProfileResponse,
)
from app.modules.auth.service import (
    verify_password,
    create_session,
    verify_and_get_session,
    invalidate_session,
    register_owner_account,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=SessionResponse)
async def register_endpoint(
    req: RegisterOwnerRequest,
    db: AsyncSession = Depends(get_db),
):
    """Registers new merchant account, provisions shop workspace, and returns session token."""
    return await register_owner_account(db, req)


@router.post("/login", response_model=SessionResponse)
async def login_endpoint(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate email & password, producing session token."""
    q = select(User).where(User.email == req.email.lower().strip())
    res = await db.execute(q)
    user = res.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        return SessionResponse(success=False, error="Invalid email address or password.")

    token, _ = await create_session(db, user.id)

    return SessionResponse(
        success=True,
        token=token,
        user=UserProfileResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            is_super_admin=user.is_super_admin,
            plan=user.plan,
            subscription_status=user.subscription_status,
        ),
    )


@router.get("/me")
async def get_current_user_endpoint(
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Validate active session token and return user profile."""
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header format")

    token = parts[1]
    user = await verify_and_get_session(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")

    return {
        "success": True,
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "isSuperAdmin": user.is_super_admin,
            "plan": user.plan,
            "subscriptionStatus": user.subscription_status,
        },
    }


@router.post("/logout")
async def logout_endpoint(
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate session in database."""
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        await invalidate_session(db, parts[1])
    return {"success": True}


from pydantic import BaseModel


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/forgot-password")
async def forgot_password_endpoint(
    req: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate secure password reset token and return details for notification."""
    import secrets
    from datetime import datetime, timedelta
    from app.models.identity import PasswordResetToken
    from sqlalchemy import delete

    clean_email = req.email.lower().strip()
    q = select(User).where(User.email == clean_email)
    res = await db.execute(q)
    user = res.scalar_one_or_none()

    if not user:
        return {"success": True}

    del_stmt = delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    await db.execute(del_stmt)

    raw_token = secrets.token_hex(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)

    reset_rec = PasswordResetToken(
        user_id=user.id,
        token=raw_token,
        expires_at=expires_at,
    )
    db.add(reset_rec)
    await db.commit()

    return {
        "success": True,
        "token": raw_token,
        "userName": user.name,
        "email": user.email,
    }


@router.post("/reset-password")
async def reset_password_endpoint(
    req: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Validate reset token and update user password."""
    from datetime import datetime
    from app.models.identity import PasswordResetToken
    from app.modules.auth.service import hash_password

    now = datetime.utcnow()
    q = (
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token == req.token,
            PasswordResetToken.expires_at > now,
        )
    )
    res = await db.execute(q)
    token_match = res.scalar_one_or_none()

    if not token_match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The reset link is invalid or has expired. Please request a new one."
        )

    q_user = select(User).where(User.id == token_match.user_id)
    user = (await db.execute(q_user)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password_hash = hash_password(req.new_password)
    await db.delete(token_match)
    await db.commit()

    return {"success": True}

