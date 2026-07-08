from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.user import User
from app.application.services.user_service import UserService
from app.application.dto.user import PermissionAssignRequest, UserPermissionsResponse
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
async def list_permissions(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = UserService(session)
    return await service.get_all_permissions()


@router.get("/modules")
async def permissions_by_module(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = UserService(session)
    return await service.get_permission_modules()


@router.get("/{employee_id}/permissions", response_model=UserPermissionsResponse)
async def get_employee_permissions(
    employee_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("permission.view")),
):
    service = UserService(session)
    permissions = await service.get_user_permissions(employee_id)
    return {"user_id": employee_id, "permissions": permissions}


@router.put("/{employee_id}/permissions", response_model=MessageResponse)
async def assign_permissions(
    employee_id: UUID = Path(),
    data: PermissionAssignRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("permission.assign")),
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
    await service.assign_permissions(employee_id, data.permission_ids, h_id, current_user["id"])
    return {"message": "Permissions updated"}


@router.post("/{employee_id}/permissions/{perm_id}", response_model=MessageResponse)
async def grant_permission(
    employee_id: UUID = Path(),
    perm_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("permission.assign")),
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
    await service.grant_permission(employee_id, perm_id, h_id, current_user["id"])
    return {"message": "Permission granted"}


@router.delete("/{employee_id}/permissions/{perm_id}", response_model=MessageResponse)
async def revoke_permission(
    employee_id: UUID = Path(),
    perm_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("permission.assign")),
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
    await service.revoke_permission(employee_id, perm_id, h_id)
    return {"message": "Permission revoked"}
