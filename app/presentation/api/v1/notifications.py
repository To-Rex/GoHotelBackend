from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.notification_service import NotificationService
from app.application.dto.common import MessageResponse
from app.infrastructure.database.models.notification import Notification
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter(tags=["Notifications"])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


def _map_notification_type(n: Notification) -> str:
    if n.entity_type == "task":
        return "newTask"
    if n.entity_type == "problem":
        return "problemAccepted"
    if n.entity_type == "urgent":
        return "critical"
    if n.entity_type == "inventory":
        return "inventory"
    return "system"


@router.get("/")
async def get_notifications(
    unread_only: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = NotificationService(session)
    notifications = await service.get_my_notifications(
        current_user["id"], skip=skip, limit=limit, unread_only=unread_only
    )
    return [
        {
            "id": str(n.id),
            "type": _map_notification_type(n),
            "title": n.title,
            "message": n.body or "",
            "room_number": None,
            "timestamp": n.created_at.isoformat() if n.created_at else None,
            "is_read": n.is_read,
            "has_actions": n.entity_type in ("task", "problem"),
        }
        for n in notifications
    ]


@router.get("/broadcasts")
async def get_broadcasts(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = NotificationService(session)
    notifications = await service.get_hotel_notifications(h_id, skip=skip, limit=limit)
    return [
        {
            "id": str(n.id),
            "type": _map_notification_type(n),
            "title": n.title,
            "message": n.body or "",
            "room_number": None,
            "timestamp": n.created_at.isoformat() if n.created_at else None,
            "is_read": n.is_read,
            "has_actions": n.entity_type in ("task", "problem"),
        }
        for n in notifications
    ]


@router.put("/{notification_id}/read", response_model=MessageResponse)
async def mark_read(
    notification_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = NotificationService(session)
    await service.mark_read(notification_id)
    return {"message": "Notification marked as read"}


@router.put("/read-all", response_model=MessageResponse)
async def mark_all_read(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    stmt = (
        sa_update(Notification)
        .where(Notification.user_id == current_user["id"], Notification.is_read == False)
        .values(is_read=True)
    )
    await session.execute(stmt)
    await session.flush()
    return {"message": "All notifications marked as read"}
