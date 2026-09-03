"""Boshqaruv paneli endpointlari — `/api/v1/superadmin`.

Bu marshrutlar mehmonxona endpointlaridan butunlay ajratilgan: ular
`require_active_hotel` ga ham, xodim tokeniga ham bog'liq emas. Yagona
kirish sharti — panel tokeni.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import UnauthorizedException
from app.superadmin import security
from app.superadmin.estate_service import EstateService
from app.superadmin.insight_service import InsightService
from app.superadmin.models import PanelUser
from app.superadmin.service import PanelAuthService

router = APIRouter()


# ------------------------------------------------------------ kirish --


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


async def current_panel_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> PanelUser:
    """Panel tokenini tekshiradi.

    Xodim tokeni bu yerdan o'tmaydi: uning ichida `aud: superadmin`
    yo'q va `decode_token` uni rad etadi.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedException("Panel tokeni kerak")
    payload = security.decode_token(authorization.split(" ", 1)[1].strip())
    if not payload or not payload.get("sub"):
        raise UnauthorizedException("Token yaroqsiz yoki muddati tugagan")
    try:
        user_id = UUID(str(payload["sub"]))
    except ValueError:
        raise UnauthorizedException("Token yaroqsiz") from None
    return await PanelAuthService(session).current(user_id)


@router.post("/auth/login")
async def login(data: LoginRequest, session: AsyncSession = Depends(get_db)):
    """Panelga kirish. Muvaffaqiyatsiz urinishda sabab aytilmaydi."""
    return await PanelAuthService(session).login(data.email, data.password)


@router.get("/auth/me")
async def me(actor: PanelUser = Depends(current_panel_user)):
    return {
        "id": str(actor.id),
        "email": actor.email,
        "label": actor.label or security.ROOT_LABEL,
        "is_root": bool(actor.is_root),
    }


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=security.MIN_PASSWORD_LENGTH, max_length=200)


@router.post("/auth/change-password")
async def change_password(
    data: ChangePasswordRequest,
    session: AsyncSession = Depends(get_db),
    actor: PanelUser = Depends(current_panel_user),
):
    """O'z parolini almashtirish — eskisini bilish shart."""
    return await PanelAuthService(session).change_own_password(
        actor, data.current_password, data.new_password
    )


# ------------------------------------------- panel foydalanuvchilari --


class PanelUserCreateRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=security.MIN_PASSWORD_LENGTH, max_length=200)
    label: str = Field(default="", max_length=120)


class PasswordRequest(BaseModel):
    password: str = Field(min_length=security.MIN_PASSWORD_LENGTH, max_length=200)


class ActiveRequest(BaseModel):
    is_active: bool


@router.get("/users")
async def list_panel_users(
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await PanelAuthService(session).list_users()


@router.post("/users")
async def create_panel_user(
    data: PanelUserCreateRequest,
    session: AsyncSession = Depends(get_db),
    actor: PanelUser = Depends(current_panel_user),
):
    """Panelga yangi odam qo'shish — faqat tizim egasi."""
    return await PanelAuthService(session).create_user(
        actor, data.email, data.password, data.label
    )


@router.patch("/users/{user_id}/active")
async def set_panel_user_active(
    data: ActiveRequest,
    user_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    actor: PanelUser = Depends(current_panel_user),
):
    return await PanelAuthService(session).set_active(actor, user_id, data.is_active)


@router.post("/users/{user_id}/password")
async def reset_panel_user_password(
    data: PasswordRequest,
    user_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    actor: PanelUser = Depends(current_panel_user),
):
    return await PanelAuthService(session).reset_password(
        actor, user_id, data.password
    )


@router.delete("/users/{user_id}")
async def delete_panel_user(
    user_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    actor: PanelUser = Depends(current_panel_user),
):
    await PanelAuthService(session).delete_user(actor, user_id)
    return {"message": "O'chirildi"}


# ------------------------------------------- mehmonxonalar va filiallar --


class HotelRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    code: str | None = Field(default=None, max_length=10)
    description: str | None = None
    stars: int | None = Field(default=None, ge=1, le=7)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    address_line1: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=20)


class BranchRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    code: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    address_line1: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    is_main_branch: bool | None = None


class UserStatusRequest(BaseModel):
    status: str = Field(max_length=20)


@router.get("/overview")
async def overview(
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Tizim bo'yicha yig'ma raqamlar."""
    return await EstateService(session).overview()


@router.get("/hotels")
async def list_hotels(
    search: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).list_hotels(search)


@router.post("/hotels")
async def create_hotel(
    data: HotelRequest,
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).create_hotel(data.model_dump(exclude_none=True))


@router.get("/hotels/{hotel_id}")
async def get_hotel(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).get_hotel(hotel_id)


@router.put("/hotels/{hotel_id}")
async def update_hotel(
    data: HotelRequest,
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).update_hotel(
        hotel_id, data.model_dump(exclude_unset=True)
    )


@router.delete("/hotels/{hotel_id}")
async def deactivate_hotel(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Mehmonxonani to'xtatadi (yozuv o'chirilmaydi — tarix saqlanadi)."""
    return await EstateService(session).delete_hotel(hotel_id)


@router.get("/hotels/{hotel_id}/branches")
async def list_branches(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).list_branches(hotel_id)


@router.post("/hotels/{hotel_id}/branches")
async def create_branch(
    data: BranchRequest,
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).create_branch(
        hotel_id, data.model_dump(exclude_none=True)
    )


@router.put("/branches/{branch_id}")
async def update_branch(
    data: BranchRequest,
    branch_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).update_branch(
        branch_id, data.model_dump(exclude_unset=True)
    )


@router.delete("/branches/{branch_id}")
async def delete_branch(
    branch_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    await EstateService(session).delete_branch(branch_id)
    return {"message": "O'chirildi"}


@router.get("/hotels/{hotel_id}/users")
async def list_hotel_users(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).list_users(hotel_id)


@router.patch("/staff/{user_id}/status")
async def set_staff_status(
    data: UserStatusRequest,
    user_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await EstateService(session).set_user_status(user_id, data.status)


@router.post("/staff/{user_id}/password")
async def reset_staff_password(
    data: PasswordRequest,
    user_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Mehmonxona xodimining parolini tiklash."""
    return await EstateService(session).reset_user_password(user_id, data.password)


# ------------------------------------------- nazorat: bron, pul, tarix --


class StaffCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=200)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    user_type: str = Field(default="EMPLOYEE", max_length=20)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    branch_id: UUID | None = None


@router.post("/hotels/{hotel_id}/users")
async def create_hotel_staff(
    data: StaffCreateRequest,
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Mehmonxonaga xodim qo'shish — yangi obyektning birinchi admini uchun."""
    return await EstateService(session).create_staff(
        hotel_id, data.model_dump(exclude_none=True)
    )


@router.get("/hotels/{hotel_id}/rooms")
async def list_hotel_rooms(
    hotel_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    return await InsightService(session).rooms(hotel_id)


@router.get("/reservations")
async def list_reservations(
    hotel_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    search: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Barcha mehmonxonalardagi bronlar."""
    return await InsightService(session).reservations(
        hotel_id=hotel_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        search=search,
        skip=skip,
        limit=limit,
    )


@router.get("/finance")
async def finance(
    date_from: date = Query(...),
    date_to: date = Query(...),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Mehmonxonalar kesimida tushum va xarajat."""
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return await InsightService(session).finance(date_from, date_to, hotel_id)


@router.get("/audit")
async def audit(
    hotel_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Kim nima o'zgartirgani — oxirgi yozuvlar."""
    return await InsightService(session).audit(
        hotel_id=hotel_id, action=action, limit=limit
    )


@router.get("/guests")
async def guests(
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    _: PanelUser = Depends(current_panel_user),
):
    """Mehmonlar bazasi — barcha mehmonxonalar uchun umumiy."""
    return await InsightService(session).guests(search=search, limit=limit)
