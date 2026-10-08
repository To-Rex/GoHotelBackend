import logging
from functools import lru_cache
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.configurator_access import assert_can_manage_settings
from app.application.dto.guest_stay import GuestHistoryResponse
from app.application.services import document_images
from app.application.services.document_ocr import intake
from app.application.services.blacklist_service import BlacklistService
from app.application.services.guest_history_service import GuestHistoryService
from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.infrastructure.database.models.guest import Guest
from app.application.services.guest_service import GuestService
from app.application.dto.guest import GuestCreateRequest, GuestUpdateRequest, GuestResponse
from app.application.dto.reservation import ReservationResponse
from app.application.dto.common import MessageResponse
from app.core.constants import MAX_PAGE_SIZE
from app.presentation.middleware.auth import get_current_user, require_permission
from app.presentation.api.v1._deps import require_active_hotel
from app.infrastructure.database.repositories.reservation_repo import ReservationRepository
from app.application.services.branch_settings import settings_owner

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
#   device — faqat brauzerda (OCR uchun rasm serverga yuborilmaydi)
#
# store_images — skanerlangan hujjat surati mehmon kartasiga saqlanadimi
#   (MinIO, `document_images.py`). Standart — ha.
DEFAULT_SCAN_SETTINGS = {"mode": "auto", "engine": "server", "store_images": True}

#: Rasm o'qish, hajm chegarasi va OCR chaqiruvi qabulxona telefoni bilan
#: umumiy — qoidalar `document_ocr/intake.py` da.
MAX_SCAN_IMAGE_BYTES = intake.MAX_SCAN_IMAGE_BYTES


class ScanSettingsRequest(BaseModel):
    mode: Literal["mrz", "visual", "auto"] = "auto"
    engine: Literal["server", "device"] = "server"
    #: Berilmasa saqlangan qiymat o'zgarmaydi (eski ilovalar bu maydonni
    #: yubormaydi — ular bayroqni bilmasdan o'chirib qo'ymasin)
    store_images: bool | None = None


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
        "store_images": document_images.store_enabled(settings),
    }


