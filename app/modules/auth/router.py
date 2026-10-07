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
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=SessionResponse, dependencies=[Depends(verify_internal_api_key)])
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


@router.get("/me", dependencies=[Depends(verify_internal_api_key)])
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


@router.post("/logout", dependencies=[Depends(verify_internal_api_key)])
async def logout_endpoint(
    authorization: str = Header(..., description="Bearer <session_token>"),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate session in database."""
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        await invalidate_session(db, parts[1])
    return {"success": True}
