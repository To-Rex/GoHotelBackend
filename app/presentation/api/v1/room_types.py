from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.application.services.room_service import RoomService
from app.application.dto.room import RoomTypeCreateRequest, RoomTypeUpdateRequest, RoomTypeResponse
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter()


def _resolve_hotel_id(current_user: dict, hotel_id: UUID | None) -> UUID | None:
    """Xona turlari mehmonxonaga tegishli: SUPER_ADMIN query yoki o'z
    konteksti bilan ishlaydi, qolganlar doim o'z mehmonxonasi bilan."""
    if current_user["user_type"] == "SUPER_ADMIN":
        return hotel_id or current_user.get("hotel_id")
    h_id = current_user.get("hotel_id")
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


async def _get_owned_room_type(
    service: RoomService, type_id: UUID, current_user: dict
):
    """Turni yuklab, chaqiruvchining mehmonxonasiga tegishliligini tekshiradi.
    SUPER_ADMIN istalgan turga tega oladi; boshqalar faqat o'znikiga —
    begonasi mavjudligi ham oshkor qilinmaydi (404)."""
    rt = await service.get_room_type(type_id)
    if current_user["user_type"] != "SUPER_ADMIN":
        h_id = current_user.get("hotel_id")
        if not h_id or str(rt.hotel_id) != str(h_id):
            raise NotFoundException("Room type not found", "ROOM_TYPE_NOT_FOUND")
    return rt


@router.get("/", response_model=list[RoomTypeResponse])
async def list_room_types(
    active_only: bool = Query(default=False),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    h_id = _resolve_hotel_id(current_user, hotel_id)
    service = RoomService(session)
    return await service.get_room_types(active_only=active_only, hotel_id=h_id)


@router.post("/", response_model=RoomTypeResponse)
async def create_room_type(
    data: RoomTypeCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.create")),
):
    # ADMIN ham o'z mehmonxonasi uchun xona turlarini yarata oladi
    if current_user["user_type"] not in ("SUPER_ADMIN", "ADMIN"):
        raise ForbiddenException("Only SUPER_ADMIN or ADMIN can manage room types")
    h_id = _resolve_hotel_id(current_user, hotel_id)
    if not h_id:
        raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
    service = RoomService(session)
    return await service.create_room_type(data.model_dump(), hotel_id=h_id)


@router.get("/{type_id}", response_model=RoomTypeResponse)
async def get_room_type(
    type_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = RoomService(session)
    return await _get_owned_room_type(service, type_id, current_user)


@router.put("/{type_id}", response_model=RoomTypeResponse)
async def update_room_type(
    type_id: UUID = Path(),
    data: RoomTypeUpdateRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.update")),
):
    if current_user["user_type"] not in ("SUPER_ADMIN", "ADMIN"):
        raise ForbiddenException("Only SUPER_ADMIN or ADMIN can manage room types")
    service = RoomService(session)
    await _get_owned_room_type(service, type_id, current_user)
    return await service.update_room_type(type_id, data.model_dump(exclude_none=True))


@router.delete("/{type_id}", response_model=MessageResponse)
async def delete_room_type(
    type_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.delete")),
):
    if current_user["user_type"] not in ("SUPER_ADMIN", "ADMIN"):
        raise ForbiddenException("Only SUPER_ADMIN or ADMIN can manage room types")
    service = RoomService(session)
    await _get_owned_room_type(service, type_id, current_user)
    await service.delete_room_type(type_id)
    return {"message": "Room type deleted"}


@router.patch("/{type_id}/status", response_model=RoomTypeResponse)
async def update_room_type_status(
    type_id: UUID = Path(),
    is_active: bool = Query(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.update")),
):
    if current_user["user_type"] not in ("SUPER_ADMIN", "ADMIN"):
        raise ForbiddenException("Only SUPER_ADMIN or ADMIN can manage room types")
    service = RoomService(session)
    await _get_owned_room_type(service, type_id, current_user)
    return await service.update_room_type(type_id, {"is_active": is_active})
