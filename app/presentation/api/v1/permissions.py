from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.user import User
from app.infrastructure.database.models.permission import Permission
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


# Menejer (EMPLOYEE turi, permission.assign ruxsatiga ega xodim) faqat
# "Farrosh" roli doirasidagi ruxsatlarni bera/olib tashlay oladi.
# ADMIN va SUPER_ADMIN uchun cheklov yo'q. Frontenddagi Farrosh shabloni
# bilan bir xil to'plam (housekeeping.cleaning.* naqshi bilan).
MANAGER_ASSIGNABLE_PATTERNS = (
    "room.view",
    "room.status.update",
    "housekeeping.task.update",
    "housekeeping.cleaning.*",
)


def _is_manager_assignable(code: str) -> bool:
    for pattern in MANAGER_ASSIGNABLE_PATTERNS:
        if pattern.endswith(".*"):
            # "housekeeping.cleaning.*" -> "housekeeping.cleaning." prefiksi
            if code.startswith(pattern[:-1]):
                return True
        elif code == pattern:
            return True
    return False


async def _ensure_manager_scope(
    session: AsyncSession, current_user: dict, changed_ids: set[UUID]
) -> None:
    """EMPLOYEE (menejer) o'zgartirayotgan ruxsatlar farrosh to'plamidan
    tashqariga chiqmasligini tekshiradi. ADMIN/SUPER_ADMIN cheklanmaydi."""
    if current_user["user_type"] in ("SUPER_ADMIN", "ADMIN"):
        return
    if not changed_ids:
        return
    result = await session.execute(
        select(Permission).where(Permission.id.in_(changed_ids))
    )
    for perm in result.scalars().all():
        if not _is_manager_assignable(perm.code):
            raise ForbiddenException(
                "Menejer faqat Farrosh roli doirasidagi ruxsatlarni o'zgartira oladi",
                "MANAGER_SCOPE_LIMITED",
            )


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
    # Menejer uchun faqat o'zgargan (qo'shilgan/olib tashlangan) ruxsatlar
    # tekshiriladi — farrosh to'plamidan tashqaridagi mavjud ruxsatlar
    # ro'yxatda qolsa, bu o'zgarish hisoblanmaydi va rad etilmaydi.
    if current_user["user_type"] not in ("SUPER_ADMIN", "ADMIN"):
        current_perms = await service.get_user_permissions(employee_id)
        current_ids = {UUID(str(p["id"])) for p in current_perms}
        new_ids = {UUID(str(pid)) for pid in data.permission_ids}
        await _ensure_manager_scope(
            session, current_user, (new_ids - current_ids) | (current_ids - new_ids)
        )
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
    await _ensure_manager_scope(session, current_user, {perm_id})
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
    await _ensure_manager_scope(session, current_user, {perm_id})
    service = UserService(session)
    await service.revoke_permission(employee_id, perm_id, h_id)
    return {"message": "Permission revoked"}
