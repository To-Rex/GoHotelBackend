"""Talab, taklif va shikoyatlar — mehmon murojaatlari kitobi endpointlari.

Kim nimani qila olishi feedback_service.py da bitta joyda yozilgan
(can_view / can_create / can_manage / can_delete) — bu yerda faqat
chaqiriladi. Qabulxona va menejer yangi ruxsat berilmasdan ishlaydi.
"""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.feedback import (
    FeedbackCreateRequest,
    FeedbackStatusRequest,
    FeedbackUpdateRequest,
)
from app.application.services import feedback_service as rules
from app.application.services.feedback_service import FeedbackService
from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.presentation.api.v1._deps import require_active_hotel
from app.presentation.middleware.auth import get_current_user

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _hotel_id(current_user: dict, hotel_id: UUID | None) -> UUID:
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = current_user.get("hotel_id")
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


def _require_view(current_user: dict) -> None:
    if not rules.can_view(current_user):
        raise ForbiddenException(
            "Murojaatlar bo'limi qabulxona va menejer uchun", "FEEDBACK_VIEW_DENIED"
        )


def _require_create(current_user: dict) -> None:
    if not rules.can_create(current_user):
        raise ForbiddenException(
            "Murojaat kiritish uchun ruxsat yo'q", "FEEDBACK_CREATE_DENIED"
        )


def _require_manage(current_user: dict) -> None:
    if not rules.can_manage(current_user):
        raise ForbiddenException(
            "Murojaat holatini faqat menejer yoki qabulxona o'zgartira oladi",
            "FEEDBACK_MANAGE_DENIED",
        )


def _require_delete(current_user: dict) -> None:
    if not rules.can_delete(current_user):
        raise ForbiddenException(
            "Murojaatni faqat administrator o'chira oladi", "FEEDBACK_DELETE_DENIED"
        )


@router.get("/")
async def list_feedback(
    status: str | None = Query(default=None),
    feedback_type: str | None = Query(default=None),
    assigned_to: UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    query: str | None = Query(default=None, max_length=200),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ro'yxat — eng yangisi birinchi. `items` + `total` qaytaradi."""
    _require_view(current_user)
    h_id = _hotel_id(current_user, hotel_id)
    service = FeedbackService(session)
    items, total = await service.list(
        h_id,
        status=status,
        feedback_type=feedback_type,
        assigned_to=assigned_to,
        date_from=date_from,
        date_to=date_to,
        query=query,
        skip=skip,
        limit=limit,
    )
    return {"items": await service.serialize(items), "total": total}


@router.post("/", status_code=201)
async def create_feedback(
    data: FeedbackCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Yangi murojaat. Shikoyat bo'lsa menejerlarga push ketadi."""
    _require_create(current_user)
    h_id = _hotel_id(current_user, hotel_id)
    service = FeedbackService(session)
    fb = await service.create(
        h_id,
        data.model_dump(),
        created_by=current_user["id"],
        default_branch_id=current_user.get("branch_id"),
    )
    return (await service.serialize([fb]))[0]


@router.get("/{feedback_id}")
async def get_feedback(
    feedback_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_view(current_user)
    h_id = _hotel_id(current_user, hotel_id)
    service = FeedbackService(session)
    fb = await service.get(feedback_id, h_id)
    return (await service.serialize([fb]))[0]


@router.patch("/{feedback_id}")
async def update_feedback(
    data: FeedbackUpdateRequest,
    feedback_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Matn, tur, muhimlik, bog'lamalar, mas'ul — faqat yuborilgan maydonlar."""
    _require_manage(current_user)
    h_id = _hotel_id(current_user, hotel_id)
    service = FeedbackService(session)
    fb = await service.update(
        feedback_id, h_id, data.model_dump(exclude_unset=True), actor=current_user["id"]
    )
    return (await service.serialize([fb]))[0]


@router.patch("/{feedback_id}/status")
async def set_feedback_status(
    data: FeedbackStatusRequest,
    feedback_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """NEW → IN_PROGRESS → RESOLVED/REJECTED; yopilgani qayta ochiladi.
    Yopishda `resolution` shart."""
    _require_manage(current_user)
    h_id = _hotel_id(current_user, hotel_id)
    service = FeedbackService(session)
    fb = await service.set_status(
        feedback_id, h_id, data.status, data.resolution, actor=current_user["id"]
    )
    return (await service.serialize([fb]))[0]


@router.delete("/{feedback_id}")
async def delete_feedback(
    feedback_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_delete(current_user)
    h_id = _hotel_id(current_user, hotel_id)
    await FeedbackService(session).delete(feedback_id, h_id)
    return {"message": "Feedback deleted"}
