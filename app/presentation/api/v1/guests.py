import logging
from functools import lru_cache
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.guest_stay import GuestHistoryResponse
from app.application.services.document_ocr import intake
from app.application.services.blacklist_service import BlacklistService
from app.application.services.guest_history_service import GuestHistoryService
from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
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

logger = logging.getLogger(__name__)

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
#
# engine — OCR qayerda bajariladi:
#   server — serverdagi PP-OCR (tezroq va aniqroq; brauzer zaif qurilmada
#            ham yuklanmaydi), aloqa uzilsa qurilmadagi OCR zaxira bo'ladi
#   device — faqat brauzerda (rasm qurilmadan chiqmaydi)
DEFAULT_SCAN_SETTINGS = {"mode": "auto", "engine": "server"}

#: Rasm o'qish, hajm chegarasi va OCR chaqiruvi qabulxona telefoni bilan
#: umumiy — qoidalar `document_ocr/intake.py` da.
MAX_SCAN_IMAGE_BYTES = intake.MAX_SCAN_IMAGE_BYTES


class ScanSettingsRequest(BaseModel):
    mode: Literal["mrz", "visual", "auto"] = "auto"
    engine: Literal["server", "device"] = "server"


#: Dvigatel bor-yo'qligi — qabulxona telefoni bilan umumiy tekshiruv.
_server_ocr_available = intake.server_ocr_available


#: Qizdirish mantiqiy jihatdan skanerning bir qismi — `intake` da turadi
#: va telefon yo'li bilan umumiy.
_start_ocr_warm_up = intake.start_warm_up


def _resolve_scan(settings: dict | None) -> dict:
    saved = (settings or {}).get(SCAN_SETTINGS_KEY) or {}
    mode = saved.get("mode")
    if mode not in ("mrz", "visual", "auto"):
        mode = DEFAULT_SCAN_SETTINGS["mode"]
    engine_choice = saved.get("engine")
    if engine_choice not in ("server", "device"):
        engine_choice = DEFAULT_SCAN_SETTINGS["engine"]
    return {
        "mode": mode,
        "engine": engine_choice,
        # Frontend shu bayroqqa qarab serverga yuborishni tanlaydi: dvigatel
        # o'rnatilmagan serverda u qurilmadagi OCR'da qolaveradi.
        "serverAvailable": _server_ocr_available(),
    }


@router.get("/scan-settings")
async def get_scan_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Skaner rejimi — har qanday xodim o'qiy oladi (skanerlash uchun kerak)."""
    h_id = _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    resolved = _resolve_scan(hotel.settings if hotel else None)
    if resolved["serverAvailable"] and resolved["engine"] == "server":
        _start_ocr_warm_up()
    return resolved


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
    new_settings[SCAN_SETTINGS_KEY] = {"mode": data.mode, "engine": data.engine}
    hotel.settings = new_settings
    await session.flush()
    return _resolve_scan(new_settings)


async def _read_scan_image(file, label: str) -> bytes | None:
    return await intake.read_image(file, label)


@router.post("/scan-document")
async def scan_document(
    document_type: Literal["ID_CARD", "PASSPORT"] = Form(default="ID_CARD"),
    front: UploadFile | None = File(default=None),
    back: UploadFile | None = File(default=None),
    file: UploadFile | None = File(default=None),
    side: Literal["front", "back", "passport"] = Form(default="front"),
    current_user: dict = Depends(get_current_user),
):
    """Hujjat rasm(lar)ini o'qib, maydonlarni va tekshiruvlar ro'yxatini qaytaradi.

    ID karta uchun IKKALA tomon bitta so'rovda yuboriladi. Bu shunchaki qulaylik
    emas: faqat shundagina old tomondagi bosma ma'lumotni orqa tomondagi MRZ
    bilan solishtirish, ikkala tomon bitta hujjatga tegishli ekanini tekshirish
    va nazorat raqami bo'yicha tiklangan belgini mustaqil tasdiqlash mumkin.
    Passport uchun bitta sahifa yetarli — unda MRZ ham, bosma maydonlar ham bor.

    Rasm SAQLANMAYDI — faqat xotirada o'qiladi va javob qaytgach yo'qoladi.

    Dvigatel serverda mavjud bo'lmasa 503 qaytadi — frontend buni ko'rib,
    qurilmadagi OCR'ga qaytadi va foydalanuvchi hech narsa sezmaydi.
    """
    intake.require_server_ocr()

    images: dict[str, bytes] = {}
    front_bytes = await _read_scan_image(front, "Old tomon")
    back_bytes = await _read_scan_image(back, "Orqa tomon")
    single_bytes = await _read_scan_image(file, "Rasm")

    if front_bytes:
        images["passport" if document_type == "PASSPORT" else "front"] = front_bytes
    if back_bytes:
        images["back"] = back_bytes
    if single_bytes and not images:
        # Eski chaqiruv shakli: bitta `file` va `side`
        images[side] = single_bytes

    return await intake.run_scan(images, document_type)


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


class BlacklistRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class BlacklistPolicyRequest(BaseModel):
    block_booking: bool


@router.get("/blacklist")
async def list_blacklist(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Qora ro'yxatdagi mehmonlar."""
    # Ro'yxat global — mehmonlar bazasi kabi. Izoh servisda.
    return await BlacklistService(session).list_blacklisted()


@router.get("/blacklist-settings")
async def get_blacklist_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Qora ro'yxatdagi mehmonga bron ochish taqiqlanganmi."""
    from app.infrastructure.database.models.hotel import Hotel
    from app.application.services.blacklist_service import (
        DEFAULT_BLOCK_BOOKING,
        resolve_block_booking,
    )

    h_id = (
        current_user.get("hotel_id")
        if current_user["user_type"] == "SUPER_ADMIN"
        else _get_hotel_id(current_user)
    )
    hotel = await session.get(Hotel, h_id) if h_id else None
    return {
        "block_booking": resolve_block_booking(hotel.settings if hotel else None),
        "default_block_booking": DEFAULT_BLOCK_BOOKING,
    }


@router.put("/blacklist-settings")
async def save_blacklist_settings(
    data: BlacklistPolicyRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Qoidani saqlash — faqat ADMIN/SUPER_ADMIN."""
    from app.infrastructure.database.models.hotel import Hotel
    from app.application.services.blacklist_service import (
        BLACKLIST_SETTINGS_KEY,
        DEFAULT_BLOCK_BOOKING,
    )

    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        raise ForbiddenException("Faqat administrator o'zgartira oladi")
    h_id = _get_hotel_id(current_user)
    hotel = await session.get(Hotel, h_id) if h_id else None
    if not hotel:
        raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
    # JSONB YANGI dict bilan almashtiriladi — o'zgarish sezilishi uchun
    new_settings = dict(hotel.settings or {})
    new_settings[BLACKLIST_SETTINGS_KEY] = {"block_booking": data.block_booking}
    hotel.settings = new_settings
    await session.flush()
    return {
        "block_booking": data.block_booking,
        "default_block_booking": DEFAULT_BLOCK_BOOKING,
    }


@router.post("/{guest_id}/blacklist", response_model=GuestResponse)
async def add_to_blacklist(
    data: BlacklistRequest,
    guest_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mehmonni qora ro'yxatga qo'shish — faqat administrator, sabab bilan.

    Mehmonxona bo'yicha cheklov yo'q: mehmonlar bazasi global va qora
    ro'yxat ham shunday. Izoh `blacklist_service` da.
    """
    return await BlacklistService(session).add(guest_id, data.reason, current_user)


@router.delete("/{guest_id}/blacklist", response_model=GuestResponse)
async def remove_from_blacklist(
    guest_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Qora ro'yxatdan chiqarish — faqat administrator."""
    return await BlacklistService(session).remove(guest_id, current_user)


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


@router.get("/{guest_id}/history", response_model=GuestHistoryResponse)
async def get_guest_history(
    guest_id: UUID = Path(),
    hotel_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mehmonning turish tarixi: qachon, qaysi xonada, kim bilan.

    Mehmon HAMROH bo'lib turgan bronlar ham kiradi — ularsiz "kim bilan
    kelgan" savoli chala javob olardi.
    """
    if current_user["user_type"] == "SUPER_ADMIN":
        h_id = hotel_id or current_user.get("hotel_id")
    else:
        h_id = _get_hotel_id(current_user)
    return await GuestHistoryService(session).get_history(guest_id, h_id)


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
