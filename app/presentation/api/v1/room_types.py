from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.room_service import RoomService
from app.application.dto.room import RoomTypeCreateRequest, RoomTypeUpdateRequest, RoomTypeResponse
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter()


@router.get("/", response_model=list[RoomTypeResponse])
async def list_room_types(
    active_only: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = RoomService(session)
    return await service.get_room_types(active_only=active_only)


@router.post("/", response_model=RoomTypeResponse)
async def create_room_type(
    data: RoomTypeCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.create")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can manage room types")
    service = RoomService(session)
    return await service.create_room_type(data.model_dump())


@router.get("/{type_id}", response_model=RoomTypeResponse)
async def get_room_type(
    type_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = RoomService(session)
    return await service.get_room_type(type_id)


@router.put("/{type_id}", response_model=RoomTypeResponse)
async def update_room_type(
    type_id: UUID = Path(),
    data: RoomTypeUpdateRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.update")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can manage room types")
    service = RoomService(session)
    return await service.update_room_type(type_id, data.model_dump(exclude_none=True))


@router.delete("/{type_id}", response_model=MessageResponse)
async def delete_room_type(
    type_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.delete")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can manage room types")
    service = RoomService(session)
    await service.delete_room_type(type_id)
    return {"message": "Room type deleted"}


@router.patch("/{type_id}/status", response_model=RoomTypeResponse)
async def update_room_type_status(
    type_id: UUID = Path(),
    is_active: bool = Query(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("room_type.update")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can manage room types")
    service = RoomService(session)
    return await service.update_room_type(type_id, {"is_active": is_active})
