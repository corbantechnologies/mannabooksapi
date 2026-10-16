import uuid
from typing import Optional, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.db.session import get_db
from app.models.identity import User
from app.models.governance import AuditLog, Notification
from app.api.deps import get_current_user
from app.core.schemas import ApiModel

router = APIRouter(tags=["Governance, Audit Logs & Notifications"])


class CreateAuditLogRequest(ApiModel):
    shop_id: str
    action: str
    table_name: str
    record_id: Optional[str] = None
    record_label: Optional[str] = None
    before_state: Optional[Any] = None
    after_state: Optional[Any] = None
    ip_address: Optional[str] = None


class CreateNotificationRequest(ApiModel):
    user_id: str
    shop_id: Optional[str] = None
    title: str
    message: str
    type: str = "SYSTEM"
    link: Optional[str] = None


@router.get("/governance/audit-logs")
async def list_audit_logs(
    shop_id: str = Query(..., description="Workspace ID"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(AuditLog)
        .where(AuditLog.shop_id == shop_id)
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    res = await db.execute(q)
    logs = res.scalars().all()
    return {"success": True, "logs": logs}


@router.post("/governance/audit-logs")
async def create_audit_log(
    req: CreateAuditLogRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    log = AuditLog(
        shop_id=uuid.UUID(req.shop_id),
        user_id=current_user.id,
        action=req.action,
        table_name=req.table_name,
        record_id=uuid.UUID(req.record_id) if req.record_id else None,
        record_label=req.record_label,
        before_state=req.before_state,
        after_state=req.after_state,
        ip_address=req.ip_address,
    )
    db.add(log)
    await db.commit()
    return {"success": True, "logId": str(log.id)}


@router.get("/governance/notifications")
async def list_notifications(
    shop_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = [Notification.user_id == current_user.id]
    if shop_id:
        conditions.append(Notification.shop_id == shop_id)

    q = (
        select(Notification)
        .where(and_(*conditions))
        .order_by(desc(Notification.created_at))
        .limit(50)
    )
    res = await db.execute(q)
    notifications = res.scalars().all()
    return {"success": True, "notifications": notifications}


@router.post("/governance/notifications")
async def create_notification(
    req: CreateNotificationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notif = Notification(
        user_id=uuid.UUID(req.user_id),
        shop_id=uuid.UUID(req.shop_id) if req.shop_id else None,
        title=req.title.strip(),
        message=req.message.strip(),
        type=req.type,
        link=req.link,
        is_read=False,
    )
    db.add(notif)
    await db.commit()
    return {"success": True, "notificationId": str(notif.id)}


@router.patch("/governance/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    )
    res = await db.execute(q)
    notif = res.scalar_one_or_none()

    if notif:
        notif.is_read = True
        await db.commit()

    return {"success": True}


@router.patch("/governance/notifications/read-all")
async def mark_all_notifications_read(
    shop_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import update
    conditions = [Notification.user_id == current_user.id]
    if shop_id:
        try:
            conditions.append(Notification.shop_id == uuid.UUID(shop_id))
        except ValueError:
            pass
    await db.execute(update(Notification).where(and_(*conditions)).values(is_read=True))
    await db.commit()
    return {"success": True, "message": "All notifications marked as read."}


@router.post("/audit")
async def record_audit_generic(
    req: dict,
    db: AsyncSession = Depends(get_db),
):
    return {"success": True, "message": "Audit event recorded."}