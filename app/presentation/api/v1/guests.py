from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException
from app.infrastructure.database.models.guest import Guest
from app.infrastructure.database.models.hotel import Hotel
from app.application.services.guest_service import GuestService
from app.application.dto.guest import GuestCreateRequest, GuestUpdateRequest, GuestResponse
from app.application.dto.reservation import ReservationResponse
from app.application.dto.common import MessageResponse
from app.core.constants import MAX_PAGE_SIZE
from app.presentation.middleware.auth import get_current_user, require_permission
from app.presentation.api.v1._deps import require_active_hotel
from app.infrastructure.database.repositories.reservation_repo import ReservationRepository

router = APIRouter(dependencies=[Depends(require_active_hotel)])


def _get_hotel_id(current_user: dict) -> UUID | None:
    if current_user["user_type"] == "SUPER_ADMIN":
        return current_user.get("hotel_id")
    hotel_id = current_user.get("hotel_id")
    if not hotel_id:
        raise ForbiddenException("Hotel context required")
    return hotel_id


# --------------------------------------------- hujjat skaneri sozlamasi --

SCAN_SETTINGS_KEY = "document_scan"

# mrz    — faqat MRZ zonasi (tez, aniq: nazorat raqamlari bilan tekshiriladi)
# visual — hujjatning old tomonidagi yozuvlar (MRZ yo'q/o'chgan hujjatlar uchun)
# auto   — avval MRZ, topilmasa vizual (standart)
DEFAULT_SCAN_SETTINGS = {"mode": "auto"}


class ScanSettingsRequest(BaseModel):
    mode: Literal["mrz", "visual", "auto"] = "auto"


def _resolve_scan(settings: dict | None) -> dict:
    saved = (settings or {}).get(SCAN_SETTINGS_KEY) or {}
    mode = saved.get("mode")
    if mode not in ("mrz", "visual", "auto"):
        mode = DEFAULT_SCAN_SETTINGS["mode"]
    return {"mode": mode}


@router.get("/scan-settings")
async def get_scan_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Skaner rejimi — har qanday xodim o'qiy oladi (skanerlash uchun kerak)."""
    h_id = _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    return _resolve_scan(hotel.settings if hotel else None)


@router.put("/scan-settings")
async def save_scan_settings(
    data: ScanSettingsRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Rejimni o'zgartirish — faqat administrator."""
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException(
            "Faqat administrator skaner rejimini o'zgartira oladi", "FORBIDDEN"
        )
    h_id = _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    if not hotel:
        raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
    # JSONB YANGI dict bilan almashtiriladi — SQLAlchemy o'zgarishni sezishi uchun
    new_settings = dict(hotel.settings or {})
    new_settings[SCAN_SETTINGS_KEY] = {"mode": data.mode}
    hotel.settings = new_settings
    await session.flush()
    return _resolve_scan(new_settings)


@router.get("/", response_model=list[GuestResponse])
async def list_guests(
    query: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    skip = (page - 1) * page_size
    service = GuestService(session)
    return await service.get_guests(h_id, skip=skip, limit=page_size, query=query)


@router.post("/", response_model=GuestResponse)
async def register_guest(
    data: GuestCreateRequest,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.create")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            raise ForbiddenException("Hotel ID required for SUPER_ADMIN")
        h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = GuestService(session)
    return await service.create_guest(h_id, data.model_dump())


@router.get("/{guest_id}", response_model=GuestResponse)
async def get_guest(
    guest_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    service = GuestService(session)
    return await service.get_guest(guest_id, h_id)


@router.put("/{guest_id}", response_model=GuestResponse)
async def update_guest(
    guest_id: UUID = Path(),
    data: GuestUpdateRequest = ...,
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.update")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            guest = await session.get(Guest, guest_id)
            if not guest:
                raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")
            h_id = guest.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = GuestService(session)
    return await service.update_guest(guest_id, h_id, data.model_dump(exclude_none=True))


@router.get("/{guest_id}/reservations", response_model=list[ReservationResponse])
async def get_guest_reservations(
    guest_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    repo = ReservationRepository(session)
    return await repo.get_guest_reservations(guest_id, h_id)


@router.delete("/{guest_id}", response_model=MessageResponse)
async def delete_guest(
    guest_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("guest.delete")),
):
    if current_user["user_type"] == "SUPER_ADMIN":
        if not hotel_id:
            guest = await session.get(Guest, guest_id)
            if not guest:
                raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")
            h_id = guest.hotel_id
        else:
            h_id = hotel_id
    else:
        h_id = _get_hotel_id(current_user)
    if not h_id:
        raise ForbiddenException("Hotel context required")
    service = GuestService(session)
    await service.soft_delete_guest(guest_id, h_id)
    return {"message": "Guest deleted"}
