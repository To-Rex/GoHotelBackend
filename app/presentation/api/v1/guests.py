import logging
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

#: Bir vaqtda nechta OCR ishlaydi. Model kichik, lekin VPS yadrolari kam —
#: cheklovsiz qo'yilsa bir nechta parallel skan bir-birini sekinlashtiradi.
_MAX_CONCURRENT_SCANS = 2
_scan_limiter = anyio.Semaphore(_MAX_CONCURRENT_SCANS)

#: Yuklangan rasm uchun oqilona chegara (frontend ~200-400 KB yuboradi)
MAX_SCAN_IMAGE_BYTES = 12 * 1024 * 1024


class ScanSettingsRequest(BaseModel):
    mode: Literal["mrz", "visual", "auto"] = "auto"
    engine: Literal["server", "device"] = "server"


def _server_ocr_available() -> bool:
    try:
        from app.application.services.document_ocr import engine as ocr_engine

        return ocr_engine.engine_importable()
    except Exception:  # noqa: BLE001
        return False


_ocr_warm_up_started = False


def _start_ocr_warm_up() -> None:
    """Modellarni fonda yuklaydi — birinchi skan model kutib turmasligi uchun.

    Skaner dialogi ochilganda sozlama so'raladi, ya'ni bu chaqiruv aynan
    skanerlashdan bir necha soniya oldin keladi — modelni yuklashning eng
    qulay payti.
    """
    global _ocr_warm_up_started
    if _ocr_warm_up_started:
        return
    _ocr_warm_up_started = True
    try:
        import asyncio

        from app.application.services.document_ocr import engine as ocr_engine

        asyncio.get_running_loop().create_task(
            anyio.to_thread.run_sync(ocr_engine.warm_up)
        )
    except Exception:  # noqa: BLE001
        _ocr_warm_up_started = False


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
    if file is None:
        return None
    content = await file.read()
    if not content:
        raise ValidationException(f"{label}: rasm bo'sh", "BAD_IMAGE")
    if len(content) > MAX_SCAN_IMAGE_BYTES:
        raise ValidationException(f"{label}: rasm juda katta", "IMAGE_TOO_LARGE")
    return content


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
    if not _server_ocr_available():
        raise HTTPException(
            status_code=503, detail="Server hujjat skaneri bu serverda mavjud emas"
        )

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
    if not images:
        raise ValidationException("Hujjat rasmi yuborilmadi", "BAD_IMAGE")

    from app.application.services.document_ocr import service as ocr_service

    async with _scan_limiter:
        try:
            return await anyio.to_thread.run_sync(
                ocr_service.scan_document, images, document_type
            )
        except ValueError as exc:
            code = str(exc)
            raise ValidationException(
                {
                    "BAD_IMAGE": "Rasm o'qilmadi",
                    "IMAGE_TOO_SMALL": "Rasm juda kichik — hujjatni yaqinroqdan oling",
                    "NO_TEXT": "Rasmda yozuv topilmadi — hujjatni ramkaga to'liq joylang",
                }.get(code, "Hujjat o'qilmadi"),
                code,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Hujjat skanerlashda kutilmagan xato")
            raise HTTPException(status_code=500, detail="Hujjatni o'qishda xatolik")


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
