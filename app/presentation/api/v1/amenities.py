from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.application.services.amenity_service import AmenityService
from app.application.dto.amenity import (
    AmenityCreateRequest,
    AmenityUpdateRequest,
    AmenityResponse,
)
from app.application.dto.common import MessageResponse
from app.presentation.middleware.auth import get_current_user, require_permission

router = APIRouter()


@router.get("/", response_model=list[AmenityResponse])
async def list_amenities(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = AmenityService(session)
    return await service.get_amenities()


@router.post("/", response_model=AmenityResponse)
async def create_amenity(
    data: AmenityCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("services.manage")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can manage amenities")
    service = AmenityService(session)
    return await service.create_amenity(data.model_dump())


@router.put("/{amenity_id}", response_model=AmenityResponse)
async def update_amenity(
    amenity_id: UUID = Path(),
    data: AmenityUpdateRequest = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("services.manage")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can manage amenities")
    service = AmenityService(session)
    return await service.update_amenity(amenity_id, data.model_dump(exclude_none=True))


@router.delete("/{amenity_id}", response_model=MessageResponse)
async def delete_amenity(
    amenity_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("services.manage")),
):
    if current_user["user_type"] != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can manage amenities")
    service = AmenityService(session)
    await service.delete_amenity(amenity_id)
    return {"message": "Amenity deleted"}
