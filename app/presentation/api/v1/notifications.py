from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.application.services.notification_service import NotificationService
from app.application.dto.common import MessageResponse
from app.application.dto.notification import (
    NotificationSendRequest,
    NotificationBroadcastRequest,
    NotificationSendResponse,
    TestPushRequest,
    TestPushResponse,
    RegisterDeviceRequest,
    PushHealthResponse,
)
from app.infrastructure.database.models.notification import Notification
from app.infrastructure.database.models.user import User
from app.infrastructure.push import firebase
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


@router.post("/test-push", response_model=TestPushResponse)
async def test_push(data: TestPushRequest):
    """SINOV endpointi — FCM tokenga to'g'ridan-to'g'ri push yuboradi.

    Avtorizatsiya (bearer token) TALAB QILINMAYDI va DB'ga hech narsa yozilmaydi.
    Faqat push mexanizmini tekshirish uchun.
    """
    sent = await firebase.send_push(
        data.fcm_token, data.title, data.body, data=data.data
    )
    return {"success": sent > 0, "push_sent": sent}


@router.post("/register-device", response_model=MessageResponse)
async def register_device(
    data: RegisterDeviceRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Joriy foydalanuvchining FCM device tokenini saqlaydi/yangilaydi.

    Mobil ilova login'dan keyin va token yangilanganda chaqiradi. Shu bilan
    farrosh doim yaroqli token bilan turadi va vazifa push'lari yetib boradi.
    """
    user = await session.get(User, current_user["id"])
    if not user or user.is_deleted:
        raise NotFoundException("User not found", "USER_NOT_FOUND")
    user.fcm_token = data.fcm_token
    await session.flush()
    return {"message": "Device token registered"}


@router.get("/push-health", response_model=PushHealthResponse)
async def push_health(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Push (FCM) konfiguratsiyasi holati — nega push kelmayotganini aniqlash uchun.

    `configured=false` bo'lsa server Firebase kalitini topa olmayapti (deploy'da
    sir qo'yilmagan). `current_user_has_token=false` bo'lsa foydalanuvchida saqlangan
    FCM token yo'q (mobil ilova token yubormagan).
    """
    diag = firebase.diagnostics()
    token = await session.execute(
        select(User.fcm_token).where(User.id == current_user["id"])
    )
    diag["current_user_has_token"] = bool(token.scalar_one_or_none())
    return diag


def _require_sender(current_user: dict) -> None:
    """Notification yuborishga faqat ADMIN va SUPER_ADMIN haqli."""
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException(
            "Only ADMIN or SUPER_ADMIN can send notifications", "FORBIDDEN"
        )


@router.post("/send", response_model=NotificationSendResponse)
async def send_notification(
    data: NotificationSendRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bitta foydalanuvchiga push notification (+ DB yozuv) yuboradi."""
    _require_sender(current_user)

    target = await session.get(User, data.user_id)
    if not target or target.is_deleted:
        raise NotFoundException("User not found", "USER_NOT_FOUND")

    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or target.hotel_id
    else:
        h_id = current_user.get("hotel_id")
        if not h_id:
            raise ForbiddenException("Hotel context required")
        if target.hotel_id != h_id:
            raise ForbiddenException("User belongs to another hotel", "FORBIDDEN")
    if not h_id:
        raise ValidationException("hotel_id required", "HOTEL_ID_REQUIRED")

    service = NotificationService(session)
    notif, push_sent = await service.send_to_user(
        h_id, target.id, data.title, data.body, data.entity_type, data.entity_id
    )
    return {"success": True, "notification_id": notif.id, "push_sent": push_sent}


@router.post("/broadcast", response_model=NotificationSendResponse)
async def broadcast_notification(
    data: NotificationBroadcastRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mehmonxonaning barcha faol foydalanuvchilariga push notification yuboradi."""
    _require_sender(current_user)

    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
        if not h_id:
            raise ValidationException(
                "hotel_id required for SUPER_ADMIN broadcast", "HOTEL_ID_REQUIRED"
            )
    else:
        h_id = current_user.get("hotel_id")
        if not h_id:
            raise ForbiddenException("Hotel context required")

    service = NotificationService(session)
    notif, push_sent = await service.send_to_hotel(
        h_id, data.title, data.body, data.entity_type, data.entity_id
    )
    return {"success": True, "notification_id": notif.id, "push_sent": push_sent}


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
            "task_id": (
                str(n.entity_id)
                if n.entity_type == "task" and n.entity_id
                else None
            ),
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
            "task_id": (
                str(n.entity_id)
                if n.entity_type == "task" and n.entity_id
                else None
            ),
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
