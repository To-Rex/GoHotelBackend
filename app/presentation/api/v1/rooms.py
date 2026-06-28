from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.room import Room
from app.infrastructure.database.models.branch import Branch
from app.application.services.room_service import RoomService
from app.application.services.amenity_service import AmenityService
from app.application.dto.room import (
    RoomCreateRequest,
    RoomUpdateRequest,
    RoomStatusUpdateRequest,
    RoomResponse,
    RoomDetailResponse,
    RoomStatusHistoryResponse,
)
from app.application.dto.amenity import RoomAmenityRequest
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


@router.get("/", response_model=list[RoomResponse])
async def list_rooms(
    branch_id: UUID | None = Query(default=None),
    floor_id: UUID | None = Query(default=None),
    room_type_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = RoomService(session)
    return await service.get_rooms(
        h_id,
        branch_id=branch_id,
        floor_id=floor_id,
        room_type_id=room_type_id,
        status=status,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=RoomResponse)
async def create_room(
    data: RoomCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("rooms.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            branch = await session.get(Branch, data.branch_id)
            if not branch:
                raise NotFoundException("Branch not found", "BRANCH_NOT_FOUND")
            h_id = branch.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = RoomService(session)
    return await service.create_room(h_id, data.branch_id, data.model_dump())


@router.get("/available", response_model=list[RoomResponse])
async def get_available_rooms(
    check_in: date | None = Query(default=None),
    check_out: date | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    room_type_id: UUID | None = Query(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = RoomService(session)
    return await service.get_available_rooms(h_id, check_in, check_out, branch_id, room_type_id)


@router.get("/{room_id}", response_model=RoomResponse)
async def get_room(
    room_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = RoomService(session)
    room = await service.get_room(room_id, h_id)
    return room


@router.put("/{room_id}", response_model=RoomResponse)
async def update_room(
    room_id: UUID = Path(),
    data: RoomUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("rooms.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            room = await session.get(Room, room_id)
            if not room:
                raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
            h_id = room.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = RoomService(session)
    return await service.update_room(room_id, h_id, data.model_dump(exclude_none=True))


@router.patch("/{room_id}/status", response_model=RoomResponse)
async def update_room_status(
    room_id: UUID = Path(),
    data: RoomStatusUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("rooms.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            room = await session.get(Room, room_id)
            if not room:
                raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
            h_id = room.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = RoomService(session)
    return await service.update_room_status(
        room_id, h_id, data.status, current_user["id"], notes=data.notes
    )


@router.get("/{room_id}/status-history", response_model=list[RoomStatusHistoryResponse])
async def get_room_status_history(
    room_id: UUID = Path(),
    limit: int = Query(default=50, ge=1, le=200),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = RoomService(session)
    return await service.get_status_history(room_id, h_id, limit=limit)


@router.delete("/{room_id}", response_model=MessageResponse)
async def delete_room(
    room_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("rooms.delete")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            room = await session.get(Room, room_id)
            if not room:
                raise NotFoundException("Room not found", "ROOM_NOT_FOUND")
            h_id = room.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = RoomService(session)
    await service.soft_delete_room(room_id, h_id)
    return {"message": "Room deleted"}


@router.post("/{room_id}/amenities", response_model=MessageResponse)
async def add_room_amenity(
    room_id: UUID = Path(),
    data: RoomAmenityRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("rooms.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = AmenityService(session)
    await service.add_room_amenity(room_id, data.amenity_id, h_id)
    return {"message": "Amenity added to room"}


@router.delete("/{room_id}/amenities/{amenity_id}", response_model=MessageResponse)
async def remove_room_amenity(
    room_id: UUID = Path(),
    amenity_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("rooms.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = AmenityService(session)
    await service.remove_room_amenity(room_id, amenity_id, h_id)
    return {"message": "Amenity removed from room"}
