from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models.hotel import Hotel
from app.application.services.hotel_service import HotelService
from app.application.services.amenity_service import AmenityService
from app.application.services.room_service import RoomService
from app.application.dto.hotel import (
    HotelCreateRequest,
    HotelUpdateRequest,
    HotelStatusUpdate,
    HotelResponse,
    HotelBriefResponse,
)
from app.application.dto.amenity import HotelAmenityRequest, AmenityResponse
from app.application.dto.room import HotelRoomTypeRequest, RoomTypeResponse
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter()


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


async def _ensure_hotel_access(current_user: dict, target_hotel_id: UUID, session: AsyncSession) -> None:
    user_type = current_user.get("user_type", "")
    if user_type == "SUPER_ADMIN":
        return
    user_hotel_id = current_user.get("hotel_id")
    if not user_hotel_id or str(user_hotel_id) != str(target_hotel_id):
        raise ForbiddenException("Access denied to this hotel")
    hotel = await session.get(Hotel, target_hotel_id)
    if hotel and hotel.status != "ACTIVE":
        raise ForbiddenException(f"Hotel is {hotel.status}. Operations are blocked.")


@router.post("/", response_model=HotelResponse)
async def create_hotel(
    data: HotelCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.create")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can create hotels")
    service = HotelService(session)
    hotel = await service.create_hotel(data.model_dump(), current_user["id"])
    return hotel


@router.get("/", response_model=list[HotelBriefResponse])
async def list_hotels(
    active_only: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
        if h_id:
            hotel = await HotelService(session).get_hotel(h_id)
            return [hotel]
    else:
        h_id = _get_hotel_id(current_user)
        if h_id:
            hotel = await HotelService(session).get_hotel(h_id)
            return [hotel]

    service = HotelService(session)
    return await service.get_hotels(skip=skip, limit=limit, active_only=active_only)


@router.get("/{hotel_id}", response_model=HotelResponse)
async def get_hotel(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = HotelService(session)
    return await service.get_hotel(hotel_id)


@router.put("/{hotel_id}", response_model=HotelResponse)
async def update_hotel(
    hotel_id: UUID = Path(),
    data: HotelUpdateRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.update")),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = HotelService(session)
    return await service.update_hotel(hotel_id, data.model_dump(exclude_none=True))


@router.patch("/{hotel_id}/status", response_model=HotelResponse)
async def update_hotel_status(
    hotel_id: UUID = Path(),
    data: HotelStatusUpdate = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.update")),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = HotelService(session)
    return await service.update_hotel_status(hotel_id, data.status)


@router.get("/{hotel_id}/amenities", response_model=list[AmenityResponse])
async def get_hotel_amenities(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = AmenityService(session)
    return await service.get_hotel_amenities(hotel_id)


@router.post("/{hotel_id}/amenities", response_model=MessageResponse)
async def add_hotel_amenity(
    hotel_id: UUID = Path(),
    data: HotelAmenityRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.update")),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = AmenityService(session)
    await service.add_hotel_amenity(hotel_id, data.amenity_id)
    return {"message": "Amenity added to hotel"}


@router.delete("/{hotel_id}/amenities/{amenity_id}", response_model=MessageResponse)
async def remove_hotel_amenity(
    hotel_id: UUID = Path(),
    amenity_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.update")),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = AmenityService(session)
    await service.remove_hotel_amenity(hotel_id, amenity_id)
    return {"message": "Amenity removed from hotel"}


@router.get("/{hotel_id}/room-types", response_model=list[RoomTypeResponse])
async def get_hotel_room_types(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = RoomService(session)
    return await service.get_hotel_room_types(hotel_id)


@router.post("/{hotel_id}/room-types", response_model=MessageResponse)
async def add_hotel_room_type(
    hotel_id: UUID = Path(),
    data: HotelRoomTypeRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.update")),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = RoomService(session)
    await service.add_hotel_room_type(hotel_id, data.room_type_id)
    return {"message": "Room type added to hotel"}


@router.delete("/{hotel_id}/room-types/{room_type_id}", response_model=MessageResponse)
async def remove_hotel_room_type(
    hotel_id: UUID = Path(),
    room_type_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.update")),
):
    await _ensure_hotel_access(current_user, hotel_id, session)
    service = RoomService(session)
    await service.remove_hotel_room_type(hotel_id, room_type_id)
    return {"message": "Room type removed from hotel"}


@router.delete("/{hotel_id}", response_model=MessageResponse)
async def delete_hotel(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotels.delete")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can delete hotels")
    service = HotelService(session)
    await service.delete_hotel(hotel_id)
    return {"message": "Hotel and all related data deleted"}
