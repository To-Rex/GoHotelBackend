from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.exceptions import ForbiddenException
from app.infrastructure.database.models.service import Service
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


@router.get("/")
async def list_services(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    stmt = select(Service).where(Service.is_active.is_(True)).order_by(Service.category, Service.name)
    result = await session.execute(stmt)
    services = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "code": s.code,
            "description": s.description,
            "category": s.category,
            "is_active": s.is_active,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in services
    ]


@router.post("/")
async def create_service(
    data: dict,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("service.create")),
):
    if current_user.get("user_type") != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can create global services")
    svc = Service(
        name=data["name"],
        code=data["code"],
        description=data.get("description"),
        category=data.get("category", "OTHER"),
        is_active=True,
    )
    session.add(svc)
    await session.flush()
    return {
        "id": str(svc.id),
        "name": svc.name,
        "code": svc.code,
        "description": svc.description,
        "category": svc.category,
    }


@router.put("/{service_id}")
async def update_service(
    service_id: UUID = Path(),
    data: dict = ...,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("service.update")),
):
    if current_user.get("user_type") != "SUPER_ADMIN":
        raise ForbiddenException("Only SUPER_ADMIN can update global services")
    stmt = select(Service).where(Service.id == service_id)
    result = await session.execute(stmt)
    svc = result.scalar_one_or_none()
    if not svc:
        from app.core.exceptions import NotFoundException
        raise NotFoundException("Service not found", "SERVICE_NOT_FOUND")
    updatable = ["name", "description", "category", "is_active"]
    for k, v in data.items():
        if k in updatable and v is not None:
            setattr(svc, k, v)
    await session.flush()
    return {
        "id": str(svc.id),
        "name": svc.name,
        "code": svc.code,
        "description": svc.description,
        "category": svc.category,
        "is_active": svc.is_active,
    }
