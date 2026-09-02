from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.hotel import Hotel
from app.infrastructure.database.models.housekeeping import HousekeepingTask
from app.application.services.housekeeping_service import (
    HousekeepingService,
    HK_AUTO_COMPLETE_DEFAULTS,
    HK_SETTINGS_KEY,
    resolve_auto_complete_minutes,
)
from app.application.dto.housekeeping import (
    TaskCreateRequest,
    TaskUpdateRequest,
    TaskStatusUpdateRequest,
    TaskAssignRequest,
    TaskResponse,
)
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission
from app.presentation.api.v1._deps import require_active_hotel

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


class AutoCompleteSettingsRequest(BaseModel):
    """Vazifa turlari bo'yicha avtomatik yakunlash vaqtlari (daqiqa, 0=o'chirilgan)."""

    durations: dict[str, int] = Field(default_factory=dict)


@router.get("/auto-complete-settings")
async def get_auto_complete_settings(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Joriy mehmonxonaning avto-yakunlash vaqtlari (standartlar bilan birga)."""
    h_id = hotel_id if current_user["user_type"] == "SUPER_ADMIN" and hotel_id else _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    hotel_settings = (hotel.settings if hotel else {}) or {}
    durations = {
        task_type: resolve_auto_complete_minutes(hotel_settings, task_type)
        for task_type in HK_AUTO_COMPLETE_DEFAULTS
    }
    return {"durations": durations, "defaults": HK_AUTO_COMPLETE_DEFAULTS}


@router.put("/auto-complete-settings")
async def save_auto_complete_settings(
    data: AutoCompleteSettingsRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Avto-yakunlash vaqtlarini saqlash — faqat ADMIN/SUPER_ADMIN."""
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException("Only admins can change auto-complete settings")
    h_id = hotel_id if current_user["user_type"] == "SUPER_ADMIN" and hotel_id else _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    if not hotel:
        raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")

    cleaned: dict[str, int] = {}
    for task_type in HK_AUTO_COMPLETE_DEFAULTS:
        if task_type in data.durations:
            value = int(data.durations[task_type])
            if value < 0 or value > 1440:
                raise ForbiddenException("Duration must be between 0 and 1440 minutes")
            cleaned[task_type] = value

    # JSONB ustunini YANGI dict bilan almashtiramiz — SQLAlchemy o'zgarishni
    # sezishi uchun (ichki mutatsiya kuzatilmaydi)
    new_settings = dict(hotel.settings or {})
    new_settings[HK_SETTINGS_KEY] = {**(new_settings.get(HK_SETTINGS_KEY) or {}), **cleaned}
    hotel.settings = new_settings
    await session.flush()

    durations = {
        task_type: resolve_auto_complete_minutes(new_settings, task_type)
        for task_type in HK_AUTO_COMPLETE_DEFAULTS
    }
    return {"durations": durations, "defaults": HK_AUTO_COMPLETE_DEFAULTS}


@router.get("/cleaning-report")
async def cleaning_report(
    date_from: date = Query(),
    date_to: date = Query(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Chiqishlar va tozalashlarni solishtirish.

    Davrda necha marta mehmon chiqdi va shundan nechtasi haqiqatan
    tozalandi. Batafsil izoh `cleaning_report_service` da.
    """
    from app.application.services.cleaning_report_service import (
        CleaningReportService,
    )

    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return await CleaningReportService(session).build(h_id, date_from, date_to)


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = Query(default=None),
    room_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    assigned_to: UUID | None = Query(default=None),
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
    service = HousekeepingService(session)
    return await service.get_tasks(
        h_id,
        skip=skip,
        limit=limit,
        status=status,
        room_id=room_id,
        branch_id=branch_id,
        assigned_to=assigned_to,
    )


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    data: TaskCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("housekeeping.task.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = HousekeepingService(session)
    return await service.create_task(h_id, data.model_dump(), current_user["id"])


@router.get("/tasks/my-tasks", response_model=list[TaskResponse])
async def get_my_tasks(
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
    service = HousekeepingService(session)
    return await service.get_my_tasks(h_id, current_user["id"], skip=skip, limit=limit)


@router.get("/tasks/open", response_model=list[TaskResponse])
async def get_open_tasks(
    branch_id: UUID | None = Query(default=None),
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
    service = HousekeepingService(session)
    return await service.get_open_tasks(h_id, branch_id, skip=skip, limit=limit)


@router.get("/occupied-rooms")
async def get_occupied_rooms(
    include_reserved: bool = Query(default=False),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Farrosh uchun: hozir band (CHECKED_IN) xonalar va chiqish vaqtlari.

    Chiqishga eng yaqin xona birinchi qaytadi (kechikkanlar undan ham oldin).
    `include_reserved=true` bo'lsa hali kirmagan (CONFIRMED) bronlar ham
    qo'shiladi. Har bir yozuvda expected_checkout, minutes_until_checkout va
    is_overdue maydonlari bor.
    """
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = HousekeepingService(session)
    return await service.get_occupied_rooms(h_id, include_reserved=include_reserved)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = HousekeepingService(session)
    return await service.get_task(task_id, h_id)


@router.put("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID = Path(),
    data: TaskUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("housekeeping.task.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            task = await session.get(HousekeepingTask, task_id)
            if not task:
                raise NotFoundException("Task not found", "TASK_NOT_FOUND")
            h_id = task.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = HousekeepingService(session)
    return await service.update_task(task_id, h_id, data.model_dump(exclude_none=True))


@router.patch("/tasks/{task_id}/status", response_model=TaskResponse)
async def update_task_status(
    task_id: UUID = Path(),
    data: TaskStatusUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("housekeeping.task.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            task = await session.get(HousekeepingTask, task_id)
            if not task:
                raise NotFoundException("Task not found", "TASK_NOT_FOUND")
            h_id = task.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = HousekeepingService(session)
    return await service.update_task_status(
        task_id, h_id, data.status, current_user["id"], data.notes
    )


@router.post("/tasks/{task_id}/assign", response_model=TaskResponse)
async def assign_task(
    task_id: UUID = Path(),
    data: TaskAssignRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("housekeeping.task.assign")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            task = await session.get(HousekeepingTask, task_id)
            if not task:
                raise NotFoundException("Task not found", "TASK_NOT_FOUND")
            h_id = task.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = HousekeepingService(session)
    return await service.assign_task(task_id, h_id, data.assigned_to)
