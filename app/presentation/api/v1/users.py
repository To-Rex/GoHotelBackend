from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.user import User
from app.application.services.user_service import UserService
from app.application.dto.user import EmployeeCreateRequest, EmployeeUpdateRequest, UserResponse
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


@router.get("/", response_model=list[UserResponse])
async def list_employees(
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
    service = UserService(session)
    return await service.get_employees(h_id, skip=skip, limit=limit, status=status)


@router.post("/", response_model=UserResponse)
async def create_employee(
    data: EmployeeCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("employee.create")),
):
    h_id = _get_hotel_id(current_user)
    if h_id and data.hotel_id != h_id:
        raise ForbiddenException("Cannot create employee for another hotel")
    if not h_id:
        h_id = data.hotel_id
    service = UserService(session)
    payload = data.model_dump()
    payload["hotel_id"] = h_id
    return await service.create_employee(payload)


@router.get("/{employee_id}", response_model=UserResponse)
async def get_employee(
    employee_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = UserService(session)
    return await service.get_employee(employee_id, h_id)


@router.put("/{employee_id}", response_model=UserResponse)
async def update_employee(
    employee_id: UUID = Path(),
    data: EmployeeUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("employee.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            user = await session.get(User, employee_id)
            if not user:
                raise NotFoundException("Employee not found", "EMPLOYEE_NOT_FOUND")
            h_id = user.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = UserService(session)
    return await service.update_employee(employee_id, h_id, data.model_dump(exclude_none=True))


@router.delete("/{employee_id}", response_model=MessageResponse)
async def delete_employee(
    employee_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("employee.delete")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            user = await session.get(User, employee_id)
            if not user:
                raise NotFoundException("Employee not found", "EMPLOYEE_NOT_FOUND")
            h_id = user.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = UserService(session)
    await service.delete_employee(employee_id, h_id)
    return {"message": "Employee deleted"}
