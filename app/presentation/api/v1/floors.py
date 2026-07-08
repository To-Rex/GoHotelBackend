from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.floor import Floor
from app.infrastructure.database.models.branch import Branch
from app.application.services.room_service import RoomService
from app.application.dto.room import FloorCreateRequest, FloorResponse, FloorUpdateRequest
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


@router.get("/", response_model=list[FloorResponse])
async def list_floors(
    branch_id: UUID | None = Query(default=None),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = RoomService(session)
    return await service.get_floors(h_id, branch_id=branch_id)


@router.post("/", response_model=FloorResponse)
async def create_floor(
    data: FloorCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("floor.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id and data.hotel_id:
            hotel_id = data.hotel_id
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
    return await service.create_floor(h_id, data.branch_id, data.model_dump())


@router.put("/{floor_id}", response_model=FloorResponse)
async def update_floor(
    floor_id: UUID = Path(),
    data: FloorUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("floor.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            floor = await session.get(Floor, floor_id)
            if not floor:
                raise NotFoundException("Floor not found", "FLOOR_NOT_FOUND")
            h_id = floor.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = RoomService(session)
    floor = await service.get_floor(floor_id, h_id)
    updatable = {"floor_number": data.floor_number, "name": data.name}
    update_data = {k: v for k, v in updatable.items() if v is not None}
    return await service.update_floor(floor, **update_data)


@router.delete("/{floor_id}", response_model=MessageResponse)
async def delete_floor(
    floor_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("floor.delete")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            floor = await session.get(Floor, floor_id)
            if not floor:
                raise NotFoundException("Floor not found", "FLOOR_NOT_FOUND")
            h_id = floor.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = RoomService(session)
    await service.delete_floor(floor_id, h_id)
    return {"message": "Floor deleted"}