@router.get("/scan-settings")
async def get_scan_settings(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Skaner rejimi — har qanday xodim o'qiy oladi (skanerlash uchun kerak)."""
    h_id = _get_hotel_id(current_user)
    hotel = await settings_owner(session, h_id)
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
    """Rejimni o'zgartirish — faqat sozlovchi (configurator_access)."""
    assert_can_manage_settings(current_user)
    h_id = _get_hotel_id(current_user)
    hotel = await settings_owner(session, h_id)
    if not hotel:
        raise NotFoundException("Hotel not found", "HOTEL_NOT_FOUND")
    # JSONB YANGI dict bilan almashtiriladi — SQLAlchemy o'zgarishni sezishi uchun
    new_settings = dict(hotel.settings or {})
    store = (
        data.store_images
        if data.store_images is not None
        else document_images.store_enabled(hotel.settings)
    )
    new_settings[SCAN_SETTINGS_KEY] = {
        "mode": data.mode,
        "engine": data.engine,
        "store_images": store,
    }
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
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Hujjat rasm(lar)ini o'qib, maydonlarni va tekshiruvlar ro'yxatini qaytaradi.

    ID karta uchun IKKALA tomon bitta so'rovda yuboriladi. Bu shunchaki qulaylik
    emas: faqat shundagina old tomondagi bosma ma'lumotni orqa tomondagi MRZ
    bilan solishtirish, ikkala tomon bitta hujjatga tegishli ekanini tekshirish
    va nazorat raqami bo'yicha tiklangan belgini mustaqil tasdiqlash mumkin.
    Passport uchun bitta sahifa yetarli — unda MRZ ham, bosma maydonlar ham bor.

    Bu endpoint rasmni SAQLAMAYDI — mehmon hali tanlanmagan. Surat mehmon
    yaratilgach/tanlangach `POST /guests/{id}/document-images` bilan
    saqlanadi (skaner sozlamasida `store_images` yoqiq bo'lsa).

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

    # Sozlamadagi rejim SERVERDA ham kuchga kiradi — ilgari u faqat
    # qurilmadagi OCR'ga ta'sir qilib, server doim "auto" ishlardi
    h_id = _get_hotel_id(current_user)
    hotel = await settings_owner(session, h_id)
    mode = _resolve_scan(hotel.settings if hotel else None)["mode"]
    return await intake.run_scan(images, document_type, mode)


# ------------------------------------------- hujjat suratlari (MinIO) --

#: Hujjat suratini yuklash: mehmonni ro'yxatga oladigan yoki bron ochadigan
#: xodim (skaner shu oynalarda ishlaydi)
DOCUMENT_IMAGE_UPLOAD_CODES = (
    "guest.create",
    "guest.update",
    "reservation.create",
    "reservation.update",
    "file.upload",
)
#: Ko'rish: mehmon kartasini ko'ra oladigan xodim. Surat shaxsiy ma'lumot —
#: farrosh/usta ko'rmaydi.
DOCUMENT_IMAGE_VIEW_CODES = (
    "guest.view",
    "guest.create",
    "guest.update",
    "reservation.read",
    "reservation.create",
    "reservation.update",
)


def _document_images_hotel(current_user: dict, codes: tuple[str, ...]) -> UUID:
    """Surat har doim MEHMONXONAGA tegishli (mehmonlar bazasi global, lekin
    surat uni olgan mehmonxonadan tashqariga chiqmaydi)."""
    if current_user["user_type"] not in ("ADMIN", "SUPER_ADMIN"):
        granted = current_user.get("permissions") or []
        if not any(code in granted for code in codes):
            raise ForbiddenException(
                "Hujjat suratlari bilan ishlashga ruxsat yo'q", "DOCUMENT_IMAGES_FORBIDDEN"
            )
    h_id = current_user.get("hotel_id")
    if not h_id:
        raise ForbiddenException("Hotel context required")
    return h_id


async def _live_guest(session: AsyncSession, guest_id: UUID) -> Guest:
    guest = await session.get(Guest, guest_id)
    if guest is None or getattr(guest, "is_deleted", False):
        raise NotFoundException("Guest not found", "GUEST_NOT_FOUND")
    return guest


async def _uploader_names(session: AsyncSession, ids: set) -> dict:
    from app.infrastructure.database.models.user import User

    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await session.execute(select(User).where(User.id.in_(list(ids))))).scalars().all()
    return {
        u.id: (" ".join(p for p in (u.first_name, u.last_name) if p) or None) for u in rows
    }


@router.post("/{guest_id}/document-images")
async def upload_document_images(
    guest_id: UUID = Path(),
    document_type: Literal["ID_CARD", "PASSPORT"] | None = Form(default=None),
    passport: UploadFile | None = File(default=None),
    front: UploadFile | None = File(default=None),
    back: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Vebda skanerlangan hujjat suratini mehmon kartasiga saqlaydi.

    Veb skaneri natijani mehmon hali yo'q paytda beradi — surat mehmon
    yaratilgach yoki tanlangach shu yerga yuklanadi. Sozlamada saqlash
    o'chirilgan bo'lsa hech narsa yozilmaydi (`disabled: true`).
    """
    h_id = _document_images_hotel(current_user, DOCUMENT_IMAGE_UPLOAD_CODES)
    await _live_guest(session, guest_id)
    hotel = await settings_owner(session, h_id)
    if not document_images.store_enabled(hotel.settings if hotel else None):
        return {"stored": [], "disabled": True}

    images: dict[str, bytes] = {}
    for side, upload, label in (
        ("passport", passport, "Passport"),
        ("front", front, "Old tomon"),
        ("back", back, "Orqa tomon"),
    ):
        data = await intake.read_image(upload, label)
        if not data:
            continue
        if document_images.sniff_image(data) is None:
            raise ValidationException(
                f"{label}: faqat JPEG, PNG yoki WEBP rasm", "DOCUMENT_IMAGE_INVALID"
            )
        images[side] = data
    if not images:
        raise ValidationException("Hujjat surati yuborilmadi", "DOCUMENT_IMAGE_REQUIRED")

    stored = await document_images.store_images(
        session,
        hotel_id=h_id,
        images=images,
        entity_type=document_images.ENTITY_GUEST,
        entity_id=guest_id,
        uploaded_by=current_user["id"],
        document_type=document_type,
    )
    if not stored:
        raise document_images.DocumentStorageUnavailable()
    return {
        "stored": [document_images.attachment_dict(att) for att in stored],
        "disabled": False,
    }


@router.get("/{guest_id}/document-images")
async def list_document_images(
    guest_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mehmonning hujjat suratlari (skaner suratlari va qo'lda yuklangan
    "pasport surati"), eng yangisi birinchi. Rasmning o'zi
    `GET /guests/{id}/document-images/{file_id}` dan olinadi."""
    h_id = _document_images_hotel(current_user, DOCUMENT_IMAGE_VIEW_CODES)
    rows = await document_images.attachments_of(
        session,
        hotel_id=h_id,
        entity_type=document_images.ENTITY_GUEST,
        entity_id=guest_id,
        categories=document_images.GUEST_IMAGE_CATEGORIES,
    )
    names = await _uploader_names(session, {row.uploaded_by for row in rows})
    return [
        document_images.attachment_dict(row, names.get(row.uploaded_by)) for row in rows
    ]


@router.get("/{guest_id}/document-images/{file_id}")
async def get_document_image(
    guest_id: UUID = Path(),
    file_id: UUID = Path(),
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Suratning o'zi — server orqali (MinIO havolasi HTTPS sahifada
    ochilmaydi va tashqariga ko'rinmasligi kerak)."""
    from app.infrastructure.database.models.file_attachment import FileAttachment
    from app.infrastructure.storage import minio as storage

    h_id = _document_images_hotel(current_user, DOCUMENT_IMAGE_VIEW_CODES)
    attachment = (
        await session.execute(
            select(FileAttachment).where(
                FileAttachment.id == file_id,
                FileAttachment.hotel_id == h_id,
                FileAttachment.entity_type == document_images.ENTITY_GUEST,
                FileAttachment.entity_id == guest_id,
                FileAttachment.is_deleted.is_(False),
                FileAttachment.category.in_(document_images.GUEST_IMAGE_CATEGORIES),
            )
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFoundException("File not found", "FILE_NOT_FOUND")
    try:
        data = await storage.download_file(attachment.minio_bucket, attachment.minio_path)
    except Exception:  # noqa: BLE001
        logger.exception("Hujjat suratini o'qib bo'lmadi: %s", attachment.minio_path)
        raise NotFoundException("File not found", "FILE_NOT_FOUND")
    return Response(
        content=data,
        media_type=attachment.mime_type,
        # Shaxsiy ma'lumot: brauzer/proksi keshida qolmasin
        headers={"Cache-Control": "private, no-store"},
    )


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
    from app.application.services.blacklist_service import (
        DEFAULT_BLOCK_BOOKING,
        resolve_block_booking,
    )

    h_id = (
        current_user.get("hotel_id")
        if current_user["user_type"] == "SUPER_ADMIN"
        else _get_hotel_id(current_user)
    )
    hotel = await settings_owner(session, h_id)
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
    from app.application.services.blacklist_service import (
        BLACKLIST_SETTINGS_KEY,
        DEFAULT_BLOCK_BOOKING,
    )

    assert_can_manage_settings(current_user)
    h_id = _get_hotel_id(current_user)
    hotel = await settings_owner(session, h_id)
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
