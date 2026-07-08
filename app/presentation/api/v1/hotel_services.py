from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.service import HotelService, Service
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


@router.get("/")
async def list_hotel_services(
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    if h_id is None:
        stmt = (
            select(HotelService, Service)
            .join(Service, Service.id == HotelService.service_id)
        )
    else:
        stmt = (
            select(HotelService, Service)
            .join(Service, Service.id == HotelService.service_id)
            .where(HotelService.hotel_id == h_id)
        )
    result = await session.execute(stmt)
    rows = result.all()
    return [
        {
            "id": str(hs.id),
            "service_id": str(svc.id),
            "name": svc.name,
            "code": svc.code,
            "category": svc.category,
            "price": float(hs.price),
            "is_active": hs.is_active,
        }
        for hs, svc in rows
    ]


@router.post("/")
async def enable_service(
    data: dict,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotel_service.manage")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    svc_stmt = select(Service).where(Service.id == data["service_id"])
    svc_result = await session.execute(svc_stmt)
    svc = svc_result.scalar_one_or_none()
    if not svc:
        raise NotFoundException("Service not found", "SERVICE_NOT_FOUND")

    hs = HotelService(
        hotel_id=h_id,
        service_id=data["service_id"],
        price=data["price"],
        is_active=True,
    )
    session.add(hs)
    await session.flush()
    return {
        "id": str(hs.id),
        "service_id": str(hs.service_id),
        "name": svc.name,
        "price": float(hs.price),
        "is_active": hs.is_active,
    }


@router.put("/{hotel_service_id}")
async def update_hotel_service(
    hotel_service_id: UUID = Path(),
    data: dict = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotel_service.manage")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    stmt = select(HotelService).where(
        HotelService.id == hotel_service_id, HotelService.hotel_id == h_id
    )
    result = await session.execute(stmt)
    hs = result.scalar_one_or_none()
    if not hs:
        raise NotFoundException("Hotel service not found", "HOTEL_SERVICE_NOT_FOUND")
    if "price" in data and data["price"] is not None:
        hs.price = data["price"]
    if "is_active" in data and data["is_active"] is not None:
        hs.is_active = data["is_active"]
    await session.flush()
    return {
        "id": str(hs.id),
        "service_id": str(hs.service_id),
        "price": float(hs.price),
        "is_active": hs.is_active,
    }


@router.delete("/{hotel_service_id}", response_model=MessageResponse)
async def disable_service(
    hotel_service_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("hotel_service.manage")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    stmt = select(HotelService).where(
        HotelService.id == hotel_service_id, HotelService.hotel_id == h_id
    )
    result = await session.execute(stmt)
    hs = result.scalar_one_or_none()
    if not hs:
        raise NotFoundException("Hotel service not found", "HOTEL_SERVICE_NOT_FOUND")
    hs.is_active = False
    await session.flush()
    return {"message": "Service disabled for hotel"}
